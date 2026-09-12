"""Reproducible live demo probes. Requires --live; preserves each full response."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path


def json_value(value):
    """Keep Pydantic evidence structured rather than turning it into repr strings."""
    if hasattr(value, "model_dump"):
        return json_value(value.model_dump(mode="json"))
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def cases():
    templates = {
        "status": "{fab} 지금 WIP 몇 개야?",
        "area_status": "{fab} etch 공정의 현재 WIP과 Queue Time 알려줘",
        "trend": "{fab} 최근 7일 공정별 수율 추세를 그래프로 보여줘",
        "diagnosis": "왜 {fab} etch 공정 Queue Time이 늘었어? 데이터와 문서 근거로 원인 후보를 설명해줘",
        "impact": "{fab} etch 가동률이 5%p 떨어지면 처리량에 얼마나 영향이 있어?",
        "master": "{fab} 공정 영역별 설비 대수 합계를 보여줘",
    }
    return [{"id":f"{fab}_{kind}", "kind":kind, "question":q.format(fab=fab)}
            for fab in ("fab10", "fab11", "fab12", "fab13")
            for kind, q in templates.items()]


def run_case(case, mode):
    started = time.monotonic()
    record = dict(case)
    try:
        if mode == "sql":
            from app.sub_agent.text2sql import answer_question
            response = json_value(answer_question(case["question"], execute=True))
        else:
            from app.agents.supervisor import Supervisor
            from app.schemas.chat import ChatRequest
            response = json_value(Supervisor().run(ChatRequest(message=case["question"])))
        record["response"] = response
        record["status"] = response["status"]
    except Exception as exc:  # noqa: BLE001 - preserve individual failures in the evaluation report
        record.update(status="error", error=f"{type(exc).__name__}: {exc}")
    record["seconds"] = round(time.monotonic() - started, 3)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mode", choices=["sql", "chat"], default="sql")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--freeze-source", action="store_true", help="Run an isolated source copy so concurrent development cannot change this evaluation")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for configured DB/model calls")
    selected = json.loads(args.fixture.read_text()) if args.fixture else cases()
    selected = [c for c in selected if not args.ids or c["id"] in args.ids]
    source = Path(__file__).resolve().parents[1] / "src"
    snapshot = None
    if args.freeze_source:
        snapshot = tempfile.TemporaryDirectory(prefix="fab_demo_eval_")
        copied_app = Path(snapshot.name) / "assistant"
        shutil.copytree(source, copied_app / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("data", "output"):
            (copied_app / name).symlink_to(source.parent / name, target_is_directory=True)
        source = copied_app / "src"
        sys.path.insert(0, str(source))
    report = {"started_at": datetime.now(UTC).isoformat(), "mode": args.mode,
              "source_frozen": bool(snapshot),
              "source_sha256": hashlib.sha256(b"".join(
                  p.read_bytes() for p in sorted(source.rglob("*.py")))).hexdigest(),
              "results": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
        futures = [pool.submit(run_case, c, args.mode) for c in selected]
        for future in as_completed(futures):
            row = future.result()
            report["results"].append(row)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            print(json.dumps({k: row[k] for k in ("id", "status", "seconds")}, ensure_ascii=False), flush=True)
    if snapshot:
        snapshot.cleanup()


if __name__ == "__main__":
    main()
