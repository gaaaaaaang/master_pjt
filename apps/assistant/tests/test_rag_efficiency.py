import pytest
from app.rag.embeddings import RequestEmbeddingCache, ordered_vectors
from app.rag.search import search


def chunk(cid, content, **metadata):
    return {
        "chunk_id": cid,
        "knowledge_base": "incident_playbook",
        "title": cid,
        "content": content,
        "metadata": metadata,
    }


def test_exact_lookup_uses_primary_id_without_any_model_call():
    def unexpected(*args):
        raise AssertionError("Exact identifier lookup must not call embeddings")

    class Reranker:
        def rank(self, *args):
            raise AssertionError("Exact identifier lookup must not call LLM")

    records = [
        chunk("reference", "playbook_id PB-EX-001\nSee PB-EQ-001"),
        chunk("procedure", "playbook_id PB-EQ-001\nActual equipment procedure"),
    ]
    result = search("PB-EQ-001", records, dense_search=unexpected, reranker=Reranker())
    assert [c["chunk_id"] for c in result.chunks] == ["procedure"]
    assert result.trace["retrieval_mode"] == "exact_id"


def test_exact_lookup_missing_or_withdrawn_ids_do_not_return_incidental_mentions():
    records = [
        chunk("withdrawn", "playbook_id PB-EQ-001", status="withdrawn"),
        chunk("reference", "playbook_id PB-EX-001\nSee PB-EQ-001"),
    ]
    result = search("PB-EQ-001, PB-ZZ-999", records)
    assert result.chunks == []
    assert result.trace["missing_exact_ids"] == ["PB-EQ-001", "PB-ZZ-999"]


def test_query_embedding_cache_is_request_scoped_and_returns_copies():
    class Client:
        calls = 0

        def embed_texts(self, texts):
            self.calls += 1
            return [[0.1, 0.2]]

    client = Client()
    cache = RequestEmbeddingCache(client)
    cache.embed_texts(["question"])[0][0] = 99
    assert cache.embed_texts(["question"]) == [[0.1, 0.2]]
    assert client.calls == 1
    RequestEmbeddingCache(client).embed_texts(["question"])
    assert client.calls == 2


@pytest.mark.parametrize("indexes", [[0, 0], [1, 2], [False, 1], [0]])
def test_embedding_indexes_must_match_input_order_exactly(indexes):
    with pytest.raises(ValueError):
        ordered_vectors({"data": [{"index": i, "embedding": [1.0]} for i in indexes]}, 2)


def test_out_of_order_embedding_response_is_correctly_reordered():
    assert ordered_vectors(
        {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}, 2
    ) == [[1.0], [2.0]]
