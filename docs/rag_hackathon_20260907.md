# RAG 검색 고도화 해커톤

- 시작: 2026-09-07 22:54 KST / 변경된 종료: 2026-09-08 01:00 KST
- 사용자 최신 지시: 사용량 절약을 위해 기존 7시간 계획을 단축. 신규 확장보다 검색 실패 개선·검증·정리에 집중.
- 기준: main `94eba81`, branch `fix/rag_adv`
- 작업 위치: `/private/tmp/master_pjt_rag_adv` (원래 fix/adv_t2s의 미커밋 작업 보존)
- 참고: https://toss.tech/article/tech_talk_talk_3 (2026-08-26)

## 초기 진단

main의 RAG는 KB 둘 중 하나를 선택하고 local lexical 또는 Milvus dense 단일 검색을 실행하는 PoC다.
한국어 조사/한영 전문용어, multi-intent, metadata scope, reranker, retrieval evaluation이 없다.
local은 score=0인 경우에도 첫 chunk를 반환한다. graph는 empty retrieval도 succeeded 처리한다.
35개 기존 chunk: incident 11개, SMT2020 simulator documentation 24개.
매뉴얼은 simulation reference이며 실제 사내 SOP가 아니다. process_basics는 일반 반도체 공정
교육 corpus가 아니라 AutoSched/General Data 명세다. 검색 고도화만으로 corpus 지식 공백을 채울 수 없다.
기존 PDF ingestion은 페이지를 이어 붙이고 문자 단위로 잘라 여러 playbook과 절차가 섞인다.
문서 앞 2000자에서 추출한 issue/FAB metadata를 모든 chunk에 복제하며 source ID에 절대경로가 포함된다.

## 목표와 판단 기준

1. 재현 가능한 baseline 및 별도 retrieval 평가: Recall@K, MRR, nDCG, 무응답 정확도, 복합 근거 coverage.
2. 구조 보존 ingestion: 페이지/섹션 provenance, 안정 ID, 문서 버전, chunk별 metadata.
3. 질문 분석/전문용어 사전 + BM25 + dense 후보 + RRF + bounded reranking.
4. KB 복합 검색, 명시 범위 필터, evidence 다양성/근거 예산, 부족 근거 명시.
5. trace/장애 강등/모델·index 일관성/회귀 테스트 및 실제 연결 검증.
6. GraphRAG는 문서에 명시된 관계만 탐색 가능하게 설계. 문서 동시 출현을 인과 관계로 만들지 않는다.

토스 글의 검색 설계를 참조하되 모델·운영 데이터·트래픽에 대한 동등성을 주장하지 않는다.
현재 corpus에 맞는 측정 가능한 개선을 우선한다. 신규 외부 corpus와 verified SOP는 별도 데이터 과제다.

## 초기 검증

main 전체 테스트: 70 passed, 2 failed (worktree에 git 미추적 SMT2020 data가 없어 발생).
기존 data를 read-only 용도로 심볼릭 링크하여 동일 입력을 사용한다.

## 1차 체크포인트 — 2026-09-07 23시대

구현:
- 페이지/Markdown heading 단위 ingestion, 문서 SHA-256, portable chunk ID, chunk별 issue metadata.
- 기존 35개 혼합 청크를 55개 구조 청크로 재생성: playbook 25, simulator documentation 30.
- 한국어 조사 정규화, FAB 용어 사전 v2, 명시 부정/FAB/절차 ID/검증 SOP 요구 분석.
- BM25 원질의·분해질의 후보, weighted RRF, 선택 가능한 bounded LLM reranker, 기본 feature reranker.
- KB/FAB/withdrawn/verified source 필터, 중복 제거, 전체 근거 문자 예산, 복합 주제 coverage.
- 생성 단계에 페이지/절차 provenance와 시뮬레이션 출처 한계를 전달. 근거 0개는 성공이 아니다.
- 문서·임베딩 모델/수정버전/차원을 결합한 index manifest와 변경 시 fail-closed 검증.
- Milvus 검색 예외/잘못된 벡터·리랭커 출력·corpus 검사 및 실패 시 명시적 강등.
- 새 index build CLI 및 명시적 live smoke CLI. 기존 collection은 재생성하거나 지우지 않았다.

평가 `evaluation_version=evidence_units.v2`, K=3 (모두 local offline):

| 세트 | 구현 | Top-3 hit | 필수 근거 Recall@3 | MRR | nDCG@3 | 모든 근거 충족 |
|---|---|---:|---:|---:|---:|---:|
| 기본 28문항 / 답 있음 25 | main 94eba81 | .720 | .700 | .560 | .576 | .680 |
| 기본 28문항 / 답 있음 25 | 현재 | 1.000 | 1.000 | .980 | .949 | 1.000 |
| 도전 30문항 / 답 있음 29 | main 94eba81 | .655 | .638 | .529 | .554 | .621 |
| 도전 30문항 / 답 있음 29 | 현재 | .862 | .862 | .845 | .849 | .862 |

기본 세트 무응답 정답률: 0/3 → 3/3. 도전 세트: 0/1 → 1/1.
이 소수의 범위 밖 문항으로 일반적인 거절 정확도를 주장할 수 없다.
평가 문항은 개발 중 실패 분석에 사용되었으므로 독립적인 운영/블라인드 평가가 아니다.
초기 marker 단순포함 보고서(`rag_hybrid_v*.json`)와 달리 v2는 `playbook_id`의 해당 절차 본문만
정답으로 인정한다. nDCG는 corpus 전체에서 계산한 binary chunk qrels를 사용하고,
복합 질문의 필수 근거 coverage를 별도 계산한다. 완전한 답변 정확도 평가는 아직 아니다.

최신 확인: 전체 테스트 138개 통과. 변경 파일 ruff check 통과. 기존 경고 2개는 dependency deprecation.
외부 LLM smoke는 sandbox 연결 실패로 fallback만 확인했고, 이후 escalation은 자동 승인 검토가
문서 egress 명시적 승인 부재로 거절했다. 사용자에게 `skax.ai-talentlab.com` 목적지로 평가 질문과
SMT2020/시뮬레이션 문서 청크 전송을 질문한 상태다. 승인 없이 재시도/우회하지 않는다.
`rag_live_reranker_smoke.json`은 성공한 LLM 평가로 해석하면 안 된다.

GraphRAG 판단: 현재 매뉴얼 25페이지 중 실제 교차 절차 참조는 예시 페이지 PB-EX-001에서
PB-BN-001/PB-HL-001로 연결되는 정도다. 토스와 같은 관계 검색을 평가할 온톨로지/관계 corpus가
부족하다. 동시 출현으로 인과 관계를 지어내거나 장식적인 Neo4j 구축을 하지 않았다.

## 사용량 절약을 위한 남은 작업 우선순위

1. 현재 diff의 자원 정리/예외 처리와 adapter 실제 경로를 짧게 검토한다. 특히 Milvus client 수명,
   복합 KB 라우팅, 빈 검색 trace, source metadata 필터가 실제 경로에서 보존되는지 확인한다.
2. 도전 세트의 미해결 의미 차이(챔버 멈춤/소모품 재고/장비 우회/KB 갱신 등)를 분석한다.
   현장 표현에 과적합한 규칙을 늘리기보다 사전의 범용성·LLM 재작성 필요성을 구분한다.
3. 사용자 egress 승인 시에만 기존 설정의 LLM reranker 소수 문항을 실측한다. Milvus URL은
   현재 .env에 설정되어 있지 않으며 milvus_lite/로컬 neural 모델 패키지도 설치되어 있지 않다.
   설치/외부 검증 없이 mock 경로를 실서비스 dense 검색으로 보고하지 않는다.
4. 01:00에는 신규 기능을 중단하고 최소 회귀 검증, 커밋 정리, 최종 보고와 heartbeat 중지를 한다.

후속 automation: `rag-7`, 이 task 전용 heartbeat. 00/30분 간격이며 마지막 01:00 KST 회차 포함.
기존 7시간 goal의 문구보다 사용자의 최신 01:00 종료 지시가 우선한다.

## 23:20 운영 경로 점검

- Milvus ensure/insert/search에서 직접 만든 client만 finally에서 닫는다. 주입받은 client 소유권은 유지한다.
- Milvus가 0건 저장을 보고했을 때 요청 row 수로 대체해 성공 처리하던 결함을 제거했다.
- 명시 dimension=0을 기본 차원으로 바꾸지 않고 거절하도록 수정했다.
- RAG 단독 질의의 corpus 오류는 전체 status=failed로 전파한다.
- 복합 KB diagnosis에서도 incident 근거가 있으면 실제 원인 확정 불가 limitation을 유지한다.
- 관련 테스트 61개 통과, 변경 파일 lint/diff 검사 통과. 검색 순위 로직은 변경하지 않았으므로
  평가 세트를 불필요하게 반복 실행하지 않았다.
- 외부 문서 egress 승인은 여전히 대기 중이다. 실제 dense/LLM 성능을 확인한 것으로 간주하지 않는다.

## 23시대 최종 로컬 점검 및 실측 차단

- 같은 chunk를 새 임베딩 revision으로 올릴 때 기존 row ID를 덮어쓰던 문제를 수정했다.
  index_version + chunk_id를 물리 key로 사용하므로 이전 index와 새 index가 공존하고,
  같은 버전의 반복 upsert는 idempotent하다. 회귀 테스트로 검증했다.
- 전체 회귀 테스트 143개 통과. source 변경 범위 lint와 diff 검사 통과.
- 현재까지 로컬 구현/실패 분석/회귀 검증/재현 보고서/실행 안내를 준비했다.
- 아직 증명되지 않은 핵심 항목은 실제 dense 검색·LLM reranker의 품질/지연 및 실제 생성 답변의
  인용 정확도다. Mock 테스트나 local BM25 점수로 이를 대신 주장하지 않는다.
- 동일 문서 egress 승인 차단은 최초 작업과 후속 두 goal turn에서도 해소되지 않았다.
  더 많은 규칙·문항·반복 테스트로 실측을 대신하지 않는다. 사용자 승인 또는 서비스 연결의
  외부 상태 변경 전까지 연속 실행은 blocked로 두고 01:00 최종 정리를 유지한다.

## 2026-09-08 01:00 종료 확인

- HEAD b1cc574이며 직전 검증 이후 tracked code 변경이 없음을 확인했다. 전체 테스트는 재실행하지 않았다.
- 저장된 기본/도전 평가 보고서 수치를 다시 확인했다. 로컬 전체 회귀 143개 통과 기록을 유지한다.
- 문서 egress 승인 답변은 없었으며 실제 API/Vector DB 실측을 추가 수행하지 않았다.
- rag-7 heartbeat를 PAUSED로 변경했다. 예약에 의한 추가 실행은 없다.
- 해커톤 작업을 종료하여 로컬 개선 결과와 미검증 범위를 보고한다. 실제 서비스 동등성은 미증명으로 남는다.

## 사용자 후속 요청: 실제 API 검증

사용자가 실제 API 검증 진행을 지시했다. 기존 목적지 skax.ai-talentlab.com 및 문서 payload를
구체화해 재검토했지만 자동 승인 검토가 두 번 차단했다. 별도의 명시적 승인 질문을 제시한 상태다.
문서 전송 차단과 독립적으로, corpus를 읽지 않는 비민감 합성 문장 검사로 실제 API를 호출했다.

- 임베딩 API: 성공, 실제 3072차원 반환, 약 0.27초.
- LLM reranker API: 성공, 관련 합성 문서 grade=3 / 무관 문서 grade=0, 약 2.23초.
- 결과: `apps/assistant/output/evals/rag_api_connectivity.json`.
- 재현: `check_rag_api_connectivity.py --live --env-file <기존 .env 경로>`.

이는 실제 인증·연결·응답 계약 검증이며 실제 매뉴얼 검색 품질이나 Milvus 통합 성능 평가가 아니다.
그 문서 평가에 대한 egress 승인은 여전히 대기 중이다.

## 명시적 전송 승인 후 실제 통합 검증 완료

사용자는 “해당 목적지로 명시된 문서와 질문 전송 승인”이라고 답했다. 위의 승인 대기는
해소되었으며 기존 skax.ai-talentlab.com API로 다음 검증을 수행했다.

- 실제 text-embedding-3-large로 55개 청크를 3072차원 임베딩하여 Milvus 2.5.4에 저장했다.
  격리 compose project `rag-adv-validation`, collection `master_pjt_rag_api_eval`,
  index version `61d9cd5e076e055f0b6436f7`. 기존 DB와 collection은 수정하지 않았다.
- 동일한 9문항(답변 가능 7, 불가 2)의 local/실제 hybrid 비교:

| 경로 | Hit@3 | Recall@3 | MRR | 근거 없음 판정 |
| --- | --- | --- | --- | --- |
| 로컬 feature | 3/7 | 0.429 | 0.429 | 2/2 |
| 실제 API 최초 실행 | 4/7 | 0.571 | 0.571 | 2/2 |
| 후보 순서 수정 후 실제 API | 7/7 | 1.000 | 0.929 | 2/2 |

- 수정 전에는 lexical gate에서 탈락한 후보들이 ID 순으로 정렬되어 좋은 dense 후보가
  bounded reranker 입력에서 빠졌다. 동점에 RRF 순위를 보존하도록 수정하고 회귀 테스트를 추가했다.
- 초기 별도 reranker smoke에서 ValueError fallback 1건을 관측했다. 원인은 재현되지 않았다.
  응답 schema의 ID enum을 실제 후보에 한정해 계약을 강화했다. 최종 9문항에서는 LLM 대상
  8문항 모두 llm.v1이었고 승인 SOP 요구 1문항은 문서 범위 필터로 API rerank 전에 제외됐다.
- 짧은 rerank 근거를 요청해 최종 검색 시간 중앙값 5.139초, 최댓값 8.712초를 관측했다.
  9건의 단회 측정이며 p95/SLA/비용 또는 장시간 안정성 검증은 아니다.
- Composer는 PM/복합 장비 고장+Hold/EUV unknown 3건을 실제 API로 검증했다.
  최초 답변에서 문서명 축약과 가능성 조건 변형을 발견해 원문 조건/역할/파일명 보존 지침을 보강했다.
  저장된 검색 근거를 재사용한 최종 생성 3건에서 정확한 파일명·페이지, 시뮬레이션 자료 표시,
  EUV 수치 답변 보류를 확인했다. 생성 시간은 각각 3.049/4.214/1.239초였다.
  disposition을 ‘처분’, reroute를 ‘공정 변경’으로 풀이하는 등 전문 용어 번역은 추가 검토가 필요하다.
- 전체 회귀 145개 통과. 검색 수정과 동적 schema의 회귀 테스트를 포함한다.

최종 결과: `rag_actual_api_final.json` (검색), `rag_actual_composer_final.json` (검색 재사용 및
최종 생성). 최초 결과 `rag_actual_api_eval.json`과 smoke도 실패 분석용으로 보존한다.
이 9문항은 이미 실패를 관찰한 개발 문항이며 독립 blind test가 아니다. 생성 검증은 명시적 KB와
고정 PlannerDecision으로 실행한 Composer 검증이다. HTTP /chat, Planner/Supervisor 전체 흐름,
부하/비용, 실제 승인 SOP나 토스 운영 수준을 검증한 것으로 해석하면 안 된다.
검증용 compose 컨테이너는 종료하고 인덱스 volume은 재현을 위해 보존한다.
