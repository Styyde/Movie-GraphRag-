import pytest


def test_real_http_health_movie_and_validation(api):
    assert api.get("/health").json()["status"] == "ok"
    assert api.get("/movies/27205").json()["title"] == "Inception"
    assert api.get("/movies/0").status_code == 422
    assert api.post("/questions", json={"question": "x", "cypher": "DELETE"}).status_code == 422


@pytest.mark.llm
@pytest.mark.parametrize("question,expected", [
    ("Quels films ont été réalisés par Christopher Nolan avec Cillian Murphy ?", {272, 155, 49026, 27205}),
    ("Quels réalisateurs ont travaillé avec Leonardo DiCaprio et Cillian Murphy ?", {525, 2034}),
])
def test_real_mistral_analyzer_and_graph_api(api, mistral_configured, question, expected):
    result = api.post("/questions", json={"question": question, "limit": 20}).json()
    assert result["status"] == "ok", result
    assert {r["id"] for r in result["results"]} == expected
    assert result["trace"]["llm_usage"]["calls"] >= 1
    assert result["trace"]["llm_usage"]["models"]


@pytest.mark.llm
def test_real_mistral_hybrid_and_citations(api, mistral_configured):
    result = api.post("/questions", json={
        "question": "Quels films de Nolan avec DiCaprio parlent des rêves ?"}).json()
    assert result["status"] == "ok", result
    assert result["strategy"] == "HYBRID_GRAPH_FIRST"
    assert {r["id"] for r in result["results"]} == {27205}
    evidence = {e["id"]: e for e in result["evidence"]}
    for row in result["results"]:
        assert set(row["evidence_ids"]) <= set(evidence)
        assert any(evidence[i]["kind"] == "relation" for i in row["evidence_ids"])
        for judgment in row["judgments"]:
            if judgment["source"] == "keyword":
                assert judgment["centrality"] == "unknown"


@pytest.mark.llm
def test_real_absent_entity_never_falls_back_to_vector(api, mistral_configured):
    result = api.post("/questions", json={
        "question": "Quels films ont été réalisés par UnePersonneInexistanteXYZ ?"}).json()
    assert result["status"] == "entity_not_found", result
    assert not result["results"]
