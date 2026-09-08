import json
from pathlib import Path

import pytest
from app.config import get_settings
from app.rag.ingest import build_chunks_for_paths, ingest_documents
from app.rag.milvus_store import ensure_collection, insert_chunks, search_chunks
from app.sub_agent.rag import (
    INCIDENT_PLAYBOOK,
    PROCESS_BASICS,
    retrieve_incident_playbook,
    retrieve_knowledge,
    retrieve_process_basics,
)


def test_ingest_documents_writes_chunk_jsonl(tmp_path: Path) -> None:
    input_dir = tmp_path / "docs"
    input_dir.mkdir()
    (input_dir / "queue_time_playbook.txt").write_text(
        """
        fab10 queue_time 대응 절차

        Queue Time이 증가하면 병목 설비, WIP 증가, breakdown 이력을 함께 확인한다.
        병목 공정 앞 대기 건수가 늘면 downstream lead time 영향도 같이 기록한다.
        """,
        encoding="utf-8",
    )
    output_path = tmp_path / "rag" / "fab_knowledge.jsonl"

    count = ingest_documents(
        input_dir,
        "master_pjt",
        knowledge_base=INCIDENT_PLAYBOOK,
        output_path=output_path,
        chunk_target_chars=180,
        chunk_overlap_chars=20,
    )

    assert count >= 1
    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["collection"] == "master_pjt"
    assert records[0]["knowledge_base"] == INCIDENT_PLAYBOOK
    assert records[0]["metadata"]["fab_id"] == "fab10"
    assert records[0]["metadata"]["issue_type"] == "queue_time"
    assert records[0]["metadata"]["source_document"] == "queue_time_playbook.txt"
    assert "Queue Time" in records[0]["content"]


def test_ingest_infers_issue_metadata_per_chunk_instead_of_whole_document(tmp_path: Path) -> None:
    path = tmp_path / "playbook.txt"
    path.write_text(
        "issue_type\nbreakdown\n장비 고장 대응\n\n"
        "issue_type\nqueue_time_risk\nQueue Time 위반 대응",
        encoding="utf-8",
    )

    chunks = build_chunks_for_paths(
        [path],
        collection="test",
        knowledge_base=INCIDENT_PLAYBOOK,
        chunk_target_chars=45,
        chunk_overlap_chars=0,
    )

    assert chunks[0].metadata["issue_type"] == "breakdown"
    assert chunks[-1].metadata["issue_type"] == "queue_time_risk"


def test_chunk_ids_are_stable_across_absolute_source_directories(tmp_path: Path) -> None:
    first = tmp_path / "one" / "playbook.txt"
    second = tmp_path / "two" / "playbook.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("Queue Time 대응 절차", encoding="utf-8")
    second.write_text("Queue Time 대응 절차", encoding="utf-8")

    first_chunks = build_chunks_for_paths(
        [first], collection="test", knowledge_base=INCIDENT_PLAYBOOK
    )
    second_chunks = build_chunks_for_paths(
        [second], collection="test", knowledge_base=INCIDENT_PLAYBOOK
    )

    assert first_chunks[0].chunk_id == second_chunks[0].chunk_id


def test_retrieve_knowledge_returns_ranked_evidence(tmp_path: Path) -> None:
    store_path = tmp_path / "store.jsonl"
    records = [
        {
            "chunk_id": "queue-time-1",
            "collection": "master_pjt",
            "knowledge_base": INCIDENT_PLAYBOOK,
            "source": "queue.txt",
            "title": "Queue Time playbook",
            "content": "Queue Time 증가 시 병목 설비와 WIP 증가를 함께 확인한다.",
            "metadata": {"issue_type": "queue_time"},
        },
        {
            "chunk_id": "pm-1",
            "collection": "master_pjt",
            "knowledge_base": PROCESS_BASICS,
            "source": "pm.txt",
            "title": "PM checklist",
            "content": "정기 PM 일정과 점검 항목을 관리한다.",
            "metadata": {"issue_type": "pm"},
        },
    ]
    store_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    evidence = retrieve_knowledge("왜 Queue Time이 늘었어? WIP도 봐줘", store_path=store_path)

    assert evidence
    assert evidence[0].source_type == "rag_chunk"
    assert evidence[0].metadata["chunk_id"] == "queue-time-1"
    assert evidence[0].metadata["knowledge_base"] == INCIDENT_PLAYBOOK
    assert evidence[0].metadata["source_document"] == "queue.txt"
    assert evidence[0].metadata["score"] > 0


def test_retrieval_deprioritizes_table_of_contents_matches(tmp_path: Path) -> None:
    store_path = tmp_path / "store.jsonl"
    records = [
        {
            "chunk_id": "toc",
            "collection": "master_pjt",
            "knowledge_base": PROCESS_BASICS,
            "source": "manual.pdf",
            "title": "Manual",
            "content": (
                "Critical Queue Time .......... 13\n"
                "Reports ...................... 14\n"
                "Setup ........................ 15"
            ),
            "metadata": {},
        },
        {
            "chunk_id": "body",
            "collection": "master_pjt",
            "knowledge_base": PROCESS_BASICS,
            "source": "manual.pdf",
            "title": "Critical Queue Time",
            "content": "Critical Queue Time requires a custom dispatching function.",
            "metadata": {"issue_type": "queue_time"},
        },
    ]
    store_path.write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )

    evidence = retrieve_knowledge(
        "critical queue time 구현",
        knowledge_base=PROCESS_BASICS,
        store_path=store_path,
    )

    assert evidence[0].metadata["chunk_id"] == "body"


def test_rag_auto_router_ignores_negated_incident_when_requesting_process_basics(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "store.jsonl"
    records = [
        {
            "chunk_id": "incident",
            "collection": "test",
            "knowledge_base": INCIDENT_PLAYBOOK,
            "source": "incident.txt",
            "title": "장비 고장 대응",
            "content": "장비 고장과 down 복구 절차",
            "metadata": {"issue_type": "equipment_down"},
        },
        {
            "chunk_id": "cmp-basics",
            "collection": "test",
            "knowledge_base": PROCESS_BASICS,
            "source": "cmp.txt",
            "title": "CMP 공정 기초",
            "content": "CMP는 웨이퍼 평탄화 공정입니다.",
            "metadata": {"issue_type": "all"},
        },
    ]
    store_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    evidence = retrieve_knowledge(
        "장비 고장은 아니고 CMP 공정을 설명해줘",
        store_path=store_path,
    )

    assert [item.metadata["chunk_id"] for item in evidence] == ["cmp-basics"]
    assert evidence[0].metadata["knowledge_base"] == PROCESS_BASICS


def test_retrieve_knowledge_reports_missing_store(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="RAG store has no chunks"):
        retrieve_knowledge("Queue Time", store_path=tmp_path / "missing.jsonl")


def test_rag_abstains_when_only_identifier_or_no_semantic_terms_match(tmp_path: Path) -> None:
    store_path = tmp_path / "store.jsonl"
    record = {
        "chunk_id": "fab10-down",
        "collection": "master_pjt",
        "knowledge_base": INCIDENT_PLAYBOOK,
        "source": "down.txt",
        "title": "fab10 장비 고장",
        "content": "fab10 equipment down 대응 절차",
        "metadata": {"fab_id": "fab10", "issue_type": "equipment_down"},
    }
    store_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    assert (
        retrieve_knowledge(
            "fab10 담당자 전화번호 알려줘",
            knowledge_base=INCIDENT_PLAYBOOK,
            store_path=store_path,
        )
        == []
    )
    assert (
        retrieve_knowledge(
            "오늘 식당 메뉴 알려줘",
            knowledge_base=INCIDENT_PLAYBOOK,
            store_path=store_path,
        )
        == []
    )


def test_incident_rag_filters_explicit_issue_mismatch_and_reads_all_chunk_declarations(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "store.jsonl"
    records = [
        {
            "chunk_id": "pm-only",
            "collection": "master_pjt",
            "knowledge_base": INCIDENT_PLAYBOOK,
            "source": "playbook.txt",
            "title": "설비 PM과 대기 증가",
            "content": "issue_type pm_delay\n설비 PM 지연으로 queue와 WIP가 증가한다.",
            "metadata": {"issue_type": "pm_delay"},
        },
        {
            "chunk_id": "combined-queue",
            "collection": "master_pjt",
            "knowledge_base": INCIDENT_PLAYBOOK,
            "source": "playbook.txt",
            "title": "Hot lot과 Queue Time",
            "content": (
                "issue_type hot_lot\n긴급 lot 대응\n"
                "issue_type queue_time_risk\nQueue Time 증가와 대기 lot 대응"
            ),
            "metadata": {"issue_type": "hot_lot"},
        },
    ]
    store_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    evidence = retrieve_incident_playbook(
        "Queue Time 증가와 대기 lot 대응",
        top_k=5,
        store_path=store_path,
    )

    assert [item.metadata["chunk_id"] for item in evidence] == ["combined-queue"]
    assert evidence[0].metadata["declared_issue_types"] == ["hot_lot", "queue_time_risk"]
    assert evidence[0].metadata["query_issue_intents"] == ["queue_time"]
    assert evidence[0].metadata["matched_issue_types"] == ["queue_time_risk"]
    assert evidence[0].metadata["issue_aligned"] is True


def test_retrieve_agents_keep_playbook_and_basics_separate(tmp_path: Path) -> None:
    store_path = tmp_path / "store.jsonl"
    records = [
        {
            "chunk_id": "incident-1",
            "collection": "master_pjt",
            "knowledge_base": INCIDENT_PLAYBOOK,
            "source": "playbook.txt",
            "title": "장비 고장 대응",
            "content": "장비 고장과 병목 발생 시 lot hold와 escalation을 검토한다.",
            "metadata": {"issue_type": "breakdown"},
        },
        {
            "chunk_id": "basics-1",
            "collection": "master_pjt",
            "knowledge_base": PROCESS_BASICS,
            "source": "basics.txt",
            "title": "Photo 공정 기본",
            "content": "Photo lithography 공정은 패턴을 웨이퍼에 전사하는 단계다.",
            "metadata": {"issue_type": "process_basics"},
        },
    ]
    store_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    incident = retrieve_incident_playbook("장비 고장 대응은?", store_path=store_path)
    basics = retrieve_process_basics("Photo lithography가 뭐야?", store_path=store_path)

    assert incident[0].metadata["chunk_id"] == "incident-1"
    assert basics[0].metadata["chunk_id"] == "basics-1"


def test_build_chunks_for_paths_accepts_files(tmp_path: Path) -> None:
    path = tmp_path / "basics.txt"
    path.write_text("CMP 공정은 wafer 평탄화 단계다.", encoding="utf-8")

    chunks = build_chunks_for_paths([path], collection="master_pjt", knowledge_base=PROCESS_BASICS)

    assert len(chunks) == 1
    assert chunks[0].knowledge_base == PROCESS_BASICS
    assert chunks[0].collection == "master_pjt"


def test_ensure_collection_creates_master_pjt_by_default(monkeypatch) -> None:
    monkeypatch.setenv("VECTOR_DB_COLLECTION", "master_pjt")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "3072")
    get_settings.cache_clear()

    client = FakeMilvusClient(existing=set())

    result = ensure_collection(uri="milvus_demo.db", client=client)

    assert result == {
        "uri": "milvus_demo.db",
        "collection_name": "master_pjt",
        "dimension": 3072,
        "created": True,
        "recreated": False,
    }
    assert client.created == [("master_pjt", "schema")]
    get_settings.cache_clear()


def test_ensure_collection_can_recreate_existing_collection() -> None:
    client = FakeMilvusClient(existing={"master_pjt"})

    result = ensure_collection(
        uri="http://localhost:19530",
        collection_name="master_pjt",
        dimension=3072,
        recreate=True,
        client=client,
    )

    assert result["created"] is True
    assert result["recreated"] is True
    assert client.dropped == ["master_pjt"]
    assert client.created == [("master_pjt", "schema")]


def test_insert_chunks_embeds_and_inserts_by_knowledge_base() -> None:
    client = FakeMilvusClient(existing={"master_pjt"})
    embedding_client = FakeEmbeddingClient(dimension=3)
    chunks = [
        {
            "chunk_id": "incident-1",
            "collection": "master_pjt",
            "knowledge_base": INCIDENT_PLAYBOOK,
            "source": "playbook.txt",
            "title": "Queue Time 대응",
            "content": "Queue Time 증가 대응 절차",
            "metadata": {"issue_type": "queue_time"},
        }
    ]

    result = insert_chunks(
        chunks,
        uri="http://localhost:19530",
        collection_name="master_pjt",
        dimension=3,
        client=client,
        embedding_client=embedding_client,
    )

    assert result["inserted"] == 1
    assert result["mode"] == "upsert"
    assert result["row_count"] == 1
    assert isinstance(client.inserted[0]["id"], int)
    assert client.inserted[0]["chunk_id"] == "incident-1"
    assert client.inserted[0]["knowledge_base"] == INCIDENT_PLAYBOOK
    assert client.inserted[0]["vector"] == [0.1, 0.2, 0.3]


def test_search_chunks_filters_by_knowledge_base() -> None:
    client = FakeMilvusClient(existing={"master_pjt"})
    embedding_client = FakeEmbeddingClient(dimension=3)
    client.search_result = [
        [
            {
                "id": "basics-1",
                "distance": 0.87,
                "entity": {
                    "knowledge_base": PROCESS_BASICS,
                    "collection": "master_pjt",
                    "chunk_id": "basics-1",
                    "source": "basics.txt",
                    "title": "CMP 기본",
                    "content": "CMP는 wafer 평탄화 공정이다.",
                    "metadata_json": '{"issue_type":"process_basics"}',
                },
            }
        ]
    ]

    chunks = search_chunks(
        "CMP가 뭐야?",
        knowledge_base=PROCESS_BASICS,
        top_k=1,
        uri="http://localhost:19530",
        collection_name="master_pjt",
        client=client,
        embedding_client=embedding_client,
    )

    assert client.last_filter == f'knowledge_base == "{PROCESS_BASICS}"'
    assert chunks[0]["chunk_id"] == "basics-1"
    assert chunks[0]["metadata"]["source_document"] == "basics.txt"
    assert chunks[0]["metadata"]["score"] == 0.87


class FakeMilvusClient:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.created: list[tuple[str, int]] = []
        self.dropped: list[str] = []
        self.inserted: list[dict] = []
        self.upserted: list[dict] = []
        self.search_result: list[list[dict]] = []
        self.last_filter: str | None = None

    def has_collection(self, *, collection_name: str) -> bool:
        return collection_name in self.existing

    def drop_collection(self, *, collection_name: str) -> None:
        self.existing.discard(collection_name)
        self.dropped.append(collection_name)

    def create_collection(self, *, collection_name: str, schema, index_params) -> None:
        del schema, index_params
        self.existing.add(collection_name)
        self.created.append((collection_name, "schema"))

    def insert(self, *, collection_name: str, data: list[dict]) -> dict:
        del collection_name
        self.inserted.extend(data)
        return {"insert_count": len(data)}

    def upsert(self, *, collection_name: str, data: list[dict]) -> dict:
        del collection_name
        self.upserted.extend(data)
        self.inserted.extend(data)
        return {"upsert_count": len(data)}

    def flush(self, *, collection_name: str) -> None:
        del collection_name

    def get_collection_stats(self, *, collection_name: str) -> dict:
        del collection_name
        return {"row_count": len(self.inserted)}

    def search(
        self,
        *,
        collection_name: str,
        data,
        filter: str,
        limit: int,
        output_fields: list[str],
        timeout: float = 10.0,
    ):
        del collection_name, data, limit, output_fields
        self.last_filter = filter
        return self.search_result


class FakeEmbeddingClient:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3][: self.dimension] for _ in texts]
