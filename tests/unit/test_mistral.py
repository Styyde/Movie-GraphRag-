import json

import httpx

from moviegraphrag.config import Settings
from moviegraphrag.examples import reference_plan
from moviegraphrag.llm.mistral import MistralLLM, Usage
from moviegraphrag.schemas.models import QuestionDraft


def test_provider_uses_json_schema_and_records_usage():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "mistral-small-versioned",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "choices": [{"message": {"content": reference_plan("Q1").model_dump_json()}}]})
    cfg = Settings(_env_file=None, mistral_api_key="test-secret")
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.mistral.ai/v1")
    llm = MistralLLM(cfg, client)
    usage = Usage()
    result = llm.analyze("Nolan et Murphy", usage)
    assert isinstance(result, QuestionDraft)
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["temperature"] == 0
    assert usage.calls == 1 and usage.prompt_tokens == 100
    assert usage.report(cfg)["estimated_cost_usd"] is None
    assert "test-secret" not in json.dumps(usage.report(cfg))


def test_invalid_plan_has_only_one_correction():
    calls = []
    def handler(request):
        calls.append(request)
        content = '{"cypher":"delete"}' if len(calls) == 1 else reference_plan("Q1").model_dump_json()
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    cfg = Settings(_env_file=None, mistral_api_key="test")
    llm = MistralLLM(cfg, httpx.Client(transport=httpx.MockTransport(handler), base_url="https://test/v1"))
    assert llm.analyze("Nolan", Usage()).target == "result"
    assert len(calls) == 2


def test_missing_key_fails_before_network():
    import pytest
    cfg = Settings(_env_file=None)
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
        MistralLLM(cfg).analyze("Nolan", Usage())
