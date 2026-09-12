import pytest
from app.sub_agent.text2sql import answer_question
from psycopg import OperationalError


@pytest.mark.parametrize("question,fail_at", [
    ("FAB11 etch 공정의 대기 시간이 늘어난 원인을 근거로 분석해 줘", "fab11"),
    ("FAB11과 FAB13의 WIP을 비교해줘", "fab13"),
])
def test_catalog_outage_is_not_unsupported_or_partial(monkeypatch, question, fail_at):
    def load(fab):
        if fab == fail_at:
            raise OperationalError("connection refused: private connection details")
        return {f"{fab}.example_{fab}": {"logical_table": "example"}}

    monkeypatch.setattr("app.sub_agent.text2sql.load_fab_catalog", load)
    result = answer_question(question, execute=True, query_type="status")
    assert result.status == "failed"
    assert result.sql is None
    assert "DB 연결" in result.answer
    assert "private connection details" not in str(result)
