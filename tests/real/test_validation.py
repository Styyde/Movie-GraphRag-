"""Real validators with source records fetched from Neo4j; no provider substitution."""
import pytest
from pydantic import ValidationError

from moviegraphrag.evidence.validation import validate_assessments
from moviegraphrag.examples import reference_plan
from moviegraphrag.schemas.models import AssessmentBatch, QuestionDraft, ThemeRequest


def test_unknown_cypher_and_wrong_directions_are_rejected():
    raw = reference_plan("Q1").model_dump()
    raw["cypher"] = "MATCH (n) DELETE n"
    with pytest.raises(ValidationError):
        QuestionDraft.model_validate(raw)
    raw.pop("cypher")
    raw["edges"][0]["source"] = "result"
    raw["edges"][0]["target"] = "director"
    with pytest.raises(ValidationError):
        QuestionDraft.model_validate(raw)


def test_keyword_only_cannot_establish_centrality_in_real_movie(store):
    movie = store.movie(157336)
    keyword_id = next(k for k, name in zip(movie["keyword_ids"], movie["keywords"], strict=True)
                      if name == "artificial intelligence")
    request = ThemeRequest(query="IA", themes=["IA"], requirement="central")
    batch = AssessmentBatch(films=[{"movie_id": movie["movie_id"], "judgments": [{
        "theme": "IA", "source": "keyword", "centrality": "unknown", "keyword_ids": [keyword_id]}]}])
    assert validate_assessments(batch, [movie], request) == []
    batch.films[0].judgments[0].centrality = "central"
    with pytest.raises(ValueError, match="centrality"):
        validate_assessments(batch, [movie], request)


def test_hallucinated_overview_quote_rejected_against_real_source(store):
    movie = store.movie(27205)
    batch = AssessmentBatch(films=[{"movie_id": movie["movie_id"], "judgments": [{
        "theme": "dream", "source": "overview", "centrality": "central",
        "overview_quote": "This invented sentence is not a real overview citation."}]}])
    with pytest.raises(ValueError, match="exact source excerpt"):
        validate_assessments(batch, [movie], ThemeRequest(query="dream", themes=["dream"]))
