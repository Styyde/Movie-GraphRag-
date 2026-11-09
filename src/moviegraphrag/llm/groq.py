import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from moviegraphrag.llm.prompts import ANALYZER, GENERATOR
from moviegraphrag.examples import QUESTIONS, reference_plan
from moviegraphrag.ingestion.dataset import normalize
from moviegraphrag.quantities import asks_count, requires_centrality, validate_quantity
from moviegraphrag.schemas.models import GeneratedAnswer, QuestionDraft

logger = logging.getLogger(__name__)


class ProviderOutputError(ValueError):
    """The provider rejected its generated JSON, before application validation."""


def retry_after_seconds(value):
    """Honor Retry-After seconds or HTTP dates; invalid headers use normal backoff."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    models: set[str] = field(default_factory=set)
    stages: dict[str, int] = field(default_factory=dict)
    formats: dict[str, int] = field(default_factory=dict)

    def report(self, settings):
        cost = None
        if settings.llm_input_usd_per_million is not None and settings.llm_output_usd_per_million is not None:
            cost = (self.prompt_tokens * settings.llm_input_usd_per_million +
                    self.completion_tokens * settings.llm_output_usd_per_million) / 1_000_000
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "models": sorted(self.models),
                "estimated_cost_usd": cost, "stages": self.stages, "formats": self.formats}


def strict_schema(schema):
    """Groq strict mode requires every property to be required, including nullable ones."""
    if isinstance(schema, dict):
        result = {k: strict_schema(v) for k, v in schema.items() if k != "default"}
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        if "prefixItems" in result:
            # The only tuple is a pair of movie-variable refs; local validation enforces length.
            result["items"] = result.pop("prefixItems")[0]
        if "anyOf" in result and any(branch.get("type") == "number" for branch in result["anyOf"]):
            # JSON Schema number already includes integers; Groq rejects the overlapping union.
            result["anyOf"] = [branch for branch in result["anyOf"] if branch.get("type") != "integer"]
        return result
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema


class GroqLLM:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.Client(base_url="https://api.groq.com/openai/v1", timeout=60)

    def close(self):
        self.client.close()

    def structured(self, schema, system, payload, usage: Usage, output_schema=None, json_object=False):
        if not self.settings.groq_api_key.get_secret_value():
            raise RuntimeError("GROQ_API_KEY is not configured")
        request = {"model": self.settings.groq_model, "temperature": 0,
                   "max_tokens": 10000,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": schema.__name__, "schema": strict_schema(output_schema or schema.model_json_schema()), "strict": True}}}
        if json_object:
            request["response_format"] = {"type": "json_object"}
            request["messages"][0]["content"] += ("\nReturn ONLY valid JSON matching this schema: "
                + json.dumps(strict_schema(schema.model_json_schema()), ensure_ascii=False)
                + "\nEmpty arrays MUST be [], never null. Null is allowed only for nullable fields.")
        retry_deadline = time.monotonic() + 60
        for attempt in range(3):
            usage.calls += 1
            usage.stages[schema.__name__] = usage.stages.get(schema.__name__, 0) + 1
            mode = request["response_format"]["type"]
            usage.formats[mode] = usage.formats.get(mode, 0) + 1
            try:
                response = self.client.post("/chat/completions", json=request, headers={
                    "Authorization": "Bearer " + self.settings.groq_api_key.get_secret_value()})
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.is_error:
                try:
                    error = response.json().get("error", {})
                except (ValueError, AttributeError):
                    error = {}
                if not isinstance(error, dict):
                    error = {}
                secret = self.settings.groq_api_key.get_secret_value()
                def safe(value):
                    return str(value).replace(secret, "[redacted]").replace("\n", " ").replace("\r", " ")[:1600]
                logger.warning("Groq request failed status=%s schema=%s code=%s message=%s retry_after=%s",
                               response.status_code, schema.__name__, safe(error.get("code", "unknown")),
                               safe(error.get("message", "No provider error message")),
                               safe(response.headers.get("retry-after", "unspecified")))
                if response.status_code == 400 and error.get("code") == "json_validate_failed":
                    # Treat provider-side malformed generation like local schema validation.
                    # The analyzer gets its single correction; synthesis uses its fallback.
                    failed = error.get("failed_generation")
                    if isinstance(failed, str):
                        try:
                            schema.model_validate_json(failed)
                        except ValidationError as exc:
                            details = exc.errors(include_input=False, include_context=False)
                            raise ProviderOutputError(json.dumps(details)[:3000]) from exc
                    raise ProviderOutputError("Provider could not produce a valid structured response")
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    delay = retry_after_seconds(response.headers.get("retry-after"))
                    delay = delay if delay is not None else 2.0 ** attempt
                    if delay <= retry_deadline - time.monotonic():
                        time.sleep(delay)
                        continue
                    # A long daily/account limit must not become a premature retry.
                    logger.warning("Groq retry deferred: provider delay exceeds request retry budget")
            response.raise_for_status()
            result = response.json()
            usage.models.add(result.get("model", self.settings.groq_model))
            counts = result.get("usage", {})
            usage.prompt_tokens += counts.get("prompt_tokens", 0)
            usage.completion_tokens += counts.get("completion_tokens", 0)
            return schema.model_validate_json(result["choices"][0]["message"]["content"])
        raise RuntimeError("LLM request exhausted retries")

    def analyze(self, question, usage):
        if requires_centrality(question):
            return QuestionDraft(supported=False, unsupported_reason=
                "La centralité thématique n'est pas vérifiée. Reformulez comme une recherche de recommandations sur ce thème.")
        if asks_count(question) and re.search(r"\b(?:chaque|each|per director|par realisateur)\b", normalize(question)):
            return QuestionDraft(supported=False, unsupported_reason=
                "Les comptages par groupe ne sont pas pris en charge. Demandez un compte global ou précisez une personne.")
        payload = {"question": question}
        system = ANALYZER + "\nMANDATORY: variables MUST declare ALL edge endpoints and the target. " \
            "Each mentioned entity must be bound using mention_ref. Never return an empty variables array.\n" \
            + "\n".join("Worked example question: " + QUESTIONS[k] + "\nValid plan: " +
                        reference_plan(k).model_dump_json() for k in ("Q4", "Q9"))
        count_example = reference_plan("Q1").model_dump()
        count_example["operation"] = "count"
        system += ("\nWorked example question: Combien de films ont ete realises par Christopher Nolan "
                   "avec Cillian Murphy ?\nValid plan: " + json.dumps(count_example))
        pure_example = reference_plan("Q7").model_dump()
        pure_example.update(requested_limit=1, theme_request={
            "query": "films parlant des rêves", "themes": ["rêves"],
            "operator": "ALL", "requirement": "association"})
        system += ("\nWorked example question: Recommande un film sur les rêves.\nValid plan: "
                   + json.dumps(pure_example, ensure_ascii=False))
        for label, noun in (("Movie", "films"), ("Person", "personnes")):
            global_example = QuestionDraft(operation="count", variables=[{"ref": "result", "label": label}])
            system += (f"\nWorked example question: Combien de {noun} contient le dataset ?\nValid plan: "
                       + global_example.model_dump_json())
        json_fallback = False
        for attempt in range(2):
            try:
                plan = self.structured(QuestionDraft, system, payload, usage, json_object=json_fallback)
                if not plan.supported:
                    return plan
                if asks_count(question) and plan.operation != "count":
                    raise ValueError("A counting question requires operation=count, or unsupported for thematic counts")
                original = normalize(question)
                for mention in plan.mentions:
                    if normalize(mention.text) not in original:
                        raise ValueError("Entity mentions must be literal question excerpts; never expand names or translate genres.")
                    if mention.year is not None and str(mention.year) not in question:
                        raise ValueError("An identifying year must be explicitly present in the question.")
                if plan.operation == "count":
                    plan.requested_limit = None
                else:
                    plan.requested_limit = validate_quantity(question, plan.requested_limit)
                return plan
            except (ValidationError, ValueError) as exc:
                details = (json.dumps(exc.errors(include_input=False, include_context=False))
                           if isinstance(exc, ValidationError) else str(exc))
                logger.warning("Analyzer validation attempt=%s detail=%s", attempt + 1, details[:2000])
                if attempt:
                    if isinstance(exc, ProviderOutputError):
                        raise RuntimeError("Provider failed to generate JSON after the bounded correction") from exc
                    return QuestionDraft(supported=False, unsupported_reason=
                                         "La question n'a pas pu être représentée fidèlement après validation.")
                json_fallback = isinstance(exc, ProviderOutputError)
                payload = {"question": question, "validation_errors": str(exc)[:4000],
                           "instruction": "Correct the plan once; preserve exact mentions; reject unsupported requests."}

    def generate(self, context, usage):
        return self.structured(GeneratedAnswer, GENERATOR, context, usage)
