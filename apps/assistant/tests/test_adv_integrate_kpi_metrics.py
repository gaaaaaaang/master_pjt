import pytest
from scripts.evaluate_adv_integrate_kpi import sql_similarity


def test_formatting_does_not_reduce_similarity():
    result = sql_similarity("select a from t where a=1;", "SELECT a FROM t WHERE a = 1")
    assert result["em"] == 1
    assert result["soft_f1"] == 1


@pytest.mark.parametrize(
    "changed",
    [
        "SELECT a FROM t WHERE a=2",
        "SELECT a FROM t WHERE a>1",
        "SELECT a FROM other WHERE a=1",
        "SELECT a FROM t WHERE a=1 LIMIT 1",
    ],
)
def test_material_changes_are_not_exact_matches(changed):
    result = sql_similarity("SELECT a FROM t WHERE a=1", changed)
    assert result["em"] == 0
    assert 0 <= result["soft_f1"] < 1


def test_missing_prediction_is_failure():
    result = sql_similarity(None, "SELECT a FROM t")
    assert result["em"] == result["soft_f1"] == 0


def test_multiple_statements_are_rejected():
    result = sql_similarity("SELECT a FROM t; SELECT b FROM t", "SELECT a FROM t")
    assert result["em"] == result["soft_f1"] == 0
    assert "parse_error" in result


def test_similarity_is_symmetric():
    a, b = "SELECT a FROM t", "SELECT a, b FROM t WHERE b=2"
    assert sql_similarity(a, b)["soft_f1"] == sql_similarity(b, a)["soft_f1"]
