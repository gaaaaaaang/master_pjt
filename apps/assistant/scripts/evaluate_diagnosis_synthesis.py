"""Evaluate deterministic diagnosis calibration, taxonomy, and provenance contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from app.sub_agent.diagnosis import synthesize_diagnosis

DEFAULT_FIXTURE = APP_ROOT / "tests" / "fixtures" / "diagnosis_synthesis_eval.json"


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for case in cases:
        synthesis = synthesize_diagnosis(case["evidence"])
        metadata = synthesis["metadata"]
        reliability = metadata["reliability_assessment"]
        failures = []

        candidate_only_ok = (
            metadata.get("conclusion_level") == "candidate_only"
            and reliability.get("can_confirm_root_cause") is False
        )
        if not candidate_only_ok:
            failures.append("diagnosis must remain candidate_only and cannot confirm root cause")

        expected_issues = case["expected_candidate_issue_types"]
        taxonomy_ok = metadata.get("candidate_issue_types") == expected_issues
        if not taxonomy_ok:
            failures.append(
                f"candidate_issue_types expected={expected_issues} "
                f"actual={metadata.get('candidate_issue_types')}"
            )

        expected_corroboration = case["expected_corroboration_level"]
        provenance_ok = reliability.get("corroboration_level") == expected_corroboration
        if not provenance_ok:
            failures.append(
                f"corroboration expected={expected_corroboration} "
                f"actual={reliability.get('corroboration_level')}"
            )

        if metadata.get("source_coverage") != case["expected_source_coverage"]:
            failures.append("source coverage does not match expected evidence availability")
        if metadata.get("missing_evidence") != case["expected_missing_evidence"]:
            failures.append(
                f"missing_evidence expected={case['expected_missing_evidence']} "
                f"actual={metadata.get('missing_evidence')}"
            )
        eligibility_ok = metadata.get("excluded_evidence") == case.get(
            "expected_excluded_evidence",
            {
                "rag_without_diagnostic_issue": 0,
                "case_without_diagnostic_issue": 0,
            },
        )
        if not eligibility_ok:
            failures.append(
                f"excluded_evidence expected={case.get('expected_excluded_evidence')} "
                f"actual={metadata.get('excluded_evidence')}"
            )
        required = set(metadata.get("required_verification") or [])
        for item in case.get("expected_required_verification", []):
            if item not in required:
                failures.append(f"required verification missing: {item}")
        for fragment in case.get("expected_content_contains", []):
            if fragment.casefold() not in synthesis["content"].casefold():
                failures.append(f"content missing fragment: {fragment}")

        ranking_ok = metadata.get("ranked_candidate_issue_types") == case.get(
            "expected_ranked_candidate_issue_types",
            metadata.get("ranked_candidate_issue_types"),
        )
        if not ranking_ok:
            failures.append(
                "ranked candidates do not match expected evidence priority: "
                f"expected={case.get('expected_ranked_candidate_issue_types')} "
                f"actual={metadata.get('ranked_candidate_issue_types')}"
            )
        consistency_ok = reliability.get("evidence_consistency") == case.get(
            "expected_evidence_consistency",
            reliability.get("evidence_consistency"),
        )
        if not consistency_ok:
            failures.append(
                "evidence consistency expected="
                f"{case.get('expected_evidence_consistency')} "
                f"actual={reliability.get('evidence_consistency')}"
            )
        rankings = metadata.get("candidate_rankings") or []
        expected_support = case.get("expected_top_support_level")
        support_level_ok = expected_support is None or (
            bool(rankings) and rankings[0].get("support_level") == expected_support
        )
        if not support_level_ok:
            failures.append(
                f"top support level expected={expected_support} "
                f"actual={rankings[0].get('support_level') if rankings else None}"
            )

        results.append(
            {
                "id": case["id"],
                "passed": not failures,
                "candidate_only_ok": candidate_only_ok,
                "taxonomy_ok": taxonomy_ok,
                "provenance_ok": provenance_ok,
                "eligibility_ok": eligibility_ok,
                "ranking_ok": ranking_ok,
                "consistency_ok": consistency_ok,
                "support_level_ok": support_level_ok,
                "failures": failures,
            }
        )

    total = len(results)

    def rate(field: str) -> float:
        return round(sum(bool(item[field]) for item in results) / total, 4) if total else 0.0

    passed = sum(item["passed"] for item in results)
    return {
        "summary": {
            "case_count": total,
            "passed": passed,
            "contract_pass_rate": round(passed / total, 4) if total else 0.0,
            "candidate_only_rate": rate("candidate_only_ok"),
            "taxonomy_precision_rate": rate("taxonomy_ok"),
            "provenance_calibration_rate": rate("provenance_ok"),
            "evidence_eligibility_rate": rate("eligibility_ok"),
            "candidate_ranking_rate": rate("ranking_ok"),
            "conflict_calibration_rate": rate("consistency_ok"),
            "support_level_rate": rate("support_level_ok"),
            "failed_case_ids": [item["id"] for item in results if not item["passed"]],
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    report = evaluate_cases(json.loads(args.fixture.read_text(encoding="utf-8")))
    print(json.dumps(report if args.as_json else report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["contract_pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
