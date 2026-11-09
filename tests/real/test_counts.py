"""Exact aggregates against independent raw CSV facts and the real Neo4j corpus."""
import csv
import json
from pathlib import Path

import pytest

from moviegraphrag.evaluation import graph_suite_reference
from moviegraphrag.examples import reference_plan
from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.llm.groq import GroqLLM
from moviegraphrag.orchestration import MovieGraphRAG
from moviegraphrag.schemas.models import QuestionDraft, QuestionRequest


@pytest.fixture(scope="module")
def raw_facts():
    with Path("data/raw/tmdb_5000_credits.csv").open(encoding="utf-8-sig", newline="") as stream:
        credits = list(csv.DictReader(stream))
    actors, directors = set(), set()
    leo_directed = set()
    for row in credits:
        actors.update(int(p["id"]) for p in json.loads(row["cast"]))
        for person in json.loads(row["crew"]):
            if person.get("job") == "Director":
                directors.add(int(person["id"]))
                if int(person["id"]) == 6193:
                    leo_directed.add(int(row["movie_id"]))
    return {"movies": len(credits), "actors": len(actors), "directors": len(directors),
            "people": len(actors | directors), "leo_directed": len(leo_directed)}


@pytest.fixture
def pipeline(store, embedder, settings):
    llm = GroqLLM(settings)
    try:
        yield MovieGraphRAG(store, embedder, llm, settings)
    finally:
        llm.close()


def replay(pipeline, plan, question="Comptage explicite de référence", **options):
    links = EntityLinker(pipeline.store).link(plan)
    token = pipeline.clarifications.issue(question, plan, links)
    return pipeline.ask(QuestionRequest(question=question, clarification_token=token, **options))


@pytest.mark.parametrize("key,case_id,expected", [("Q1", "graph-01", 4), ("Q3", "graph-03", 509),
                                                ("Q4", "graph-04", 2)])
def test_exact_counts_match_independent_raw_csv_gold(pipeline, key, case_id, expected):
    gold = graph_suite_reference(Path("data/raw/tmdb_5000_movies.csv"),
                                 Path("data/raw/tmdb_5000_credits.csv"))
    plan = QuestionDraft.model_validate({**reference_plan(key).model_dump(), "operation": "count"})
    response = replay(pipeline, plan, limit=1)
    assert response.status == "ok", response
    assert response.count == len(gold[case_id]["relevance"]) == expected
    assert response.operation == "count" and not response.results
    assert response.evidence[0].kind == "aggregate"
    assert response.evidence[0].data["snapshot_id"] == gold[case_id]["snapshot_id"]
    assert response.coverage.effective_limit is None
    assert response.trace["llm_usage"]["calls"] == 0


@pytest.mark.parametrize("kind", ["movies", "people", "actors", "directors"])
def test_global_counts_are_distinct_and_not_limited(pipeline, raw_facts, kind):
    target = "Movie" if kind == "movies" else "Person"
    data = {"operation": "count", "variables": [{"ref": "result", "label": target}]}
    if kind in {"actors", "directors"}:
        data["variables"].append({"ref": "film", "label": "Movie"})
        data["edges"] = [{"source": "result", "target": "film",
                          "relation": "ACTED_IN" if kind == "actors" else "DIRECTED"}]
    response = replay(pipeline, QuestionDraft.model_validate(data), limit=1)
    assert response.status == "ok", response
    assert response.count == raw_facts[kind]


def test_known_actor_without_director_credits_is_zero_not_missing(pipeline, raw_facts):
    plan = QuestionDraft(operation="count", mentions=[{
        "ref": "leo", "text": "Leonardo DiCaprio", "type": "Person", "role": "director"}],
        variables=[{"ref": "person", "label": "Person", "mention_ref": "leo"},
                   {"ref": "result", "label": "Movie"}],
        edges=[{"source": "person", "relation": "DIRECTED", "target": "result"}])
    response = replay(pipeline, plan)
    assert response.status == "ok", response
    assert response.count == raw_facts["leo_directed"] == 0
    assert response.resolved_entities[0].canonical_id == 6193


def test_count_does_not_hide_unknown_or_ambiguous_identity(pipeline):
    for name, status in (("PersonneInexistanteXYZ", "entity_not_found"), ("Tom Holland", "needs_clarification")):
        plan = QuestionDraft(operation="count", mentions=[{"ref": "p", "text": name, "type": "Person"}],
            variables=[{"ref": "p", "label": "Person", "mention_ref": "p"},
                       {"ref": "result", "label": "Movie"}],
            edges=[{"source": "p", "relation": "ACTED_IN", "target": "result"}])
        response = replay(pipeline, plan)
        assert response.status == status, response
        assert response.count is None
