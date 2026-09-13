import json
from pathlib import Path

import pytest
from app.sub_agent.text2sql import plan_text2sql
from test_snapshot_queries import catalog

DEFAULTS = json.loads((Path(__file__).resolve().parents[2] / "web/src/default-questions.json").read_text())


@pytest.mark.parametrize("fab", ["fab10", "fab11", "fab12", "fab13"])
@pytest.mark.parametrize("card", [c for c in DEFAULTS if "{fab}" in c["prompt"]], ids=lambda c: c["title"])
def test_exact_ui_default_has_a_bounded_observation_plan(fab, card):
    result = plan_text2sql(card["prompt"].replace("{fab}", fab.upper()),
                          query_type="trend" if card["icon"] == "chart" else "status",
                          database_catalog=catalog(fab))
    assert result.status == "succeeded"
    assert f"{fab}.live_process_snapshots_{fab}" in result.sql
    assert "WHERE" in result.sql
    assert "interval_end" in result.sql
    if card["icon"] == "search":
        assert "avg_queue_minutes" in result.sql
        assert "wip_lots" in result.sql
        assert "'etch'" in result.sql
        assert "INTERVAL '168 hours'" in result.sql
    elif card["icon"] == "chart":
        assert "yield_percent" in result.sql
        assert result.plan.chart_intent
    else:
        assert "wip_lots" in result.sql
