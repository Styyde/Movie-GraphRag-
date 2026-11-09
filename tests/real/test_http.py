import pytest
import time


def test_real_http_health_movie_and_validation(api):
    assert api.get("/health").json()["status"] == "ok"
    assert api.get("/movies/27205").json()["title"] == "Inception"
    assert api.get("/movies/0").status_code == 422
    assert api.post("/questions", json={"question": "x", "cypher": "DELETE"}).status_code == 422


@pytest.mark.llm
def test_real_api_becomes_ready_without_a_semantic_question(api, groq_configured):
    deadline = time.monotonic() + 120
    while True:
        response = api.get("/ready")
        if response.status_code == 200:
            assert response.json()["embedding_loaded"]
            assert response.json()["llm_configured"]
            return
        assert time.monotonic() < deadline, response.json()
        time.sleep(2)


@pytest.mark.llm
@pytest.mark.parametrize("question,expected", [
    ("Quels films ont été réalisés par Christopher Nolan avec Cillian Murphy ?", {272, 155, 49026, 27205}),
    ("Quels réalisateurs ont travaillé avec Leonardo DiCaprio et Cillian Murphy ?", {525, 2034}),
])
def test_real_groq_analyzer_and_graph_api(api, groq_configured, question, expected):
    result = api.post("/questions", json={"question": question, "limit": 20}).json()
    assert result["status"] == "ok", result
    assert {r["id"] for r in result["results"]} == expected
    assert result["trace"]["llm_usage"]["calls"] >= 1
    assert result["trace"]["llm_usage"]["models"]


@pytest.mark.llm
def test_real_groq_hybrid_and_citations(api, groq_configured):
    result = api.post("/questions", json={
        "question": "Quels films de Nolan avec DiCaprio parlent des rêves ?"}).json()
    assert result["status"] == "ok", result
    assert result["strategy"] == "HYBRID_GRAPH_FIRST"
    assert {r["id"] for r in result["results"]} == {27205}
    evidence = {e["id"]: e for e in result["evidence"]}
    for row in result["results"]:
        assert set(row["evidence_ids"]) <= set(evidence)
        assert any(evidence[i]["kind"] == "relation" for i in row["evidence_ids"])
        assert any(evidence[i]["kind"] == "document" for i in row["evidence_ids"])
        assert "judgments" not in row
    assert result["thematic_verification"] == "not_performed"
    assert set(result["trace"]["llm_usage"]["stages"]) <= {"QuestionDraft", "GeneratedAnswer"}


@pytest.mark.llm
@pytest.mark.parametrize("question,expected", [
    ("Combien de films ont été réalisés par Christopher Nolan avec Cillian Murphy ?", 4),
    ("Quel est le nombre d'acteurs ayant joué dans des films réalisés par Christopher Nolan ?", 509),
    ("Combien de films ont été réalisés par Leonardo DiCaprio ?", 0),
])
def test_real_groq_exact_counts(api, groq_configured, question, expected):
    result = api.post("/questions", json={"question": question, "limit": 1}).json()
    assert result["status"] == "ok", result
    assert result["operation"] == "count" and result["count"] == expected
    assert not result["results"]
    assert result["trace"]["generation_mode"] == "deterministic"
    assert set(result["trace"]["llm_usage"]["stages"]) == {"QuestionDraft"}


@pytest.mark.llm
@pytest.mark.parametrize("question,expected", [("Combien de films contient le dataset ?", 4803),
                                             ("Combien de personnes contient le dataset ?", 56603)])
def test_real_groq_global_counts(api, groq_configured, question, expected):
    result = api.post("/questions", json={"question": question}).json()
    assert result["status"] == "ok", result
    assert result["operation"] == "count" and result["count"] == expected


@pytest.mark.llm
def test_witness_article_is_not_a_requested_result_quantity(api, groq_configured):
    result = api.post("/questions", json={
        "question": "Quels acteurs ont joué dans un film réalisé par Christopher Nolan ?", "limit": 5}).json()
    assert result["status"] == "ok", result
    assert result["operation"] == "list" and result["coverage"]["effective_limit"] == 5
    assert result["coverage"]["total_candidates"] == 509 and len(result["results"]) == 5
    assert result["trace"]["plan"]["requested_limit"] is None


@pytest.mark.llm
@pytest.mark.parametrize("question,interface,expected", [
    ("Recommande trois films sur l'intelligence artificielle.", 10, 3),
    ("Recommande un film sur les rêves.", 10, 1),
    ("Quels films parlent d'intelligence artificielle ?", 5, 5),
    ("Recommande 25 films sur l'intelligence artificielle.", 5, 20),
])
def test_real_groq_requested_quantities_and_exact_corpus_scoring(api, groq_configured, question, interface, expected):
    result = api.post("/questions", json={"question": question, "limit": interface}).json()
    assert result["status"] == "ok", result
    assert result["operation"] == "recommend" and len(result["results"]) == expected
    assert result["coverage"]["scored"] == 4801
    assert result["coverage"]["effective_limit"] == expected
    assert not result["coverage"]["approximate"]
    assert result["thematic_verification"] == "not_performed"


@pytest.mark.llm
@pytest.mark.parametrize("question", ["Combien de films parlent d'intelligence artificielle ?",
                                     "Uniquement des films centrés sur l'intelligence artificielle."])
def test_real_api_requests_reformulation_for_unsupported_thematic_guarantees(api, groq_configured, question):
    result = api.post("/questions", json={"question": question}).json()
    assert result["status"] == "unsupported", result
    assert result["count"] is None and not result["results"]


@pytest.mark.llm
def test_real_absent_entity_never_falls_back_to_vector(api, groq_configured):
    result = api.post("/questions", json={
        "question": "Quels films ont été réalisés par UnePersonneInexistanteXYZ ?"}).json()
    assert result["status"] == "entity_not_found", result
    assert not result["results"]


def test_grouped_count_requests_reformulation_without_provider_call(api):
    result = api.post("/questions", json={
        "question": "Combien de films chaque réalisateur a-t-il tournés ?"}).json()
    assert result["status"] == "unsupported", result
    assert result["count"] is None
    assert result["trace"]["llm_usage"]["calls"] == 0
