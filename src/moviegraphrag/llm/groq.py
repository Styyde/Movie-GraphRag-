import json
import time
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from moviegraphrag.llm.prompts import ANALYZER, ASSESSOR, GENERATOR
from moviegraphrag.examples import QUESTIONS, reference_plan
from moviegraphrag.ingestion.dataset import normalize
from moviegraphrag.schemas.models import AssessmentBatch, GeneratedAnswer, QuestionDraft


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    models: set[str] = field(default_factory=set)

    def report(self, settings):
        cost = None
        if settings.llm_input_usd_per_million is not None and settings.llm_output_usd_per_million is not None:
            cost = (self.prompt_tokens * settings.llm_input_usd_per_million +
                    self.completion_tokens * settings.llm_output_usd_per_million) / 1_000_000
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "models": sorted(self.models),
                "estimated_cost_usd": cost}


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

    def structured(self, schema, system, payload, usage: Usage, output_schema=None):
        if not self.settings.groq_api_key.get_secret_value():
            raise RuntimeError("GROQ_API_KEY is not configured")
        request = {"model": self.settings.groq_model, "temperature": 0,
                   "max_tokens": 10000,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": schema.__name__, "schema": strict_schema(output_schema or schema.model_json_schema()), "strict": True}}}
        for attempt in range(3):
            usage.calls += 1
            try:
                response = self.client.post("/chat/completions", json=request, headers={
                    "Authorization": "Bearer " + self.settings.groq_api_key.get_secret_value()})
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            response.raise_for_status()
            result = response.json()
            usage.models.add(result.get("model", self.settings.groq_model))
            counts = result.get("usage", {})
            usage.prompt_tokens += counts.get("prompt_tokens", 0)
            usage.completion_tokens += counts.get("completion_tokens", 0)
            return schema.model_validate_json(result["choices"][0]["message"]["content"])
        raise RuntimeError("LLM request exhausted retries")

    def analyze(self, question, usage):
        payload = {"question": question}
        system = ANALYZER + "\nMANDATORY: variables MUST declare ALL edge endpoints and the target. " \
            "Each mentioned entity must be bound using mention_ref. Never return an empty variables array.\n" \
            + "\n".join("Worked example question: " + QUESTIONS[k] + "\nValid plan: " +
                        reference_plan(k).model_dump_json() for k in ("Q1", "Q4", "Q9"))
        for attempt in range(2):
            try:
                plan = self.structured(QuestionDraft, system, payload, usage)
                original = normalize(question)
                for mention in plan.mentions:
                    if normalize(mention.text) not in original:
                        raise ValueError("Entity mentions must be literal question excerpts; never expand names or translate genres.")
                    if mention.year is not None and str(mention.year) not in question:
                        raise ValueError("An identifying year must be explicitly present in the question.")
                return plan
            except (ValidationError, ValueError) as exc:
                if attempt:
                    return QuestionDraft(supported=False, unsupported_reason=
                                         "La question n'a pas pu être représentée fidèlement après validation.")
                payload = {"question": question, "validation_errors": str(exc)[:4000],
                           "instruction": "Correct the plan once; preserve exact mentions; reject unsupported requests."}

    def assess(self, theme, movies, usage):
        output = AssessmentBatch.model_json_schema()
        output["properties"]["films"].update(minItems=len(movies), maxItems=len(movies))
        output["$defs"]["FilmAssessment"]["properties"]["movie_id"]["enum"] = [m["movie_id"] for m in movies]
        output["$defs"]["FilmAssessment"]["properties"]["judgments"].update(
            minItems=len(theme.themes), maxItems=len(theme.themes))
        output["$defs"]["ThemeJudgment"]["properties"]["theme"]["enum"] = theme.themes
        return self.structured(AssessmentBatch, ASSESSOR,
                               {"request": theme.model_dump(), "movies": movies}, usage, output)

    def generate(self, context, usage):
        return self.structured(GeneratedAnswer, GENERATOR, context, usage)
