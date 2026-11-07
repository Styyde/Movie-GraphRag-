"""Entity linking against the actual TMDB snapshot in Neo4j, without test doubles."""
import pytest

from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.schemas.models import QuestionDraft


def contextual_actor_plan(year=None):
    movie = {"ref": "identity_movie", "text": "Batman", "type": "Movie"}
    if year is not None:
        movie["year"] = year
    return QuestionDraft(
        mentions=[
            {"ref": "actor_name", "text": "Michael Keaton", "type": "Person",
             "role": "actor", "context_movie_ref": "identity_movie"},
            movie,
        ],
        variables=[{"ref": "result", "label": "Movie"},
                   {"ref": "actor", "label": "Person", "mention_ref": "actor_name"}],
        edges=[{"source": "actor", "relation": "ACTED_IN", "target": "result"}],
    )


def test_ambiguous_identifying_movie_defers_person_resolution(store):
    links = {r.mention.ref: r for r in EntityLinker(store).link(contextual_actor_plan())}
    assert links["identity_movie"].status == "ambiguous"
    assert links["identity_movie"].total_candidates == 2
    actor = links["actor_name"]
    assert actor.status == "ambiguous"
    assert actor.blocked_by_context == "identity_movie"
    assert actor.canonical_id is None
    assert actor.total_candidates >= 1


def test_explicit_film_year_resolves_real_contextual_actor(store):
    links = EntityLinker(store).link(contextual_actor_plan(1989))
    assert all(link.status == "resolved" for link in links)
    actor = next(link for link in links if link.mention.ref == "actor_name")
    assert actor.canonical_name == "Michael Keaton"
    assert actor.method == "contextual"
    assert actor.blocked_by_context is None
    assert any(value.startswith("explicit_movie=") for value in actor.contextual_evidence)


def test_confirming_identifying_movie_replays_dependent_resolution(store):
    linker = EntityLinker(store)
    plan = contextual_actor_plan()
    first = {link.mention.ref: link for link in linker.link(plan)}
    movies = first["identity_movie"].alternatives
    selected = next(movie.id for movie in movies if movie.year == 1989)
    links = linker.link(plan, {"identity_movie": selected},
                        {"identity_movie": [movie.id for movie in movies]})
    assert all(link.status == "resolved" for link in links)


def test_common_surname_requires_refinement_and_exposes_truncation(store):
    plan = QuestionDraft(
        mentions=[{"ref": "actor_name", "text": "Smith", "type": "Person", "role": "actor"}],
        variables=[{"ref": "result", "label": "Movie"},
                   {"ref": "actor", "label": "Person", "mention_ref": "actor_name"}],
        edges=[{"source": "actor", "relation": "ACTED_IN", "target": "result"}],
    )
    linker = EntityLinker(store)
    result = linker.link(plan)[0]
    assert result.status == "ambiguous"
    assert result.total_candidates > 10
    assert result.truncated
    assert len(result.alternatives) == 10
    assert any("refine" in value for value in result.contextual_evidence)
    selected = result.alternatives[0].id
    with pytest.raises(ValueError, match="refine"):
        linker.link(plan, {"actor_name": selected}, {"actor_name": [selected]})
