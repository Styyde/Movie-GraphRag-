"""Evaluation contracts checked against the real CSVs, Neo4j snapshot and HTTP API."""
import copy
import json
from pathlib import Path

import pytest

from moviegraphrag.evaluation import (
    graph_set_metrics,
    graph_suite_reference,
    preserve_annotations,
    validate_cases,
)
from moviegraphrag.schemas.models import AnswerResponse


@pytest.fixture(scope="module")
def cases(store):
    records = [json.loads(line) for line in Path("evaluation/cases.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    return validate_cases(records, store.manifest()["snapshot_id"])


def test_raw_csv_gold_includes_held_out_graph_questions(store):
    references = graph_suite_reference(Path("data/raw/tmdb_5000_movies.csv"),
                                       Path("data/raw/tmdb_5000_credits.csv"))
    assert len(references) == 20
    assert all(c["snapshot_id"] == store.manifest()["snapshot_id"] for c in references.values())
    assert set(references["graph-01"]["relevance"]) == {"155", "272", "27205", "49026"}
    assert len(references["graph-02"]["relevance"]) == 17
    assert len(references["graph-03"]["relevance"]) == 509
    assert sum(c["judgment_status"] == "csv_verified" for c in references.values()) >= 18
    assert references["graph-06"]["judgment_status"] == "csv_verified"


def test_annotations_survive_regeneration(cases, store):
    # Retain verified real CSV answers and reviewer notes without changing the sources.
    reviewed = copy.deepcopy(cases)
    reviewed[0]["judgment_status"] = "human_reviewed"
    reviewed[0]["annotation_notes"] = "retain this reviewed reference"
    regenerated = preserve_annotations(copy.deepcopy(cases), reviewed)
    assert regenerated[0] == reviewed[0]
    validate_cases(regenerated, store.manifest()["snapshot_id"])


def test_invalid_evaluation_metadata_is_rejected(cases, store):
    snapshot = store.manifest()["snapshot_id"]
    with pytest.raises(ValueError, match="duplicate case ID"):
        validate_cases(cases + [cases[0]], snapshot)
    leaked = copy.deepcopy(cases)
    held_out = next(case for case in leaked if case["split"] == "test")
    held_out["family"] = leaked[0]["family"]
    with pytest.raises(ValueError, match="crosses dev/test"):
        validate_cases(leaked, snapshot)
    stale = copy.deepcopy(cases)
    stale[0]["snapshot_id"] = "0" + snapshot[1:] if snapshot[0] != "0" else "1" + snapshot[1:]
    with pytest.raises(ValueError, match="snapshot differs"):
        validate_cases(stale, snapshot)
    unreviewed = copy.deepcopy(cases)
    unreviewed[0]["judgment_status"] = "pending_human_review"
    with pytest.raises(ValueError, match="Unreviewed relevance"):
        validate_cases(unreviewed, snapshot)


@pytest.mark.llm
def test_real_graph_complete_set_metric_and_http_pagination(api, store, cases, groq_configured):
    case = next(case for case in cases if case["id"] == "graph-02")
    first = AnswerResponse.model_validate(api.post("/questions", json={
        "question": case["question"], "limit": 5}).json())
    assert first.status == "ok", first
    complete = graph_set_metrics(store, first, case)
    assert complete["metric_scope"] == "complete_graph_retrieval"
    assert complete["exact_set_match"]
    assert complete["first_page_consistent"]
    assert complete["actual_count"] == 17
    second = AnswerResponse.model_validate(api.post("/questions", json={
        "question": case["question"], "limit": 20, "offset": 5,
        "clarification_token": first.clarification_token}).json())
    assert second.status == "ok", second
    ids = [row["id"] for row in first.results + second.results]
    assert len(ids) == len(set(ids)) == 17
    assert set(ids) == {int(entity_id) for entity_id in case["relevance"]}
    assert not second.coverage.has_more
    assert second.trace["llm_usage"]["calls"] == 0
