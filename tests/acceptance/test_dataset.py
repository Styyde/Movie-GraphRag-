import pytest

from moviegraphrag.evaluation import graph_reference

pytestmark = pytest.mark.dataset


def test_real_dataset_counts(dataset):
    assert dataset.manifest["counts"] == {"movies": 4803, "people": 56603, "genres": 20,
                                          "acted_in": 106084, "directed": 5166, "in_genre": 12160}
    assert dataset.report["ambiguous_names"] == 405
    assert dataset.report["empty_overview"] == 4
    assert dataset.report["both_roles"] == 563


def test_real_csv_acceptance_answers(dataset):
    ref = graph_reference(dataset)
    assert set(ref["Q1"]) == {272, 155, 49026, 27205}
    assert len(ref["Q2"]) == 17
    assert len(ref["Q3"]) == 509
    assert set(ref["Q4"]) == {525, 2034}
    assert ref["Q8"] == [27205]
    assert len(ref["Q9"]) == 23
    assert len(ref["Q10"]) == 254


def test_aggregate_cast_preserves_all_credits(dataset):
    import json
    assert sum(len(json.loads(r["credits_json"])) for r in dataset.acted_in) == 106257
    duplicates = [r for r in dataset.acted_in if len(json.loads(r["credits_json"])) > 1]
    assert len(duplicates) == 165
