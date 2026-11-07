import pytest

from moviegraphrag.linking.clarifications import Clarifications
from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.schemas.models import EntityCandidate, QuestionDraft


class Store:
    def find_entities(self, text, label):
        if text == "Fright Night":
            return [EntityCandidate(id=11797, name=text, type=label, method="exact")]
        return [EntityCandidate(id=1, name="Tom Holland", type=label, method="exact", roles=["actor"]),
                EntityCandidate(id=2, name="Tom Holland", type=label, method="exact", roles=["director"])]

    def context_matches(self, person, movie, role):
        return person == 2 and movie == 11797


def plan(context=False):
    mentions = [{"ref": "person", "text": "Tom Holland", "type": "Person"}]
    if context:
        mentions[0].update(role="director", context_movie_ref="film")
        mentions.append({"ref": "film", "text": "Fright Night", "type": "Movie"})
    return QuestionDraft(mentions=mentions, variables=[
        {"ref": "actor", "label": "Person", "mention_ref": "person"},
        {"ref": "result", "label": "Movie"}],
        edges=[{"source": "actor", "relation": "ACTED_IN", "target": "result"}])


def test_homonyms_require_clarification():
    result = EntityLinker(Store()).link(plan())[0]
    assert result.status == "ambiguous"
    assert result.canonical_id is None


def test_explicit_context_disambiguates():
    result = EntityLinker(Store()).link(plan(True))[0]
    assert result.canonical_id == 2
    assert result.method == "contextual"


def test_unsigned_selection_rejected():
    with pytest.raises(ValueError):
        EntityLinker(Store()).link(plan(), {"person": 1})


def test_clarification_is_scoped_and_tamper_proof():
    clarifications = Clarifications(secret=b"unit-test-secret")
    p = plan()
    token = clarifications.issue("question", p, EntityLinker(Store()).link(p))
    _, allowed = clarifications.verify(token, "question")
    assert allowed == {"person": [1, 2]}
    for token_value, question in [(token + "x", "question"), (token, "different")]:
        with pytest.raises(ValueError):
            clarifications.verify(token_value, question)


def test_fuzzy_unique_needs_confirmation():
    class FuzzyStore(Store):
        def find_entities(self, text, label):
            return [EntityCandidate(id=1, name="Tom Holland", type=label, method="fuzzy")]
    assert EntityLinker(FuzzyStore()).link(plan())[0].status == "ambiguous"
