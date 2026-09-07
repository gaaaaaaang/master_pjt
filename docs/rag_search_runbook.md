# RAG 검색 v2 실행 및 검증

## 로컬 기본 경로

`fix/rag_adv`는 main `94eba81` 기준이다. 다른 브랜치의 최신 Text2SQL/대화 메모리 변경은
이 브랜치에 병합하지 않았다. 기본 corpus는 package 경로 기준
`apps/assistant/output/rag/master_pjt_v2.jsonl`이며 실제 Vector DB 설정이 없으면 BM25를 사용한다.
이 모드는 외부 API 없이 실행된다. 로컬 평가에 `store_path`를 넘기면 LLM reranker도 호출하지 않는다.

```sh
python -m pytest -q
python apps/assistant/scripts/evaluate_rag_search.py --output /tmp/rag_current.json
python apps/assistant/scripts/evaluate_rag_search.py --fixture apps/assistant/tests/fixtures/rag_search_challenge.json --output /tmp/rag_challenge.json
python apps/assistant/scripts/evaluate_rag_search.py --baseline-ref 94eba81 --store-path apps/assistant/output/rag/master_pjt.jsonl --output /tmp/rag_baseline.json
```

Python 환경에 프로젝트의 dev/rag extra가 필요하다. 기존 프로젝트 Python은
`/Users/a11549/Desktop/skax-git/master_pjt/.venv/bin/python`이다.
재추출에는 원본 SMT2020 PDF 데이터가 필요하지만, 검색/평가는 커밋된 JSONL만으로 가능하다.

```sh
python apps/assistant/scripts/ingest_rag_documents.py --skip-insert --store-path apps/assistant/output/rag/master_pjt_v2.jsonl
```

## 실제 하이브리드 검색 활성화

먼저 승인된 endpoint와 데이터 전송 범위를 확인한 뒤 새 collection에 index를 생성한다.
기존 `master_pjt` collection을 삭제/재생성하지 않는다. 기본 embeddings 모델은
`text-embedding-3-large`다. 동일 deployment의 모델 내용이 바뀌어도 embedding revision을 변경한다.

```sh
python apps/assistant/scripts/build_rag_index.py --live --env-file .env --uri http://localhost:19530 --collection master_pjt_rag_v2 --manifest apps/assistant/output/rag/index_manifest.json
```

전체 삽입 성공 후 manifest가 원자적으로 작성된다. 서비스 환경에 다음 설정을 적용한다.

```dotenv
VECTOR_DB_URL=http://localhost:19530
VECTOR_DB_COLLECTION=master_pjt_rag_v2
RAG_INDEX_MANIFEST_PATH=apps/assistant/output/rag/index_manifest.json
RAG_RERANKER=llm
RAG_RERANK_LIMIT=12
RAG_CANDIDATE_K=30
RAG_CONTEXT_CHARS=14000
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_REVISION=text-embedding-3-large.v1
EMBEDDING_DIMENSION=3072
```

`RAG_RERANKER=feature`는 기본값이며 추가 LLM 호출을 하지 않는다. LLM reranker는 요청당
최대 12개 후보(설정 범위 1~40)를 평가하고 검증된 grade 2 이상만 전달한다. 별도 학습 모델은 아니다.
Milvus와 로컬 corpus는 같은 index manifest로 묶인다. 벡터 검색은 index_version을 필터하고,
반환된 청크의 ID/내용이 로컬 corpus와 일치할 때만 사용한다. 일치하지 않는 manifest 또는
벡터 검색 장애 시 `hybrid_degraded`, LLM 실패 시 `feature.v1_fallback`이 trace에 기록된다.

```sh
python apps/assistant/scripts/check_rag_live.py --live --env-file .env --case-limit 3 --output /tmp/rag_live.json
```

이 smoke는 LLM reranker 실제 호출을 확인하는 도구이며 Vector DB 실측을 대신하지 않는다.
현재 checkpoint에서 실제 LLM/dense 경로는 검증되지 않았다.

## 근거와 평가 해석

검색 결과는 chunk_id, source_document, page_number, section_title, document_version,
reliability, retrieval_features, retrieval_ranks, retrieval_trace, retrieval_limitations를 제공한다.
검색 점수는 순위 신호이며 정답 확률이 아니다. 반환 문서를 현재 공장 상태나 승인된 SOP로
승격하지 않는다. 필수 근거가 부족한 복합 질문은 uncovered_concepts와 limitation을 확인한다.

기본 평가 28문항과 도전 30문항은 소규모 개발 세트다. 토스 운영 시스템과 동등한 성능의 증거가
아니다. 실제 운영 승격에는 독립 질문·관련성 라벨, SOP 승인/유효기간 데이터, 더 큰 unknown 세트,
실제 하이브리드 ablation, p95 지연/비용/실패율 및 생성 답변의 인용 정확도 검증이 필요하다.

## 복귀

로컬 feature 검색만 사용하려면 VECTOR_DB_URL을 비우고 RAG_RERANKER=feature로 설정한다.
기존 corpus 비교는 RAG_LOCAL_STORE_PATH 또는 평가 CLI --store-path로 선택한다.
구 코드 복귀는 이 브랜치의 변경 commit을 확인하고 별도 검증한 뒤 revert한다.
