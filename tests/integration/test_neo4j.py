import os

import pytest

from moviegraphrag.config import Settings
from moviegraphrag.examples import reference_plan
from moviegraphrag.ingestion.loader import ingest
from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.retrieval.neo4j_store import Neo4jStore

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store():
    if os.getenv("RUN_NEO4J_TESTS") != "1":
        pytest.skip("Set RUN_NEO4J_TESTS=1 for a dedicated local Neo4j test instance")
    cfg = Settings(_env_file=None)
    result = Neo4jStore(cfg)
    yield result
    result.close()


@pytest.mark.parametrize("key,count", [("Q1", 4), ("Q2", 17), ("Q3", 509), ("Q4", 2),
                                       ("Q8", 1), ("Q9", 23), ("Q10", 254)])
def test_real_cypher_acceptance(store, key, count):
    plan = reference_plan(key)
    links = EntityLinker(store).link(plan)
    assert all(r.status == "resolved" for r in links)
    bindings = {r.mention.ref: r.canonical_id for r in links}
    rows, _ = store.graph_candidates(plan, bindings)
    assert len(rows) == count
    witness = store.witnesses(plan, bindings, rows[0]["id"])
    assert len(witness["edges"]) == len(plan.edges)


def test_exact_scoring_and_native_vector_index(store):
    query = store.read("MATCH (m:Movie) WHERE m.embedding IS NOT NULL RETURN m.movie_id AS id, "
                       "m.embedding AS embedding LIMIT 1")[0]
    exact = store.exact_scores([query["id"]], query["embedding"])
    assert exact[0]["score"] == pytest.approx(1)
    assert store.vector_candidates(query["embedding"], 5)


def test_idempotent_full_ingestion(store, dataset):
    # Reuse existing real vectors: this verifies writes, snapshot checks, aggregation and counts,
    # without downloading or substituting a fake model in the database.
    from moviegraphrag.embeddings import LocalEmbedder
    embedder = LocalEmbedder(store.settings.embedding_model, store.settings.embedding_revision)
    manifest = ingest(dataset, store, embedder)
    assert manifest["status"] == "ready"
    assert manifest["counts"] == dataset.manifest["counts"]
