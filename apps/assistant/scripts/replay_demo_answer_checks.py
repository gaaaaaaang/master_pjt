"""Replay saved answers through today's deterministic verifier without model/DB calls."""
import argparse
import json
from collections import Counter
from pathlib import Path

from app.sub_agent.reflection import verify_response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checks = []
    for path in args.reports:
        for case in json.loads(path.read_text()).get("results", []):
            response = case.get("response") or {}
            if not response.get("answer") or not isinstance(response.get("evidence"), list):
                continue
            if any(not isinstance(item, dict) for item in response["evidence"]):
                continue  # Older repr-only artifacts cannot be independently replayed.
            result = verify_response(response["answer"], question=case["question"],
                                     query_type=response.get("query_type"), evidence=response["evidence"],
                                     limitations=response.get("limitations", []))
            checks.append({"report":path.name, "id":case["id"], "original_status":response.get("status"),
                           "current_verifier_supported":result["is_supported"], "warnings":result["warnings"]})
    report = {"scope":"Offline deterministic recheck of saved answers; does not change original live status or validate every semantic claim.",
              "total":len(checks), "currently_supported":sum(row["current_verifier_supported"] for row in checks),
              "remaining_warnings":dict(Counter(warning for row in checks for warning in row["warnings"])), "checks":checks}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key:report[key] for key in ("total", "currently_supported", "remaining_warnings")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
