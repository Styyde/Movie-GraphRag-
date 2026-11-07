from fastapi.testclient import TestClient

from moviegraphrag.api.app import create_app
from moviegraphrag.orchestration import MovieGraphRAG
from test_orchestration import FakeEmbedder, FakeLLM, FakeStore


def test_api_health_validation_and_question(settings):
    service = MovieGraphRAG(FakeStore(), FakeEmbedder(), FakeLLM(), settings)
    with TestClient(create_app(service)) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/questions", json={"question": "x", "cypher": "DELETE"}).status_code == 422
        response = client.post("/questions", json={"question": "films de Nolan et rêves"})
        assert response.status_code == 200
        assert response.json()["strategy"] == "HYBRID_GRAPH_FIRST"
        assert response.json()["status"] == "ok"
        assert client.get("/movies/0").status_code == 422
