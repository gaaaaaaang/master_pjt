"""Offline evidence-unit retrieval evaluation with reproducible corpus and fixture digests.

Recall is measured over required evidence units, not the number of incidental matching chunks.
nDCG uses binary chunk relevance from the full corpus; required-unit recall separately measures coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import types
from pathlib import Path
from time import perf_counter

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))
from app.sub_agent.rag import retrieve_knowledge


def covered_units(content, markers):
    primary_ids = set(re.findall(r"playbook_id\s+(PB-[A-Z]+-\d+)", content))
    return {
        m
        for m in markers
        if (m in primary_ids if m.startswith("PB-") else m.casefold() in content.casefold())
    }


def summarize(rows):
    answerable = [r for r in rows if r["answerable"]]
    unknown = [r for r in rows if not r["answerable"]]

    def mean(field, subset):
        return sum(r[field] for r in subset) / len(subset) if subset else None

    return {
        "hit_rate": mean("hit", answerable),
        "recall_at_k": mean("coverage", answerable),
        "mrr": mean("rr", answerable),
        "ndcg": mean("ndcg", answerable),
        "complete_evidence_rate": mean("complete", answerable),
        "abstention_accuracy": mean("abstained", unknown),
        "answerable_count": len(answerable),
        "unanswerable_count": len(unknown),
    }


def evaluate(cases, *, store_path, top_k=3, retrieve_fn=retrieve_knowledge):
    if top_k < 1:
        raise ValueError("top_k must be positive.")
    corpus = [json.loads(line) for line in store_path.read_text().splitlines() if line.strip()]
    rows = []
    for case in cases:
        start = perf_counter()
        evidence = retrieve_fn(
            case["query"], top_k, knowledge_base=case["knowledge_base"], store_path=store_path
        )
        expected = set(case["expected_markers"])
        relevant_ids = {
            c["chunk_id"]
            for c in corpus
            if c["knowledge_base"] == case["knowledge_base"]
            and covered_units(c["content"], expected)
        }
        gains = []
        seen_ids = set()
        found = set()
        first_rank = 0
        for rank, item in enumerate(evidence, 1):
            units = covered_units(item.content, expected)
            if units and not first_rank:
                first_rank = rank
            cid = item.metadata["chunk_id"]
            gains.append(int(cid in relevant_ids and cid not in seen_ids))
            seen_ids.add(cid)
            found.update(units)
        dcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))
        ideal = sum(1 / math.log2(rank + 2) for rank in range(min(len(relevant_ids), top_k)))
        rows.append(
            {
                "id": case["id"],
                "split": case.get("split", "unspecified"),
                "query": case["query"],
                "answerable": bool(expected),
                "hit": bool(first_rank),
                "coverage": len(found) / len(expected) if expected else 0,
                "complete": bool(expected) and found == expected,
                "rr": 1 / first_rank if first_rank else 0,
                "ndcg": dcg / ideal if ideal else 0,
                "abstained": not evidence,
                "missing_units": sorted(expected - found),
                "chunks": [e.metadata["chunk_id"] for e in evidence],
                "latency_ms": round((perf_counter() - start) * 1000, 3),
            }
        )
    return {
        "evaluation_version": "evidence_units.v2",
        "top_k": top_k,
        "case_count": len(rows),
        "metrics": summarize(rows),
        "by_split": {
            split: summarize([r for r in rows if r["split"] == split])
            for split in sorted({r["split"] for r in rows})
        },
        "cases": rows,
    }


def baseline_retriever(ref):
    if not re.fullmatch(r"[0-9a-f]{7,40}", ref):
        raise ValueError("Use an immutable commit hash for baseline evaluation.")
    source = subprocess.run(
        ["git", "show", f"{ref}:apps/assistant/src/app/sub_agent/rag.py"],
        cwd=APP_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    module = types.ModuleType("rag_baseline")
    # Execute only the explicitly selected immutable baseline from this trusted repository.
    exec(compile(source, f"git:{ref}:rag.py", "exec"), module.__dict__)  # noqa: S102
    return module.retrieve_knowledge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", type=Path, default=APP_ROOT / "tests/fixtures/rag_search_eval.json"
    )
    parser.add_argument(
        "--store-path", type=Path, default=APP_ROOT / "output/rag/master_pjt_v2.jsonl"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--baseline-ref")
    args = parser.parse_args()
    retrieve_fn = baseline_retriever(args.baseline_ref) if args.baseline_ref else retrieve_knowledge
    report = evaluate(
        json.loads(args.fixture.read_text()),
        store_path=args.store_path,
        top_k=args.top_k,
        retrieve_fn=retrieve_fn,
    )
    report.update(
        {
            "backend": "local_offline",
            "baseline_ref": args.baseline_ref,
            "fixture_digest": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
            "corpus_digest": hashlib.sha256(args.store_path.read_bytes()).hexdigest(),
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
