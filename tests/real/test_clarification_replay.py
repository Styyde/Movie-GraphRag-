"""Signed replay through the real linker, graph store, and controlled pipeline.

The initial plans are explicit test inputs, not substituted LLM responses. Replaying
a signed plan must not call the analyzer; Groq itself is covered by the HTTP suite.
"""
import pytest

from moviegraphrag.examples import reference_plan
from moviegraphrag.linking.clarifications import Clarifications
from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.llm.groq import GroqLLM
from moviegraphrag.orchestration import MovieGraphRAG
from moviegraphrag.schemas.models import QuestionDraft, QuestionRequest


QUESTION = "Quels films avec Michael Keato, l'acteur de Batman ?"


def fuzzy_contextual_plan():
    return QuestionDraft(
        mentions=[
            {"ref": "actor_name", "text": "Michael Keato", "type": "Person",
             "role": "actor", "context_movie_ref": "identity_movie"},
            {"ref": "identity_movie", "text": "Batman", "type": "Movie"},
        ],
        variables=[{"ref": "result", "label": "Movie"},
                   {"ref": "actor", "label": "Person", "mention_ref": "actor_name"}],
        edges=[{"source": "actor", "relation": "ACTED_IN", "target": "result"}],
    )


@pytest.fixture
def replay_service(store, embedder, settings):
    llm = GroqLLM(settings)
    try:
        yield MovieGraphRAG(store, embedder, llm, settings)
    finally:
        llm.close()


def choose_identifying_film(service):
    plan = fuzzy_contextual_plan()
    links = EntityLinker(service.store).link(plan)
    movie = next(link for link in links if link.mention.ref == "identity_movie")
    actor = next(link for link in links if link.mention.ref == "actor_name")
    assert movie.status == "ambiguous"
    assert actor.blocked_by_context == "identity_movie"
    selected = next(candidate.id for candidate in movie.alternatives if candidate.year == 1989)
    token = service.clarifications.issue(QUESTION, plan, links)
    first = service.ask(QuestionRequest(
        question=QUESTION, clarification_token=token, selections={"identity_movie": selected},
    ))
    assert first.status == "needs_clarification", first
    return first, selected


def test_successive_clarifications_retain_and_revalidate_confirmed_choices(replay_service):
    first, selected_movie = choose_identifying_film(replay_service)
    _, allowed, confirmed = replay_service.clarifications.verify(first.clarification_token, QUESTION)
    assert confirmed == {"identity_movie": selected_movie}
    assert set(allowed) == {"actor_name"}
    links = {link.mention.ref: link for link in first.resolved_entities}
    assert links["identity_movie"].method == "user_confirmed"
    assert links["actor_name"].status == "ambiguous"
    chosen_actor = next(candidate.id for candidate in links["actor_name"].alternatives
                        if candidate.name == "Michael Keaton")
    final = replay_service.ask(QuestionRequest(
        question=QUESTION, clarification_token=first.clarification_token,
        selections={"actor_name": chosen_actor},
    ))
    assert final.status == "ok", final
    linked = {link.mention.ref: link for link in final.resolved_entities}
    assert linked["identity_movie"].canonical_id == selected_movie
    assert linked["actor_name"].canonical_id == chosen_actor
    assert all(link.method == "user_confirmed" for link in linked.values())
    assert all(row["type"] == "Movie" for row in final.results)
    assert final.trace["llm_usage"]["calls"] == 0


def test_client_can_only_submit_new_choices_in_the_current_token(replay_service):
    first, selected_movie = choose_identifying_film(replay_service)
    with pytest.raises(ValueError, match="Only new selections"):
        replay_service.ask(QuestionRequest(
            question=QUESTION, clarification_token=first.clarification_token,
            selections={"identity_movie": selected_movie},
        ))
    with pytest.raises(ValueError, match="not proposed"):
        replay_service.ask(QuestionRequest(
            question=QUESTION, clarification_token=first.clarification_token,
            selections={"actor_name": 2**63 - 1},
        ))


@pytest.mark.parametrize("key,expected_type", [("Q1", "Movie"), ("Q2", "Person")])
def test_graph_results_identify_movies_and_people(replay_service, key, expected_type):
    plan = reference_plan(key)
    links = EntityLinker(replay_service.store).link(plan)
    question = f"Question de graphe {key}"
    token = replay_service.clarifications.issue(question, plan, links)
    response = replay_service.ask(QuestionRequest(question=question, clarification_token=token))
    assert response.status == "ok", response
    assert response.results and all(row["type"] == expected_type for row in response.results)


def test_clarification_signature_question_scope_and_expiry_against_real_candidates(store):
    plan = fuzzy_contextual_plan()
    links = EntityLinker(store).link(plan)
    session = Clarifications()
    token = session.issue(QUESTION, plan, links)
    _, allowed, confirmed = session.verify(token, QUESTION)
    assert set(allowed) == {"identity_movie"}
    assert confirmed == {}
    with pytest.raises(ValueError, match="another question"):
        session.verify(token, QUESTION + " ")
    changed = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(ValueError, match="signature"):
        session.verify(changed, QUESTION)
    with pytest.raises(ValueError, match="signature"):
        Clarifications().verify(token, QUESTION)
    expired = Clarifications(ttl=0)
    with pytest.raises(ValueError, match="expired"):
        expired.verify(expired.issue(QUESTION, plan, links), QUESTION)
    for malformed in ("", token.split(".")[0], ".", "invalid.signature", "x" * 65537):
        with pytest.raises(ValueError, match="Malformed"):
            session.verify(malformed, QUESTION)
