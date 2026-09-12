"""Approved live-model regression with isolated memory and a model-host allowlist."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from evaluate_demo_offline import ask_stream_route

COMPARISON = [
    {"id": "fab11_wip", "question": "fab11 wip은 몇개야??", "fabs": ["fab11"], "wip": 188},
    {"id": "fab13_wip", "question": "fab13 wip은 몇개야??", "fabs": ["fab13"], "wip": 159},
    {"id": "screenshot_comparison", "question": "방금 답한 fab11이랑 fab13의 wip 차이에 대해서 분석해줄래?? 왜 fab11에는 더 쌓였는지 궁금해. 시각화 자료도 그려줄 수 있음 그려줘", "fabs": ["fab11", "fab13"], "chart": True},
    {"id": "implicit_comparison", "question": "방금 조회한 두 FAB의 WIP 차이를 비교해줘", "fabs": ["fab11", "fab13"], "chart": True},
    {"id": "total_trend", "question": "FAB11과 FAB13의 최근 24시간 WIP 추세를 비교해서 그래프로 보여줘", "fabs": ["fab11", "fab13"], "chart": True},
]
REGRESSION = [
    {"id": "fab10_wip", "question": "FAB10 지금 WIP 몇 개야?", "fabs": ["fab10"], "new": True},
    {"id": "fab12_status", "question": "FAB12 etch 공정의 현재 수율과 가동률은 얼마야?", "fabs": ["fab12"], "new": True},
    {"id": "fab12_followup", "question": "같은 공정의 WIP도 알려줘", "fabs": ["fab12"]},
    {"id": "fab12_impact", "question": "그 공정의 가동률이 5%p 떨어지면 처리량에 얼마나 영향이 있어?", "fabs": ["fab12"], "impact": True},
    {"id": "fab12_trend", "question": "FAB12 최근 7일 공정별 수율 추세를 그래프로 보여줘", "fabs": ["fab12"], "chart": True, "new": True},
    {"id": "fab12_rank", "question": "그 기간 평균 수율이 가장 낮은 공정을 알려줘", "fabs": ["fab12"]},
    {"id": "fab13_diagnosis", "question": "FAB13 photo 공정의 대기 시간이 늘어난 원인을 데이터와 문서 근거로 분석해줘", "fabs": ["fab13"], "new": True},
    {"id": "process_knowledge", "question": "Cycle Time Degradation이 무엇인지 문서 근거로 설명해줘", "knowledge": True, "new": True},
]


def check(case, result):
    errors = []
    if result.get("status") != "succeeded":
        errors.append("status=" + str(result.get("status")))
    usage = result.get("model_usage", {})
    if not any(call.get("succeeded") and call.get("transport_attempted") for call in usage.get("calls", [])):
        errors.append("no_successful_live_model_call")
    composer_kind = "fab_grounded_answer" if case.get("knowledge") else "fab_final_answer"
    if not any(call.get("kind") == composer_kind and call.get("succeeded") for call in usage.get("calls", [])):
        errors.append("no_live_composer")
    evidence = result.get("evidence", [])
    rows = (result.get("query_result") or {}).get("rows") or []
    if case.get("fabs"):
        plans = [item.get("metadata", {}).get("query_plan") for item in evidence if item.get("source_type") == "text2sql_plan"]
        plan = next((item for item in reversed(plans) if item), {})
        actual = (plan.get("slots", {}).get("fab_ids", {}).get("value") or plan.get("fab_id") or "").split(",")
        if set(actual) != set(case["fabs"]):
            errors.append("fab_scope_mismatch")
        if not rows:
            errors.append("missing_rows")
        if len(case["fabs"]) > 1 and {str(row.get("fab", "")).lower() for row in rows} != set(case["fabs"]):
            errors.append("missing_fab_rows")
    if "wip" in case and (len(rows) != 1 or rows[0].get("wip_lots") != case["wip"]):
        errors.append("captured_snapshot_wip_mismatch")
    if case.get("chart") and not result.get("chart"):
        errors.append("missing_chart")
    if case.get("impact") and not any(item.get("source_type") == "impact_calculation" for item in evidence):
        errors.append("missing_impact_calculation")
    if case.get("knowledge") and not result.get("citations"):
        errors.append("missing_citations")
    if case["id"] in {"fab11_wip", "fab13_wip"} and "시뮬레이션" in result.get("answer", ""):
        errors.append("repeated_provenance_in_current_answer")
    expected = case.get("expected", {})
    if expected.get("query_type") and result.get("query_type") != expected["query_type"]:
        errors.append("query_type_mismatch")
    if expected.get("area") and {row.get("area") for row in rows} != {expected["area"]}:
        errors.append("process_scope_mismatch")
    if expected.get("metrics") and any(not set(expected["metrics"]) <= set(row) for row in rows):
        errors.append("metric_contract_mismatch")
    if expected.get("row_count") is not None and len(rows) != expected["row_count"]:
        errors.append("row_count_mismatch")
    if expected.get("approved") and not result.get("answer_review", {}).get("approved"):
        errors.append("answer_review_rejected")
    return errors


async def run(args):
    from app.api import routes
    from app.config import get_settings
    from app.schemas.chat import ChatRequest
    from app.services.conversation_memory import ConversationMemory

    settings = get_settings()
    assert urlsplit(settings.openai_endpoint).hostname == "skax.ai-talentlab.com"
    assert urlsplit(settings.postgres_dsn).hostname in {"localhost", "127.0.0.1", "::1"}
    assert settings.openai_api_key and not settings.mock_mode
    assert not settings.langsmith_tracing and not settings.vector_db_url
    original_send = httpx.Client.send
    network_calls = []

    def approved_send(client, request, **kwargs):
        if request.url.scheme != "https" or request.url.host != "skax.ai-talentlab.com" or not request.url.path.startswith("/openai/deployments/"):
            raise RuntimeError("Live regression blocked a destination outside the approved model API")
        kwargs["follow_redirects"] = False
        response = original_send(client, request, **kwargs)
        network_calls.append({"host": request.url.host, "status_code": response.status_code})
        return response

    async def unexpected_async(*args, **kwargs):
        raise RuntimeError("Unexpected async HTTP in synchronous model workflow")

    selected = COMPARISON if args.suite == "comparison" else REGRESSION
    if getattr(args, "fixture", None):
        selected = json.loads(args.fixture.read_text())["cases"]
    if args.ids:
        selected = [case for case in selected if case["id"] in args.ids]
    source = Path(__file__).resolve().parents[1] / "src"
    report = {"started_at": datetime.now(UTC).isoformat(), "suite": args.suite,
              "mode": "live_model", "model": settings.openai_model, "approved_host": "skax.ai-talentlab.com",
              "transport": "production_SSE_route_in_process; actual_HTTP_to_model", "memory_reloaded_each_turn": True,
              "source_sha256": hashlib.sha256(b"".join(p.read_bytes() for p in sorted(source.rglob("*.py")))).hexdigest(), "results": []}
    if getattr(args, "fixture", None):
        report["fixture_sha256"] = hashlib.sha256(args.fixture.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="fab-live-followup-") as temp, patch.object(httpx.Client, "send", approved_send), \
            patch.object(httpx.AsyncClient, "send", unexpected_async), patch.object(routes, "conversation_memory", ConversationMemory()):
        conversation = None
        for index, case in enumerate(selected):
            if index and args.pause:
                await asyncio.sleep(args.pause)
            if case.get("new"):
                conversation = None
            routes.conversation_memory = ConversationMemory(store_path=Path(temp) / "memory.sqlite3")
            started = time.monotonic()
            record = dict(case)
            try:
                result, events = await ask_stream_route(ChatRequest(message=case["question"], conversation_id=conversation))
                conversation = result["conversation_id"]
                errors = check(case, result)
                record.update(response=result, errors=errors, passed=not errors, event_count=len(events))
            except Exception as exc:  # noqa: BLE001 - record each live failure, then continue bounded suite
                record.update(passed=False, error=f"{type(exc).__name__}: {exc}")
            record["seconds"] = round(time.monotonic() - started, 3)
            report["results"].append(record)
            report["network_calls"] = network_calls
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            print(json.dumps({key: record.get(key) for key in ["id", "passed", "seconds", "errors", "error"]}, ensure_ascii=False), flush=True)
    report["passed"] = all(item["passed"] for item in report["results"])
    report["finished_at"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--suite", choices=["comparison", "regression"], default="comparison")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--fixture", type=Path, help="Frozen question and acceptance contracts for generalization evaluation")
    parser.add_argument("--pause", type=float, default=20, help="Seconds between turns to avoid burst token limits")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["LANGSMITH_TRACING"] = "false"
    raise SystemExit(0 if asyncio.run(run(args)) else 1)
