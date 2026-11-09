"""Source and plan validation using the real corpus, with no provider substitutions."""
import pytest
from pydantic import ValidationError

from moviegraphrag.evidence.context import validate_generated
from moviegraphrag.evidence.validation import document_evidence
from moviegraphrag.examples import reference_plan
from moviegraphrag.quantities import validate_quantity
from moviegraphrag.schemas.models import GeneratedAnswer, QuestionDraft


def test_unknown_cypher_and_wrong_directions_are_rejected():
    raw = reference_plan("Q1").model_dump()
    raw["cypher"] = "MATCH (n) DELETE n"
    with pytest.raises(ValidationError):
        QuestionDraft.model_validate(raw)
    raw.pop("cypher")
    raw["edges"][0]["source"], raw["edges"][0]["target"] = "result", "director"
    with pytest.raises(ValidationError):
        QuestionDraft.model_validate(raw)


def test_thematic_counts_and_centrality_are_not_silently_downgraded():
    data = reference_plan("Q6").model_dump()
    data["operation"] = "count"
    with pytest.raises(ValidationError, match="Thematic counts"):
        QuestionDraft.model_validate(data)
    data["operation"] = "recommend"
    data["theme_request"]["requirement"] = "central"
    with pytest.raises(ValidationError, match="Centrality"):
        QuestionDraft.model_validate(data)


def test_real_document_integrity_and_provenance(store):
    movie, manifest = store.movie(27205), store.manifest()
    proof = document_evidence(movie, 27205, manifest)
    assert proof.kind == "document"
    assert proof.data["overview"] == movie["overview"]
    assert proof.data["thematic_verification"] == "not_performed"
    for changed in ({**movie, "snapshot_id": "other"}, {**movie, "document_text": "corrupted"}):
        with pytest.raises(RuntimeError):
            document_evidence(changed, 27205, manifest)
    valid = GeneratedAnswer(statements=[{"text": "Inception", "evidence_ids": [proof.id]}])
    assert "[d27205]" in validate_generated(valid, [proof.model_dump()])
    invalid = GeneratedAnswer(statements=[{"text": "Inception", "evidence_ids": ["missing"]}])
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_generated(invalid, [proof.model_dump()])


@pytest.mark.parametrize("question,quantity", [
    ("Recommande trois films sur l'IA", 3), ("Recommande un film sur les rêves", 1),
    ("Montre-moi les dix meilleurs films sur l'espace", 10), ("Top 25 films sur l'IA", 25),
    ("Quels films sont sortis après 2010 ?", None),
    ("Quels acteurs ont joué dans un film réalisé par Nolan ?", None),
])
def test_quantity_is_explicit_and_not_a_year_or_witness_article(question, quantity):
    assert validate_quantity(question, None) == quantity
    if quantity is None:
        with pytest.raises(ValueError, match="explicit result quantity"):
            validate_quantity(question, 1)
