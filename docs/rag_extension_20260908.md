# RAG 추가 고도화: 2026-09-08 06:30–08:00 KST

사용자가 1시간 30분 추가 개선과 실제 API 검증을 요청했다. 기존 명시적 승인 범위는
skax.ai-talentlab.com으로 SMT2020 설명서·시뮬레이션 매뉴얼 청크 및 평가 질문 전송이다.
작업 worktree는 /private/tmp/master_pjt_rag_adv, branch fix/rag_adv, 시작 HEAD 382c56b.
기존 다른 작업 브랜치는 수정하지 않는다. 검증용 compose project는 rag-adv-validation.

## 평가 및 우선순위

- 새 10문항을 코드 수정 전에 rag_extension_eval.json으로 고정했다. 개발자가 문서를 보고
  만든 문항이므로 독립 전문가 blind test는 아니다. 7 answerable / 3 unknown.
- 전체 /api/chat 및 /api/chat/stream 경로를 in-process ASGI로 실행한다. Planner, Supervisor,
  Milvus+LLM RAG, Reflection, Composer는 실제 호출한다. 배포 환경/부하 검증과 구분한다.
- 검색이 비었을 때 trace/limitation 보존, Supervisor 중단/status 일관성, LLM 입력 중복 제거,
  검증 가능한 인용과 조건 보존을 우선 개선한다.
- 기존 55청크 임베딩을 재사용하며 관련 없는 DB 데이터와 인증정보는 평가에 전송하지 않는다.

## 진행 기록

06:30 시작. 격리 Milvus 재시작. 새 평가 fixture 및 실제 chat 평가 스크립트 준비.

06:45 전후 1차 구현/실측 기록:
- Supervisor proceed=false/ready 모순은 중단 처리하고 status/answer/limitations를 보존한다.
- 빈 검색도 EvidenceResult로 trace/실패 원인을 보존하여 SSE/agent_runs로 전달한다.
- RAG 문서·Planner prompt·검색 debug metadata의 LLM 중복 전달을 제거했다.
- 문서 전용 답변은 검증 가능한 원문 참조 및 생성 후 조건/역할/수치 검토를 사용한다.
  처음에는 LLM이 인용문을 다시 작성하여 PDF 글머리표/따옴표 차이로 실패했다.
  최종 방식은 서버가 만든 quote_id를 선택하고 원문을 서버가 복원하는 방식으로 변경했다.
  JSONL 재임베딩 없이 적용한다. 정상 인용은 원문 존재를 검증한 것이며 의미적 함의는
  동일 모델의 별도 검토이므로 독립 전문가 정확도 보증은 아니다.
- 실제 전체 SSE 10문항에서 검색은 answerable 7/7 필요한 문서 포함, unknown 3건 근거 없음.
  인용문 재작성 버전은 Hold 문항 검증 실패를 남겼다(rag_chat_extension_v2.json).
  quote_id 방식은 실패했던 Hold/AutoSched와 PM의 저장된 근거 재사용 API 검증 3/3 통과
  (rag_grounding_spans.json). 이 서로 다른 실행을 단일 성공률로 합산하지 않는다.
- 요청별 실제 호출 수/공급자 보고 토큰/실패/단계 지연을 기록한다. 프롬프트·키·본문은
  계측에 저장하지 않는다. 실제 SSE에서 6회(Planner/Supervisor/embedding/rerank/generate/review)
  호출의 usage 필드가 반환됨을 확인했다. 가격/비용 추정치를 임의로 제시하지 않는다.
- SSE는 graph.astream으로 전환하고 전체 timeout을 비동기로 적용한다. 이미 전송된 동기 HTTP
  요청 자체는 강제 취소를 보장하지 않으므로 취소/timeout 후 다음 node 실행 중지와 구분한다.
- 순수 PB ID는 primary ID로 조회하여 embedding/reranker 호출을 생략한다. 여러 KB에서 같은
  질문을 검색할 때 embedding을 요청 범위 안에서만 재사용한다. 임베딩 응답의 index 중복/누락
  검증을 추가했다. 마지막 전체 테스트는 175 passed; 이후 span ID 테스트 1건 추가.
- 원문 인용을 UI에서 펼쳐 볼 수 있게 하고 페이지를 열기만 해도 실행되던 유료 batch 호출을
  제거했다. 웹 의존성이 없어 lockfile 설치 후 빌드/화면 검증 예정이다.

복합 KB 확장: 두 KB 복합 검색 fixture 3문항을 추가했다. 기존 전송 승인과 새 API 검증 요청을
제시하고 목적지를 skax.ai-talentlab.com으로 코드에서 제한했지만 자동 승인 검토는 새 3문항의
개별 승인을 두 번 요구했다. 비동기 질문으로 정확한 3문항을 제시했으며 이 추가 3문항은
사용자의 응답 전까지 API로 전송하지 않는다. 기존 승인된 문항의 검증과 로컬 작업은 계속한다.

UI 검증: npm ci --ignore-scripts 및 npm run build 성공. 루프백 전용 Vite :5176과
저장 결과 replay :8006으로 실제 브라우저에서 인용 원문 펼침을 확인했다. 화면을 여는 것만으로
batch가 호출되지 않았고, 클릭 후 원문 ‘납기 압박은 품질 hold 해제의 단독 사유가 될 수 없다.’가
표시됐다. 답변 줄바꿈이 한 문단으로 합쳐지는 문제를 발견하여 pre-wrap으로 보완했다.
이 화면 검증은 저장 결과 replay이며 별도 실제 API 호출 결과와 구분한다.

## 07:04 1차 체크포인트

- `rag_chat_extension_spans_final.json`: 동일 10문항의 전체 실제 SSE 경로에서 answerable 7/7
  필요한 원문을 검색하고, 7건 모두 quote_id 복원 및 생성 후 모델 검토를 통과했다.
  unknown 3건은 수치를 만들지 않았지만 그중 EUV 1건이 needs_clarification으로 중단됐다.
  그 불필요한 슬롯 질문을 개선한 별도 재시험 `rag_unknown_routing_retest.json`은 RAG 검색 후
  data_unavailable로 답변을 보류했다. 10문항 결과와 단일 재시험을 하나의 실행으로 합산하지 않는다.
- 10문항 end-to-end 중앙값 14.526초, 최대 20.852초, 공급자 보고 total tokens 합계 96,580.
  embedding과 chat 모델 토큰을 합친 관측값이며 비용 추정치가 아니다. 여러 회차 전체 합계가
  아니라 해당 최종 10문항 실행만의 사용량이다. 대다수 답변 가능 질문은 6번 모델/API 호출.
- 일반 `/api/chat` 실제 모델 호출 3건도 확인했다(`rag_chat_http_spans.json`):
  Hold 및 AutoSched 답변 + source citations, EUV data_unavailable.
- 실제 루프백 TCP 서버 검증(`rag_tcp_http_live.json`)은 PM SSE 답변/인용과 EUV 일반 HTTP
  답변 보류를 통과했다. 첫 SSE 이벤트 0.018초, LLM 실행 중 health 응답 0.002초,
  전체 SSE 13.321초, 일반 chat 12.234초. 배포망/부하/SLA 측정은 아니다.
- 부정 표현으로 제외한 FAB/절차 ID/승인 SOP 조건이 필터에 재유입되는 문제를 수정했다.
- ChatRequest.fab을 RAG 필터에 전달하고 질문과 충돌하면 확인 요청으로 중단한다.
  이 범위 검증은 로컬 회귀 테스트로 확인했으며 실제 FAB별 사내 문서는 현재 corpus에 없다.
- 현재 55청크 corpus를 다시 임베딩하지 않았다. 두 KB 복합 검색의 추가 3문항 API 승인은
  대기 중이며, 기존 승인된 평가 질문의 검증과 독립적인 개선은 계속 진행한다.

## 07:18 추가 원문 대조와 완전성 보완

- 최종 10문항 답변의 인용 38개를 canonical JSONL과 오프라인 대조했다.
  chunk ID, 인용문(명시한 PDF 서식 정규화 적용), 문서명, 페이지 모두 일치했다.
  `audit_rag_citations.py`와 `rag_citation_audit.json`으로 재현 가능하다.
- **7/7 검색 및 모델 검토 통과는 7/7 완전한 정답을 뜻하지 않는다.** 원문을 직접 비교하니
  PM 답변에서 표의 `low risk and manager approval / risk memo`가 빠졌는데 기존 모델
  검토는 complete로 판단했다. coverage 항목만 추가한 재시험도 같은 누락을 놓쳤다
  (`rag_pm_coverage_retest.json`). 이 실패를 삭제하지 않는다.
- 알려진 PDF `Decision / Allowed When / Evidence` 3열 레이아웃을 명시적 조건·증거 행과
  선택 가능한 원문 ID로 복원한다. 헤더/행 개수가 맞지 않는 표는 추측해 파싱하지 않는다.
  표만 강조한 중간 재시험은 복구 후 qualification 기록을 누락했다
  (`rag_pm_table_retest.json`). 승인 전 증거와 복구 후 시험 기록을 구분하도록 보완했다.
- source_spans.v3는 주장별 지원 여부 외에 질문 항목별 coverage를 검토하고, 누락 항목이
  있으면 complete=true라도 partial로 처리한다. 여전히 같은 LLM의 검토이며 누락을
  완전히 탐지하는 장치는 아니다. 수치 리터럴은 식별자(SMT2020/P95), 쉼표/지수 표기,
  부호와 소수점을 구분한다. 단위 관계나 숫자의 의미까지 증명하지는 않는다.
- 저장 근거 재사용 2문항(`rag_coverage_table_final.json`) 후 전체 실제 SSE 2문항
  `rag_chat_coverage_final.json`을 별도 실행했다. PM은 Engineer 승인, 낮은 위험도와
  manager 승인 및 risk memo, PM 이후 qualification/dummy run 결과를 모두 포함했다.
  PM 16.128초, KB 업데이트 19.363초. 두 답변의 인용 13개도 원본 대조 통과
  (`rag_coverage_citation_audit.json`). 이 2문항은 이전 10문항 결과를 덮어쓰지 않는다.
- KB 업데이트 답변은 문서에 있는 상태/검토 기준을 설명하지만, corpus에는 상세 revision
  번호/변경 이력 정책이 없다. 상태 관리 설명을 완전한 버전 이력 정책 검증으로 해석하면 안 된다.
- Queue time 문항의 초기 rubric에는 Operator 기록까지 있으나 실제 질문은 Scheduler 배정과
  Engineer 품질 판단만 묻는다. 이 질문 밖 항목을 누락 오답으로 계산하지 않는다.
- SSE 종료/timeout 시 공유 요청 ledger를 취소해, 이미 진행 중인 동기 HTTP가 끝난 뒤
  같은 worker가 후속 review 등의 모델 호출을 시작하지 않게 했다. 이미 전송된 호출의
  강제 중단은 보장하지 않는다. 실제 worker thread 회귀 테스트로 후속 호출 차단을 확인했다.

검증: 추가 수정 후 전체 Python 테스트 189 passed(기존 의존성 deprecation 경고 2건), 변경 파일 Ruff 통과. 웹은 07:04 체크포인트 이후 변경하지 않았다.

## 07:21 검색 경로 후속 검토

- LLM이 같은 관련도 등급을 준 의미 검색 후보는 마지막 정렬에서도 RRF 검색 순위를
  유지한다. 이전에는 lexical feature가 둘 다 탈락한 동점 후보가 chunk ID 순으로 정렬됐다.
  bounded rerank 진입 전 순위 보존과 최종 선택 동점 보존을 각각 테스트한다.
- 메타데이터의 Retired 상태(대소문자/공백 정규화 포함)를 withdrawn/superseded와 함께
  제외한다. lexical, dense, 정확 ID 경로 모두 확인했다.
- serving adapter는 vector DB 후보의 본문/KB를 현재 local corpus와 대조한 뒤 현재 metadata를
  사용한다. 상태 변경 후 오래된 vector metadata가 문서를 다시 살리는 경로는 없음을 확인했다.
- 이 변경은 로컬 회귀로 확인했다. 추가 API 성능 수치로 포장하지 않는다.

최신 전체 회귀: 193 passed, 변경 파일 Ruff 및 diff 검사 통과.

실행 안내 점검: README의 오래된 RAG placeholder 설명과 누락된 `.env.example`을 수정했다.
설정 예제에는 비밀값을 넣지 않았으며, Settings로 실제 로드하고 기본 corpus 경로가 존재함을
확인했다. 로컬 BM25 검색과 유료 전체 chat 호출을 구분하고 하이브리드 활성화 안내를 연결했다.

복합 계획 검토: Planner가 SQL/사례 도구를 선택했더라도 실제 근거가 문서뿐이면
원문 인용 검증 경로를 사용하도록 변경했다. 반대로 knowledge_lookup 분류여도 실제 SQL
결과가 있으면 이를 문서 전용 처리로 버리지 않는다. 실제 혼합 근거 답변은
`grounding.validation=not_applied_mixed_evidence`로 범위를 명시한다. 이 분기 변경은
로컬 회귀로 확인했으며 실제 DB 결과를 API로 전송하는 검증은 수행하지 않았다.
전체 회귀 194 passed, 변경 파일 Ruff 통과.

## 07:44 마지막 실측에서 발견한 누락과 원문 조건 보존

최종 코드 확인용 PM 1문항(`rag_final_commit_smoke.json`, f3e11b3, 15.296초)에서
모델이 Manager 승인/low risk/risk memo와 복구 기록을 설명하면서 Engineer 승인 조건을
다시 누락했다. 모델 검토는 또 supported/complete였다. 이 결과는 정상 인용 검증과
별개로 PM 완전성 rubric 실패이며, 결과 파일의 post_run_source_audit에 별도 기록했다.

source_spans.v4에서는 승인·기록을 묻는 질문에 대해 **이미 인용한 절차**의 명시적 승인/기록
문장과 알려진 decision-table의 approval 행을 원문으로 함께 표시한다. 요약을 더 잘 쓰라는
프롬프트만으로 이 누락을 해결했다고 주장하지 않는다. 출처 범위를 넓히거나 권한의 우선순위,
최종 승인자, 원인 관계를 추론하지 않는다. 해당 패턴 밖의 조건까지 포괄하는 정책 엔진도 아니다.

수정 후 전체 실제 SSE PM 1문항(`rag_procedure_requirements_final.json`, 14.402초)에서
Engineer 승인, PM short delay의 low risk/manager approval/risk memo, PM 이후 qualification
또는 dummy run 결과 기록이 모두 표시됐다. 서버 추출 조건 3개, 인용 4개 corpus 대조 통과
(`rag_procedure_requirements_citation_audit.json`). 이 단일 사례의 확인을 전체 답변 정확도나
독립 전문가 평가로 확대하지 않는다. 이전 실패 실행도 함께 보존한다.

현재 전체 회귀 195 passed, 변경 파일 Ruff 통과. 이후 변경은 결과·문서 정리에 한정한다.
