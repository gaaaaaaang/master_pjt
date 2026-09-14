"""Inspect feedback, review reusable examples and export approved examples locally."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.config import get_settings
from app.services.feedback_repair import repair_legacy_feedback
from app.services.state_store import AssistantStateStore


def export_approved_examples(store: AssistantStateStore, output_path: Path) -> int:
    """Publish a complete export atomically and never overwrite the source DB/journal."""
    protected = [store.path, Path(str(store.path) + "-wal"), Path(str(store.path) + "-shm")]
    if any(output_path.resolve() == path.resolve() or (
        output_path.exists() and path.exists() and output_path.samefile(path)
    ) for path in protected):
        raise ValueError("Export output cannot overwrite the state database or its journal")
    examples = store.list_examples(status="approved", limit=None)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent,
                                         prefix=".feedback-export-", delete=False) as output:
            temporary = Path(output.name)
            for example in examples:
                output.write(json.dumps({"message_id": example["message_id"],
                    "query_type": example["query_type"], "messages": [
                        {"role": "user", "content": example["question"]},
                        {"role": "assistant", "content": example["answer"]},
                    ]}, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(examples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(get_settings().assistant_state_store_path))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stats")
    listing = commands.add_parser("list")
    listing.add_argument("--status", choices=["pending", "approved", "rejected", "excluded"])
    listing.add_argument("--limit", type=int, default=100)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("message_id")
    repair = commands.add_parser("repair-legacy")
    repair.add_argument("--trace-id", required=True)
    repair.add_argument("--question", required=True)
    repair.add_argument("--reason", required=True)
    repair.add_argument("--apply", action="store_true", help="Apply after reviewing the default dry run")
    for name in ("approve", "reject"):
        review = commands.add_parser(name)
        review.add_argument("message_id")
        review.add_argument("--reviewer", required=True)
        review.add_argument("--note", required=True)
        if name == "approve":
            review.add_argument("--source-review-note", help="Required for legacy answers without saved evidence; describe sources manually checked")
            review.add_argument("--question-file", type=Path, required=True,
                                help="Reviewed, self-contained, anonymized example question")
            review.add_argument("--answer-file", type=Path, required=True,
                                help="Reviewed reusable answer with private/stale details removed")
    export = commands.add_parser("export")
    export.add_argument("output", type=Path)
    args = parser.parse_args()
    store = AssistantStateStore(args.store)
    try:
        if args.command == "stats":
            result = store.feedback_stats()
        elif args.command == "list":
            result = store.list_examples(status=args.status, limit=args.limit)
        elif args.command == "inspect":
            with store._connect() as connection:
                row = connection.execute(
                    "SELECT COALESCE(r.history_json, f.history_json), f.helpful, f.comment, e.status, e.feedback_id "
                    ", e.question, e.answer, e.reviewed_by, e.review_note, e.reviewed_at "
                    "FROM feedback_examples e JOIN feedback f USING(feedback_id) "
                    "LEFT JOIN feedback_relinks r USING(feedback_id) WHERE e.message_id=?",
                    (args.message_id,),
                ).fetchone()
            if row is None:
                raise KeyError(args.message_id)
            result = {"history": json.loads(row[0]), "helpful": bool(row[1]), "comment": row[2],
                      "status": row[3], "feedback_id": row[4],
                      "example": {"question": row[5], "answer": row[6]},
                      "review": {"reviewer": row[7], "note": row[8], "reviewed_at": row[9]},
                      "review_history": store.example_history(args.message_id)}
        elif args.command == "repair-legacy":
            result = repair_legacy_feedback(store, trace_id=args.trace_id, question=args.question,
                                             reason=args.reason, apply=args.apply)
        elif args.command == "export":
            count = export_approved_examples(store, args.output)
            result = {"exported": count, "output": str(args.output)}
        else:
            approve = args.command == "approve"
            store.review_example(args.message_id, approve=approve, reviewer=args.reviewer,
                                 note=args.note,
                                 question=args.question_file.read_text() if approve else None,
                                 answer=args.answer_file.read_text() if approve else None,
                                 source_review_note=args.source_review_note if approve else None)
            result = {"message_id": args.message_id, "status": "approved" if approve else "rejected"}
    except (KeyError, ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
