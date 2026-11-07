import pytest

from moviegraphrag.evaluation import metrics


def test_metrics_with_graded_and_incomplete_judgments():
    result = metrics([1, 2, 3], {"1": 2, "2": 1}, exhaustive=False)
    assert result["precision_at_5"] == .4
    assert result["ndcg_at_5"] == pytest.approx(1)
    assert result["recall_at_5"] is None
    assert metrics([1], {"1": 2, "2": 1}, exhaustive=True)["recall_at_5"] == .5
