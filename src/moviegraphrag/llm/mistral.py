import json
import time
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from moviegraphrag.llm.prompts import ANALYZER, ASSESSOR, GENERATOR
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


class MistralLLM:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.Client(base_url="https://api.mistral.ai/v1", timeout=60)

    def close(self):
        self.client.close()

    def structured(self, schema, system, payload, usage: Usage):
        if not self.settings.mistral_api_key.get_secret_value():
            raise RuntimeError("MISTRAL_API_KEY is not configured")
        request = {"model": self.settings.mistral_model, "temperature": 0,
                   "max_tokens": 10000,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}}}
        for attempt in range(3):
            usage.calls += 1
            try:
                response = self.client.post("/chat/completions", json=request, headers={
                    "Authorization": "Bearer " + self.settings.mistral_api_key.get_secret_value()})
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
            usage.models.add(result.get("model", self.settings.mistral_model))
            counts = result.get("usage", {})
            usage.prompt_tokens += counts.get("prompt_tokens", 0)
            usage.completion_tokens += counts.get("completion_tokens", 0)
            return schema.model_validate_json(result["choices"][0]["message"]["content"])
        raise RuntimeError("LLM request exhausted retries")

    def analyze(self, question, usage):
        payload = {"question": question}
        try:
            return self.structured(QuestionDraft, ANALYZER, payload, usage)
        except ValidationError as exc:
            return self.structured(QuestionDraft, ANALYZER,
                                   {**payload, "validation_errors": str(exc)[:4000],
                                    "instruction": "Correct the plan once; reject unsupported requests."}, usage)

    def assess(self, theme, movies, usage):
        return self.structured(AssessmentBatch, ASSESSOR,
                               {"request": theme.model_dump(), "movies": movies}, usage)

    def generate(self, context, usage):
        return self.structured(GeneratedAnswer, GENERATOR, context, usage)
