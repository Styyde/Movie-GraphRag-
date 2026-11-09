"""Real E5/Neo4j rankings, without simulated models or thematic LLM assessment."""
import json
import time
from pathlib import Path

import pytest

from moviegraphrag.examples import reference_plan
from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.llm.groq import GroqLLM
from moviegraphrag.orchestration import MovieGraphRAG
from moviegraphrag.schemas.models import QuestionDraft, QuestionRequest


@pytest.fixture
def ranked_pipeline(store, embedder, settings):
    local_settings = settings.model_copy(update={"synthesis_enabled": False})
    llm = GroqLLM(local_settings)
    try:
        yield MovieGraphRAG(store, embedder, llm, local_settings)
    finally:
        llm.close()


def ask_plan(service, plan, **request):
    question = "Recherche sémantique de référence"
    links = EntityLinker(service.store).link(plan)
    token = service.clarifications.issue(question, plan, links)
    return service.ask(QuestionRequest(question=question, clarification_token=token, **request))


def test_all_4801_vectors_are_scored_and_sorted_with_tie_breaker(store, embedder):
    vector = embedder.encode_query("films parlant d'intelligence artificielle")
    started = time.perf_counter()
    scores = store.exact_scores(None, vector)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    assert len(scores) == store.manifest()["embedded_documents"] == 4801
    assert len({row["id"] for row in scores}) == 4801
    assert scores == sorted(scores, key=lambda row: (-row["score"], row["id"]))
    assert all(0 <= row["score"] <= 1 for row in scores)
    assert store.exact_scores([], vector) == []
    Path("data/reports/architecture-scoring.json").write_text(json.dumps({
        "snapshot_id": store.manifest()["snapshot_id"], "documents_scored": len(scores),
        "scoring_ms": elapsed_ms, "samples": 1,
        "scope": "Neo4j exact cosine, ordering and row retrieval; excludes E5 query encoding and LLM calls",
    }, indent=2), encoding="utf-8")


def test_pure_recommendation_returns_exact_top_without_assessment(ranked_pipeline):
    plan = reference_plan("Q6")
    response = ask_plan(ranked_pipeline, plan)
    expected = ranked_pipeline.store.exact_scores(None, ranked_pipeline.embedder.encode_query(plan.theme_request.query))
    assert response.status == "ok", response
    assert response.operation == "recommend" and response.strategy == "VECTOR"
    assert [r["id"] for r in response.results] == [r["id"] for r in expected[:5]]
    assert response.coverage.scored == 4801
    assert response.coverage.without_embedding == 2
    assert not response.coverage.approximate
    assert response.thematic_verification == "not_performed"
    assert response.trace["llm_usage"]["calls"] == 0
    assert not hasattr(ranked_pipeline.llm, "assess")
    assert all(e.kind == "document" for e in response.evidence)
    assert all("judgments" not in r for r in response.results)


def test_hybrid_scores_all_254_candidates_and_keeps_witnesses(ranked_pipeline):
    response = ask_plan(ranked_pipeline, reference_plan("Q10"), limit=5)
    assert response.status == "ok", response
    assert response.coverage.total_candidates == response.coverage.scored == 254
    assert response.coverage.sources_checked == response.coverage.returned == 5
    assert response.coverage.ranking_coverage == "complete"
    evidence = {e.id: e for e in response.evidence}
    for row in response.results:
        assert {evidence[ref].kind for ref in row["evidence_ids"]} == {"document", "relation"}
        assert evidence[f"d{row['id']}"].data["snapshot_id"] == response.trace["snapshot_id"]


@pytest.mark.parametrize("quantity,interface,expected", [(None, 5, 5), (None, 10, 10),
                                                       (1, 10, 1), (3, 10, 3), (50, 5, 20)])
def test_explicit_quantity_precedes_interface_and_is_capped(ranked_pipeline, quantity, interface, expected):
    data = {**reference_plan("Q6").model_dump(), "requested_limit": quantity}
    response = ask_plan(ranked_pipeline, QuestionDraft.model_validate(data), limit=interface)
    assert response.status == "ok", response
    assert len(response.results) == response.coverage.effective_limit == expected
    assert (any("20 résultats" in w for w in response.warnings)) == (quantity == 50)


def test_single_hybrid_candidate_is_not_padded(ranked_pipeline):
    response = ask_plan(ranked_pipeline, reference_plan("Q8"), limit=20)
    assert response.status == "ok" and [r["id"] for r in response.results] == [27205]
    assert response.coverage.scored == 1


def test_context_limit_keeps_complete_structured_sources_and_skips_generation(ranked_pipeline):
    ranked_pipeline.settings = ranked_pipeline.settings.model_copy(update={
        "synthesis_enabled": True, "context_char_budget": 2000})
    response = ask_plan(ranked_pipeline, reference_plan("Q10"), limit=20)
    assert response.status == "ok", response
    assert len(response.results) == 20
    assert response.trace["context_truncated"]
    assert response.trace["generation_mode"] == "deterministic"
    assert response.trace["llm_usage"]["calls"] == 0
    assert len(response.evidence) == 40
