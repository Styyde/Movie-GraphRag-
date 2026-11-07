import json

import pytest

from moviegraphrag.evaluation import graph_reference
from moviegraphrag.examples import reference_plan
from moviegraphrag.ingestion.loader import ingest
from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.schemas.models import Label


def test_csv_counts_and_credits(dataset):
    assert dataset.manifest["counts"] == {"movies": 4803, "people": 56603, "genres": 20,
                                          "acted_in": 106084, "directed": 5166, "in_genre": 12160}
    assert dataset.report["ambiguous_names"] == 405
    assert sum(len(json.loads(r["credits_json"])) for r in dataset.acted_in) == 106257


@pytest.mark.parametrize("key", ["Q1", "Q2", "Q3", "Q4", "Q8", "Q9", "Q10"])
def test_cypher_matches_independent_csv_truth(store, dataset, key):
    plan = reference_plan(key)
    links = EntityLinker(store).link(plan)
    assert all(r.status == "resolved" for r in links)
    bindings = {r.mention.ref: r.canonical_id for r in links}
    rows, _ = store.graph_candidates(plan, bindings)
    assert {r["id"] for r in rows} == set(graph_reference(dataset)[key])
    witness = store.witnesses(plan, bindings, rows[0]["id"])
    assert len(witness["edges"]) == len(plan.edges)


def test_real_homonyms_and_fuzzy_search(store):
    candidates = store.find_entities("Tom Holland", Label.PERSON)
    assert len(candidates) == 2
    assert {c.id for c in candidates} == {1136406, 64796}
    assert store.find_entities("Christopher Nola", Label.PERSON)


def test_native_vector_search_and_exact_scoring(store, embedder):
    vector = embedder.encode_query("films explorant les rêves et le subconscient")
    candidates = store.vector_candidates(vector, 30)
    assert candidates and all(0 <= row["score"] <= 1 for row in candidates)
    ids = [r["id"] for r in candidates]
    scores = store.exact_scores(ids, vector)
    assert len(scores) == len(ids) == 30
    assert {r["id"] for r in scores} == set(ids)


def test_hybrid_scores_all_structural_candidates(store, embedder):
    plan = reference_plan("Q9")
    links = EntityLinker(store).link(plan)
    rows, _ = store.graph_candidates(plan, {r.mention.ref: r.canonical_id for r in links})
    assert len(rows) == 23
    ranked = store.exact_scores([r["id"] for r in rows], embedder.encode_query(plan.theme_request.query))
    assert len(ranked) == 23


def test_idempotent_real_ingestion(store, dataset, embedder):
    result = ingest(dataset, store, embedder)
    assert result["counts"] == dataset.manifest["counts"]
    assert result["status"] == "ready"
    assert result["embedded_documents"] == 4801
    assert result["reused_embeddings"] == 4801
