import pytest
from pydantic import ValidationError

from moviegraphrag.examples import reference_plan
from moviegraphrag.retrieval.compiler import compile_graph, route
from moviegraphrag.schemas.models import QuestionDraft


@pytest.mark.parametrize("key", [f"Q{i}" for i in range(1, 11)])
def test_reference_plans(key):
    plan = reference_plan(key)
    expected = "GRAPH" if key in {"Q1", "Q2", "Q3", "Q4"} else (
        "VECTOR" if key in {"Q5", "Q6", "Q7"} else "HYBRID_GRAPH_FIRST")
    assert route(plan) == expected


def test_compiler_does_not_interpolate_user_text():
    data = reference_plan("Q1").model_dump()
    data["mentions"][0]["text"] = "Nolan') DETACH DELETE n //"
    plan = QuestionDraft.model_validate(data)
    compiled = compile_graph(plan, {m.ref: i + 1 for i, m in enumerate(plan.mentions)})
    assert "DELETE" not in compiled.prefix
    assert "$id_" in compiled.where
    assert list(compiled.parameters.values()) == [1, 2]


@pytest.mark.parametrize("mutation", ["bad_direction", "unknown_edge", "disconnected", "unbound", "bad_target"])
def test_reject_invalid_patterns(mutation):
    data = reference_plan("Q1").model_dump()
    if mutation == "bad_direction":
        data["edges"][0]["source"], data["edges"][0]["target"] = "result", "director"
    elif mutation == "unknown_edge":
        data["edges"][0]["relation"] = "DELETE"
    elif mutation == "disconnected":
        data["variables"].append({"ref": "other", "label": "Movie"})
    elif mutation == "unbound":
        data["variables"][1]["mention_ref"] = None
    else:
        data["target"] = "director"
    with pytest.raises(ValidationError):
        QuestionDraft.model_validate(data)


def test_q4_does_not_require_different_witness_films():
    plan = reference_plan("Q4")
    query = compile_graph(plan, {m.ref: i + 1 for i, m in enumerate(plan.mentions)})
    assert "<>" not in query.where
    assert len(plan.edges) == 4
