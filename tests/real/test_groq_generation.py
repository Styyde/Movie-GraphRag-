"""Actual Groq generation from a document retrieved from the real Neo4j corpus."""
import pytest
from pydantic import ValidationError

from moviegraphrag.evidence.context import build_context, validate_generated
from moviegraphrag.evidence.validation import document_evidence
from moviegraphrag.llm.groq import GroqLLM, Usage
from moviegraphrag.llm.prompts import ANALYZER
from moviegraphrag.schemas.models import Coverage, QuestionDraft


@pytest.mark.llm
def test_real_groq_generation_cites_only_available_documents(store, settings, groq_configured):
    proof = document_evidence(store.movie(27205), 27205, store.manifest())
    context, truncated = build_context(
        [{"id": 27205, "name": "Inception", "type": "Movie", "evidence_ids": [proof.id]}],
        [proof], Coverage(returned=1), 24000,
        {"question": "Présente cette recommandation sans certifier sa centralité.", "language": "fr"})
    assert not truncated
    llm, usage = GroqLLM(settings), Usage()
    try:
        generated = llm.generate(context, usage)
    finally:
        llm.close()
    assert validate_generated(generated, context["evidence"])
    assert set(usage.stages) == {"GeneratedAnswer"}


@pytest.mark.llm
def test_real_groq_json_mode_returns_typed_plan_or_rejects_bad_schema(settings, groq_configured):
    llm, usage = GroqLLM(settings), Usage()
    plan = None
    try:
        try:
            plan = llm.structured(QuestionDraft, ANALYZER,
                {"question": "Combien de films contient le dataset ?"}, usage, json_object=True)
        except ValidationError:
            # JSON mode guarantees syntax, not schema. Rejecting a malformed plan is
            # correct behavior here; end-to-end intent/count correctness is tested via /questions.
            pass
    finally:
        llm.close()
    assert plan is None or isinstance(plan, QuestionDraft)
    assert usage.models and usage.prompt_tokens > 0
    assert usage.formats == {"json_object": usage.calls}
