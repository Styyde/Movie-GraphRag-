"""Isolate strict assessment from analysis using one real source record and Groq."""
import pytest

from moviegraphrag.evidence.validation import validate_assessments
from moviegraphrag.examples import reference_plan
from moviegraphrag.llm.groq import GroqLLM, Usage


@pytest.mark.llm
def test_real_groq_strict_assessment_of_inception(store, settings, groq_configured):
    movie = store.movie(27205)
    record = {key: movie.get(key) for key in (
        "movie_id", "title", "overview", "keywords", "keyword_ids", "snapshot_id", "text_coverage"
    )}
    theme = reference_plan("Q8").theme_request
    llm, usage = GroqLLM(settings), Usage()
    try:
        assessment = llm.assess(theme, [record], usage)
    finally:
        llm.close()
    assert [film.movie_id for film in assessment.films] == [27205]
    accepted = validate_assessments(assessment, [record], theme)
    assert [film.movie_id for film, _ in accepted] == [27205]
    assert usage.calls >= 1 and usage.models
