from moviegraphrag.examples import reference_plan
from moviegraphrag.orchestration import MovieGraphRAG
from moviegraphrag.schemas.models import AssessmentBatch, EntityCandidate, QuestionRequest


class FakeStore:
    def __init__(self, count=30):
        self.count = count
        self.scored_ids = []
        self.vector_called = False

    def readiness(self):
        return {"ready": True, "manifest": {"snapshot_id": "fixture", "movies_sha256": "m",
                "credits_sha256": "c", "counts": {"movies": self.count},
                "embedded_documents": self.count,
                "embedding": {"model": "test", "revision": "v1", "dimensions": 384}}}

    def find_entities(self, text, label):
        return [EntityCandidate(id=1, name=text, type=label, method="exact", roles=["actor", "director"])]

    def graph_candidates(self, plan, bindings):
        return [{"id": i, "name": f"Film {i}"} for i in range(1, self.count + 1)], "fixture"

    def exact_scores(self, ids, vector):
        self.scored_ids = ids
        return [{"id": i, "name": f"Film {i}", "score": 1 - i / 1000} for i in ids]

    def vector_candidates(self, vector, k):
        self.vector_called = True
        return self.exact_scores(list(range(1, self.count + 1)), vector)[:k]

    def movie(self, movie_id):
        return {"movie_id": movie_id, "title": f"Film {movie_id}", "overview": "A dream adventure.",
                "keywords": ["dream"], "keyword_ids": [1], "snapshot_id": "fixture"}

    def witnesses(self, plan, bindings, result_id):
        return {"nodes": [{"movie_id": result_id}], "edges": []}


class FakeEmbedder:
    model = object()

    def metadata(self):
        return {"model": "test", "revision": "v1", "dimensions": 384}

    def encode_query(self, query):
        return [1.0] + [0.0] * 383


class FakeLLM:
    def __init__(self, key="Q8", reject=False):
        self.key, self.reject = key, reject

    def analyze(self, question, usage):
        return reference_plan(self.key)

    def assess(self, theme, movies, usage):
        return AssessmentBatch(films=[{"movie_id": m["movie_id"], "judgments": [{
            "theme": t, "source": "none" if self.reject else "keyword", "centrality": "unknown",
            "keyword_ids": [] if self.reject else [1]} for t in theme.themes]} for m in movies])


def test_hybrid_scores_every_candidate_without_global_vector_search(settings):
    store = FakeStore(30)
    service = MovieGraphRAG(store, FakeEmbedder(), FakeLLM(), settings)
    result = service.ask(QuestionRequest(question="Nolan avec DiCaprio et des rêves"))
    assert result.status == "ok"
    assert store.scored_ids == list(range(1, 31))
    assert not store.vector_called
    assert result.coverage.scored == 30
    assert len(result.results) == 5
    assert result.coverage.semantic_coverage == "partial"
    assert all(any(e.startswith("g") for e in r["evidence_ids"]) for r in result.results)


def test_semantic_budget_cannot_claim_exhaustiveness(settings):
    store = FakeStore(120)
    result = MovieGraphRAG(store, FakeEmbedder(), FakeLLM(reject=True), settings).ask(
        QuestionRequest(question="Nolan avec DiCaprio et des rêves"))
    assert result.status == "insufficient_evidence"
    assert result.coverage.scored == 120
    assert result.coverage.examined == 100
    assert result.coverage.semantic_coverage == "partial"


def test_graph_pagination_uses_total_count(settings):
    result = MovieGraphRAG(FakeStore(509), FakeEmbedder(), FakeLLM("Q3"), settings).ask(
        QuestionRequest(question="acteurs de Nolan", limit=20, offset=20))
    assert result.coverage.total_candidates == 509
    assert result.coverage.returned == 20
    assert result.results[0]["id"] == 21
    assert result.coverage.has_more
