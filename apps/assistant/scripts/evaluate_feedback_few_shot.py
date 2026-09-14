"""Bounded live baseline/few-shot checks using synthetic evidence and an isolated DB."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.agents.llm_nodes import compose_with_llm
from app.agents.planner import PlannerDecision
from app.agents.usage import UsageLedger, usage_scope
from app.config import get_settings
from app.services.few_shot_service import FewShotService
from app.services.state_store import AssistantStateStore

CASES = [
    {
        "id": "current_wip", "question": "FAB11 현재 WIP 몇 개야?", "query_type": "status",
        "agents": ["text2sql"], "expected": "123", "forbidden": ["999", "2020-01-01"],
        "required_any": ["00:00", "00시", "0시"],
        "example": "2020-01-01 기준 FAB10 WIP은 999 LOT입니다. 기준 시각과 LOT 단위를 함께 확인하세요.",
        "evidence": [{"source_type": "text2sql_plan", "title": "Synthetic WIP fixture",
            "content": "테스트 데이터: FAB11 2026-09-13 00:00:00 KST WIP 123 LOT.",
            "metadata": {"status": "succeeded", "row_count": 1,
                "columns": ["fab_id", "interval_end", "wip_lots"],
                "sample_rows": [{"fab_id": "fab11", "interval_end": "2026-09-13T00:00:00+09:00", "wip_lots": 123}]}}],
    },
    {
        "id": "document_current_rule", "question": "CMP 점검 주기는 몇 분인가요?",
        "query_type": "knowledge_lookup", "agents": ["rag"],
        "expected": "7", "forbidden": ["999", "obsolete_manual"],
        "example": "CMP 점검 주기는 999분입니다. [obsolete_manual, p.99] 출처와 주기를 함께 제시합니다.",
        "evidence": [{"source_type": "rag_chunk", "title": "Synthetic current manual",
            "content": "이 문서는 테스트용 합성 문서입니다. CMP 점검 주기는 7분입니다.",
            "metadata": {"chunk_id": "synthetic_current_rule", "source_document": "synthetic_current_manual.txt", "page_number": 1}}],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=[case["id"] for case in CASES])
    parser.add_argument("--mode", choices=["baseline", "few-shot", "both"], default="both")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required before any model call")
    settings = get_settings()
    if urlsplit(settings.openai_endpoint).hostname != "skax.ai-talentlab.com":
        parser.error("This bounded evaluator only permits the configured project model host")
    ledger = UsageLedger()
    results = []
    with TemporaryDirectory(prefix="fab-feedback-eval-") as folder:
        store = AssistantStateStore(Path(folder) / "synthetic.sqlite3")
        retrieval = FewShotService(store)
        for case in CASES:
            store.append_exchange(conversation_id=case["id"], user_content=case["question"], user_metadata={},
                assistant_content=case["example"], assistant_metadata={"message_id": case["id"],
                    "query_type": case["query_type"], "status": "succeeded", "evidence": case["evidence"]})
            store.record_answer_feedback(conversation_id=case["id"], message_id=case["id"],
                                          helpful=True, comment="Synthetic test only", trace_id=None)
            store.review_example(case["id"], approve=True, reviewer="synthetic-test-fixture",
                note="Controlled stale-data probe in a temporary DB; not a real user vote or approval",
                question=case["question"], answer=case["example"])
        with usage_scope(ledger):
            for case in CASES:
                if args.case and case["id"] != args.case:
                    continue
                modes = {"baseline": (False,), "few-shot": (True,), "both": (False, True)}
                for enabled in modes[args.mode]:
                    diagnostics = {}
                    grounding = {}
                    examples = retrieval.select(case["question"], query_type=case["query_type"]) if enabled else []
                    try:
                        with patch("app.agents.llm_nodes.select_feedback_examples", return_value=examples):
                            answer = compose_with_llm(question=case["question"],
                                plan=PlannerDecision(status="ready", query_type=case["query_type"],
                                    intent=case["question"], selected_sub_agents=case["agents"], execution_steps=[]),
                                answer_parts=[], evidence=case["evidence"],
                                limitations=["테스트용 합성 데이터이며 실제 공장 기준이 아닙니다."],
                                reflection={}, diagnostics=diagnostics, grounding=grounding,
                                conversation_id="new-test-conversation")
                        passed = case["expected"] in answer and all(word not in answer for word in case["forbidden"])
                        if case.get("required_any"):
                            passed = passed and any(word in answer for word in case["required_any"])
                        results.append({"case": case["id"], "few_shot": enabled, "passed": passed,
                                        "answer": answer, "diagnostics": diagnostics, "grounding": grounding})
                    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                        results.append({"case": case["id"], "few_shot": enabled, "passed": False,
                                        "error_type": type(exc).__name__})
                    print(json.dumps({key: value for key, value in results[-1].items() if key != "answer"}), flush=True)
    ledger.cancel()
    report = {"synthetic_only": True, "production_state_modified": False,
              "results": results, "usage": ledger.snapshot()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
