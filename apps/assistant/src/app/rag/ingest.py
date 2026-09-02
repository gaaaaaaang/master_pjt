from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import get_settings

SUPPORTED_SUFFIXES = {".md", ".pdf", ".txt", ".docx"}
DEFAULT_CHUNK_TARGET_CHARS = 2400
DEFAULT_CHUNK_OVERLAP_CHARS = 300


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    collection: str
    knowledge_base: str
    source: str
    title: str
    content: str
    metadata: dict[str, str] = field(default_factory=dict)


def ingest_documents(
    input_dir: Path,
    collection: str,
    *,
    knowledge_base: str = "process_basics",
    output_path: Path | None = None,
    chunk_target_chars: int = DEFAULT_CHUNK_TARGET_CHARS,
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> int:
    """Extract local documents into the deterministic RAG chunk JSONL store."""
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_dir}")

    output_path = output_path or Path(get_settings().rag_local_store_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    chunks = list(
        _iter_chunks(
            input_dir,
            collection,
            knowledge_base=knowledge_base,
            chunk_target_chars=chunk_target_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )
    )
    write_chunks(output_path, chunks)
    return len(chunks)


def build_chunks_for_paths(
    paths: list[Path],
    *,
    collection: str,
    knowledge_base: str,
    chunk_target_chars: int = DEFAULT_CHUNK_TARGET_CHARS,
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for path in paths:
        if path.is_dir():
            chunks.extend(
                _iter_chunks(
                    path,
                    collection,
                    knowledge_base=knowledge_base,
                    chunk_target_chars=chunk_target_chars,
                    chunk_overlap_chars=chunk_overlap_chars,
                )
            )
        elif path.is_file():
            chunks.extend(
                _chunks_for_file(
                    path,
                    collection,
                    knowledge_base=knowledge_base,
                    chunk_target_chars=chunk_target_chars,
                    chunk_overlap_chars=chunk_overlap_chars,
                )
            )
        else:
            raise FileNotFoundError(f"RAG input path does not exist: {path}")
    return chunks


def write_chunks(output_path: Path, chunks: list[KnowledgeChunk]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(asdict(chunk), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def load_chunks(input_path: Path) -> list[dict]:
    if not input_path.exists():
        raise FileNotFoundError(f"RAG chunk store does not exist: {input_path}")
    chunks = []
    with input_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def _iter_chunks(
    input_dir: Path,
    collection: str,
    *,
    knowledge_base: str,
    chunk_target_chars: int,
    chunk_overlap_chars: int,
) -> Iterable[KnowledgeChunk]:
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        yield from _chunks_for_file(
            path,
            collection,
            knowledge_base=knowledge_base,
            chunk_target_chars=chunk_target_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )


def _chunks_for_file(
    path: Path,
    collection: str,
    *,
    knowledge_base: str,
    chunk_target_chars: int,
    chunk_overlap_chars: int,
) -> list[KnowledgeChunk]:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return []
    text = _extract_text(path)
    if not text:
        return []
    title = path.stem
    base_metadata = _infer_metadata(path, text)
    chunks = []
    for index, content in enumerate(_split_text(text, chunk_target_chars, chunk_overlap_chars)):
        metadata = {**base_metadata, "chunk_index": str(index)}
        chunks.append(
            KnowledgeChunk(
                chunk_id=_chunk_id(collection, knowledge_base, path, index, content),
                collection=collection,
                knowledge_base=knowledge_base,
                source=str(path),
                title=title,
                content=content,
                metadata=metadata,
            )
        )
    return chunks


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return _clean_text(path.read_text(encoding="utf-8", errors="ignore"))
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    return ""


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required to ingest PDF files. Install the rag extra.") from exc

    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return _clean_text("\n".join(pages))


def _extract_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("python-docx is required to ingest DOCX files. Install the rag extra.") from exc

    document = Document(path)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    return _clean_text("\n".join(paragraphs))


def _split_text(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    if target_chars <= 0:
        raise ValueError("chunk_target_chars must be positive.")
    if overlap_chars < 0 or overlap_chars >= target_chars:
        raise ValueError("chunk_overlap_chars must be non-negative and smaller than target.")

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= target_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = paragraph
        while len(current) > target_chars:
            chunks.append(current[:target_chars].strip())
            current = current[target_chars - overlap_chars :].strip()
    if current:
        chunks.append(current)
    return chunks


def _infer_metadata(path: Path, text: str) -> dict[str, str]:
    lower = f"{path.name}\n{text[:2000]}".casefold()
    metadata: dict[str, str] = {
        "source_document": path.name,
        "source_type": path.suffix.lower().lstrip("."),
    }
    for issue_type in ("queue_time", "wip", "bottleneck", "breakdown", "pm", "yield"):
        if issue_type.replace("_", " ") in lower or issue_type in lower:
            metadata["issue_type"] = issue_type
            break
    fab_match = re.search(r"\bfab(?:[-_ ]?)(1[0-3])\b", lower)
    if fab_match:
        metadata["fab_id"] = f"fab{fab_match.group(1)}"
    return metadata


def _chunk_id(collection: str, knowledge_base: str, path: Path, index: int, content: str) -> str:
    digest = hashlib.sha256(
        f"{collection}:{knowledge_base}:{path}:{index}:{content}".encode()
    ).hexdigest()
    return digest[:24]


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
