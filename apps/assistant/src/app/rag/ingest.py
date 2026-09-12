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
    metadata: dict = field(default_factory=dict)


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
    from app.rag.manifest import atomic_write

    text = "".join(
        json.dumps(asdict(chunk), ensure_ascii=False, sort_keys=True) + "\n" for chunk in chunks
    )
    atomic_write(output_path, text)


def load_chunks(input_path: Path) -> list[dict]:
    if not input_path.exists():
        raise FileNotFoundError(f"RAG chunk store does not exist: {input_path}")
    chunks = []
    with input_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    validate_chunks(chunks)
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
    units = _extract_units(path)
    document_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    title = path.stem
    chunks = []
    index = 0
    document_text = "\n".join(text for _, _, text in units)
    scope_match = re.search(r"(?i)fab(\d+)\s*[-–~]\s*fab(\d+)", document_text[:3000])
    document_fabs = []
    if scope_match:
        first, last = map(int, scope_match.groups())
        if 0 <= last - first <= 20:
            document_fabs = [f"fab{number}" for number in range(first, last + 1)]
    simulation = "simulation" in document_text.casefold() or "시뮬레이션" in document_text
    reference_summary = "프로젝트 참고 문서" in document_text[:800]
    for page, section, text in units:
        for content in _split_text(text, chunk_target_chars, chunk_overlap_chars):
            metadata = _infer_metadata(path, content)
            if document_fabs:
                metadata["fab_ids"] = document_fabs
            metadata.update(
                {
                    "chunk_index": str(index),
                    "page_number": page,
                    "section_title": section,
                    "document_version": document_hash,
                    "ingestion_version": "structure.v2",
                    "reliability": "reference_summary" if reference_summary else "simulation_reference" if simulation else "unverified_reference",
                    "playbook_ids": list(dict.fromkeys(re.findall(r"\bPB-[A-Z]+-\d+\b", content))),
                }
            )
            from app.rag.query import concepts

            metadata["concepts"] = sorted(concepts(content))
            chunks.append(
                KnowledgeChunk(
                    chunk_id=_chunk_id(collection, knowledge_base, path, index, content),
                    collection=collection,
                    knowledge_base=knowledge_base,
                    source=str(path),
                    title=section or title,
                    content=content,
                    metadata=metadata,
                )
            )
            index += 1
    return chunks


def _extract_units(path: Path) -> list[tuple[int | None, str, str]]:
    """Preserve PDF pages and Markdown headings before bounded character splitting."""
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return [
            (number, _page_title(text, path.stem), text)
            for number, page in enumerate(PdfReader(path).pages, 1)
            if (text := _clean_text(page.extract_text() or ""))
        ]
    text = _extract_text(path)
    if not text:
        return []
    if path.suffix.lower() == ".md":
        parts = re.split(r"(?m)(?=^#{1,6}\s+)", text)
        return [
            (None, part.splitlines()[0].lstrip("# "), part.strip())
            for part in parts
            if part.strip()
        ]
    return [(None, path.stem, text)]


def _page_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if re.match(r"^\d{1,2}\.\s+", line.strip()):
            return line.strip()
    return fallback


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
        raise RuntimeError(
            "python-docx is required to ingest DOCX files. Install the rag extra."
        ) from exc

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
    lower = f"{path.name}\n{text}".casefold()
    metadata: dict[str, str] = {
        "source_document": path.name,
        "source_type": path.suffix.lower().lstrip("."),
    }
    explicit_issue_types = list(
        dict.fromkeys(re.findall(r"issue_type\s+([a-z0-9_/-]+)", lower))
    )
    if explicit_issue_types:
        metadata["issue_type"] = explicit_issue_types[0]
        metadata["issue_types"] = ",".join(explicit_issue_types)
    else:
        for issue_type, terms in _ISSUE_METADATA_TERMS.items():
            if any(term in lower for term in terms):
                metadata["issue_type"] = issue_type
                metadata["issue_types"] = issue_type
                break
    fab_match = re.search(r"\bfab(?:[-_ ]?)(1[0-3])\b", lower)
    if fab_match:
        metadata["fab_id"] = f"fab{fab_match.group(1)}"
    return metadata


_ISSUE_METADATA_TERMS = {
    "queue_time": ("queue time", "queue_time", "대기 시간", "대기시간"),
    "bottleneck": ("bottleneck", "병목"),
    "breakdown": ("breakdown", "equipment down", "장비 고장", "설비 고장"),
    "pm": ("preventive maintenance", "pm 지연", "예방 정비", "예방정비"),
    "yield": ("yield", "수율"),
    "wip": ("wip", "재공"),
}


def _chunk_id(collection: str, knowledge_base: str, path: Path, index: int, content: str) -> str:
    digest = hashlib.sha256(
        f"{collection}:{knowledge_base}:{path.name}:{index}:{content}".encode()
    ).hexdigest()
    return digest[:24]


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def validate_chunks(chunks: list[dict]) -> None:
    seen = set()
    for number, chunk in enumerate(chunks, 1):
        if not isinstance(chunk, dict):
            raise TypeError(f"Corpus record {number} must be an object.")
        cid = chunk.get("chunk_id")
        if not isinstance(cid, str) or not cid or cid in seen:
            raise ValueError(f"Corpus record {number} has an invalid or duplicate chunk ID.")
        seen.add(cid)
        if chunk.get("knowledge_base") not in {"incident_playbook", "process_basics"}:
            raise ValueError(f"Corpus record {number} has an unknown knowledge base.")
        if not isinstance(chunk.get("content"), str) or not chunk["content"].strip():
            raise ValueError(f"Corpus record {number} has no text content.")
        if not isinstance(chunk.get("metadata", {}), dict):
            raise TypeError(f"Corpus record {number} metadata must be an object.")
