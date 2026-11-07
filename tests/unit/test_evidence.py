import pytest

from moviegraphrag.evidence.context import validate_generated
from moviegraphrag.evidence.validation import validate_assessments
from moviegraphrag.schemas.models import AssessmentBatch, GeneratedAnswer, ThemeRequest

MOVIE = {"movie_id": 157336, "overview": "Explorers travel through a wormhole.",
         "keyword_ids": [310], "keywords": ["artificial intelligence"]}
REQUEST = ThemeRequest(query="IA", themes=["IA"])


def batch(source="keyword", centrality="unknown", quote=None, ids=None):
    return AssessmentBatch(films=[{"movie_id": 157336, "judgments": [{
        "theme": "IA", "source": source, "centrality": centrality,
        "overview_quote": quote, "keyword_ids": [310] if ids is None else ids}]}])


def test_keyword_association_accepted_but_not_centrality():
    assert validate_assessments(batch(), [MOVIE], REQUEST)
    central = REQUEST.model_copy(update={"requirement": "central"})
    assert not validate_assessments(batch(), [MOVIE], central)
    with pytest.raises(ValueError):
        validate_assessments(batch(centrality="central"), [MOVIE], REQUEST)


def test_fabricated_excerpt_rejected():
    with pytest.raises(ValueError):
        validate_assessments(batch(source="overview", quote="Robots save humanity", ids=[]), [MOVIE], REQUEST)


def test_unknown_keyword_rejected():
    with pytest.raises(ValueError):
        validate_assessments(batch(ids=[999]), [MOVIE], REQUEST)


def test_missing_film_rejected():
    with pytest.raises(ValueError):
        validate_assessments(AssessmentBatch(films=[]), [MOVIE], REQUEST)


def test_answer_cannot_cite_unknown_evidence():
    answer = GeneratedAnswer(statements=[{"text": "claim", "evidence_ids": ["invented"]}])
    with pytest.raises(ValueError):
        validate_generated(answer, [])
