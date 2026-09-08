"""Audit saved chat citations against the canonical corpus, without model/API calls.

This checks text and provenance only. It cannot establish semantic entailment or
answer completeness; those require separate, explicitly recorded review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.rag.grounding import normalized
from app.rag.manifest import atomic_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus_bytes, report_bytes = args.corpus.read_bytes(), args.report.read_bytes()
    rows = [json.loads(line) for line in corpus_bytes.splitlines() if line.strip()]
    corpus = {row["chunk_id"]: row for row in rows}
    if len(corpus) != len(rows):
        raise ValueError("Canonical corpus has duplicate chunk IDs.")
    results = []
    for case in json.loads(report_bytes)["cases"]:
        final = case.get("final") or case.get("result") or {}
        issues = []
        citations = final.get("citations", [])
        for number, citation in enumerate(citations, start=1):
            source = corpus.get(citation.get("chunk_id"))
            if not source:
                issues.append({"citation": number, "issue": "unknown_chunk"})
                continue
            for field in ("source_document", "page_number"):
                if citation.get(field) != source["metadata"].get(field):
                    issues.append({"citation": number, "issue": f"mismatched_{field}"})
            quote = citation.get("quote")
            if not isinstance(quote, str) or not quote.strip() or normalized(quote) not in normalized(source["content"]):
                issues.append({"citation": number, "issue": "quote_not_in_corpus"})
            if citation.get("number") != number:
                issues.append({"citation": number, "issue": "nonsequential_label"})
        for evidence in final.get("evidence", []):
            if evidence.get("source_type") != "rag_chunk":
                continue
            cid = evidence.get("metadata", {}).get("chunk_id")
            if cid not in corpus or evidence.get("content") != corpus[cid]["content"]:
                issues.append({"chunk_id": cid, "issue": "evidence_differs_from_corpus"})
        results.append({"id": case["id"], "citations": len(citations), "issues": issues})
    report = {
        "audit": "canonical_source_fidelity.v1",
        "scope": "Literal quotation and source metadata; not semantic correctness or completeness.",
        "input_report": str(args.report),
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "cases": results,
        "total_citations": sum(row["citations"] for row in results),
        "passed": all(not row["issues"] for row in results),
    }
    atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("passed", "total_citations")}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
