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
