from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[1] / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from app.sub_agent.rag import (
    INCIDENT_PLAYBOOK,
    PROCESS_BASICS,
    retrieve_incident_playbook,
    retrieve_knowledge,
    retrieve_process_basics,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local RAG agent retrieval smoke check.")
    parser.add_argument("query", nargs="?", default="왜 fab10 Queue Time이 늘었어?")
    parser.add_argument(
        "--agent",
        choices=["auto", INCIDENT_PLAYBOOK, PROCESS_BASICS],
        default="auto",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--store-path", default="apps/assistant/output/rag/master_pjt.jsonl")
    args = parser.parse_args()

    store_path = Path(args.store_path)
    if args.agent == INCIDENT_PLAYBOOK:
        evidence = retrieve_incident_playbook(args.query, top_k=args.top_k, store_path=store_path)
    elif args.agent == PROCESS_BASICS:
        evidence = retrieve_process_basics(args.query, top_k=args.top_k, store_path=store_path)
    else:
        evidence = retrieve_knowledge(args.query, top_k=args.top_k, store_path=store_path)

    payload = [
        {
            "rank": index,
            "title": item.title,
            "source_type": item.source_type,
            "preview": item.content[:300],
            "metadata": item.metadata,
        }
        for index, item in enumerate(evidence, start=1)
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
