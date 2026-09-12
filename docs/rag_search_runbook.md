# RAG 검색 v2 실행 및 검증

## 로컬 기본 경로

`fix/rag_adv`는 main `94eba81` 기준이다. 다른 브랜치의 최신 Text2SQL/대화 메모리 변경은
이 브랜치에 병합하지 않았다. 기본 corpus는 package 경로 기준
`apps/assistant/output/rag/master_pjt_v2.jsonl`이며 실제 Vector DB 설정이 없으면 BM25를 사용한다.
BM25 검색 자체는 외부 API 없이 실행된다. 전체 chat의 Planner/Supervisor/답변 생성은
별도로 모델 API를 호출한다. 로컬 검색 평가에 `store_path`를 넘기면 LLM reranker도 호출하지 않는다.

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
2026-09-08 명시적 문서 전송 승인 후 실제 임베딩 55건, Milvus 저장/검색,
LLM reranker 9문항 및 Composer 3문항을 검증했다. 결과 범위와 한계는
`docs/rag_hackathon_20260907.md`의 최종 통합 검증 절을 참조한다.

```sh
docker compose -p rag-adv-validation up -d milvus
# 최초에는 build_rag_index.py로 collection/manifest를 준비한다.
python apps/assistant/scripts/evaluate_rag_live.py --live --env-file .env --manifest apps/assistant/output/rag/api_eval_manifest.json --compose --output /tmp/rag_actual.json
# 저장한 검색을 재사용해 Composer만 재검증할 수 있다.
python apps/assistant/scripts/evaluate_rag_live.py --live --env-file .env --manifest apps/assistant/output/rag/api_eval_manifest.json --compose --replay-report /tmp/rag_actual.json --output /tmp/rag_composer.json
docker compose -p rag-adv-validation stop
```

평가 기본 collection은 `master_pjt_rag_api_eval`이다. 다른 이름이면 `--collection`을 지정한다.
manifest만 복사해도 DB가 복원되지는 않는다. 인덱스가 없는 환경에서는 동일 corpus를 재인덱싱한다.
`--case-id`를 반복하면 일부 문항만 실행한다. 실제 평가에서도 전체 오케스트레이터는 우회한다.

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

## 추가 고도화: 원문 인용과 실제 chat 검증

문서 전용 응답은 source_spans.v4를 사용한다. 모델은 원문 span ID를 선택하며 서버가 인용문,
문서명, 페이지를 복원한다. 존재하지 않는 ID/인용/새 숫자는 거절한다. 생성 후 별도 모델 호출로
각 주장과 조건·역할·불확실성 보존을 검토한다. 인용 존재 검증은 결정적 검사이지만 의미 검토는
동일 모델 기반으로, 독립 전문가의 정답 보증이 아니다. 검증 실패는 failed, 근거 없음은
 data_unavailable, 일부 항목 부족은 grounding.status=partial과 limitations로 전달한다.

`/api/chat` 및 SSE final은 status, citations, grounding, model_usage를 반환한다. citations에는
number/chunk_id/quote/source_document/page_number가 있다. UI에서 선택한 인용문과 전체 근거를
펼쳐 볼 수 있다. 페이지를 여는 것만으로 유료 sample batch를 실행하지 않는다.
model_usage는 실제 호출별 공급자 보고 토큰/지연/실패를 담는다. 미보고 사용량은 0으로 간주하지
않고 unreported_usage_calls로 표시하며 가격 환산은 하지 않는다.

순수 PB ID는 primary ID 조회로 embedding/rerank 호출을 생략한다. 복합 KB의 동일 질문 embedding은
요청 안에서만 재사용한다. ChatRequest.fab은 문서 FAB 범위를 제한하며 문장과 충돌하면 확인을
요청한다. 현재 Milvus 후보 검색 뒤에도 로컬 canonical metadata 필터를 적용하므로, 대규모
FAB별 corpus에서는 서버측 필터와 후보 재현율을 추가 검증해야 한다.

```sh
python apps/assistant/scripts/evaluate_rag_chat_live.py --live --env-file .env --output /tmp/rag_chat_live.json
python apps/assistant/scripts/evaluate_rag_chat_live.py --live --env-file .env --mode chat --case-id ext_hold_deadline --output /tmp/rag_chat_http.json
python apps/assistant/scripts/serve_rag_validation.py --env-file .env --port 8007
# 다른 터미널에서 기존 PM/EUV 두 문항의 실제 TCP HTTP/SSE 확인
python apps/assistant/scripts/check_rag_http_live.py --live --output /tmp/rag_tcp.json
```

위 검증 스크립트는 현재 명시 승인된 skax.ai-talentlab.com 또는 루프백 목적지로 제한한다.
다른 목적지나 추가 전송 범위는 별도 확인이 필요하다. 추가 실행 기록과 실제 검증 범위는
`docs/rag_extension_20260908.md`를 참조한다. SSE timeout/cancellation 이후 후속 node는 중단하지만
이미 전송된 동기 HTTP 요청을 강제로 취소할 수 있다고 보장하지 않는다.

문서의 알려진 Decision/Allowed When/Evidence 표는 조건·증거 행으로 복원한다.
생성 후 검토는 주장 지원 여부와 질문 항목별 coverage를 별도로 기록한다. 같은 모델의
검토가 complete라고 판단해도 실제 누락이 있을 수 있으므로 검색 적중률, 인용 정확성,
답변 완전성을 각각 평가한다. 상세 실패·재시험은 rag_extension_20260908.md를 참고한다.

저장된 평가 결과의 인용을 모델 호출 없이 canonical corpus와 대조할 수 있다:

```bash
python apps/assistant/scripts/audit_rag_citations.py --report apps/assistant/output/evals/rag_chat_coverage_final.json --corpus apps/assistant/output/rag/master_pjt_v2.jsonl --output /tmp/rag_citation_audit.json
```

SSE 종료 후에는 요청 ledger가 후속 모델 호출을 차단한다. 이미 전송된 API 요청은 완료될
수 있으며, 해당 API 공급자에서 발생하는 사용량까지 취소되는 것은 아니다.

문서 외 도구를 계획했더라도 실제 반환 근거가 문서뿐이면 문서 인용 검증을 사용한다.
실제 SQL/사례 근거와 문서를 함께 요약하는 경로는 현재 문서 전용 검증 대상이 아니며
`grounding.validation=not_applied_mixed_evidence`를 반환한다. 문서 전용 API 평가 결과를
운영 데이터와 혼합한 답변의 정확도로 확장해 해석하지 않는다.

승인자나 필수 기록을 묻는 질문은 인용한 절차에서 인식한 명시적 승인/기록 문장과
알려진 decision-table의 approval 조건을 원문으로 함께 표시한다. 이는 생성 요약에서
조건이 빠지는 것을 보완하는 추출 규칙이며, 모든 형태의 승인 조건이나 의미적 적용 범위를
이해하는 정책 엔진은 아니다. 세부 조건의 변경·새 문서 형식에는 별도 평가가 필요하다.
