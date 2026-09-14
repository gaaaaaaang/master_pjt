# 사용자 평가 → 검토된 few-shot

## 진행 기록 (2026-09-13 야간)

사용자는 2026-09-14 02:00 KST까지 구현과 검증을 계속하도록 승인했다.
이 작업의 heartbeat 예약 ID는 `fab-few-shot`이다. 종료 시 PAUSED로 변경한다.
2026-09-13 23:25 KST 시점 구현/로컬 복구/회귀/UI/소수 실제 모델 검증을 완료했다.

- 334개는 질문/답변 메시지 수이며 평가 수가 아니다.
- 실제 개발 서버는 이 저장소에서 `uv run uvicorn app.main:app --reload`로 실행 중이다.
- 실제 DB: `apps/assistant/output/state/assistant.sqlite3`.
- 23:05 무렵 읽은 DB는 좋아요 3개, 싫어요 1개다. 읽기 전후 WAL 파일이 없음을 확인했다.
- 3개 좋아요는 22:58:49~58 KST에 기록됐다. 과거 구현은 browser trace ID를 저장하지만
  대상 turn 선택에 사용하지 않고 최신 assistant에만 연결했다. 따라서 세 snapshot 모두
  마지막 수율 질문에 붙어 있다. 원본을 지우거나 세 건을 하나로 합치면 안 된다.
- 실제 Chrome `http://127.0.0.1:5173/` DOM에서 선택된 좋아요와 browser ID를 대조했다:

| browser trace ID | 사용자가 실제 평가한 질문 |
| --- | --- |
| `9a8d335d-6c79-4f17-919b-e8ad52e84a79` | 방금거는 왜 근거를 분석하지 못한거야? |
| `4b5c2cfb-577b-4e8f-a319-e82634b3a219` | FAB11 etch 가동률이 5%p 떨어지면 처리량에 얼마나 영향이 있어? |
| `5f2f5b42-24a2-4273-934e-2767d0c7811a` | FAB12 최근 7일 공정별 수율 추세를 그래프로 보여줘 |

### 완료한 구현

- 새 답변마다 서버 `message_id` 발급, 일반 chat/SSE 응답에 동일하게 반환.
- 기존 DB turn에도 내용 변경 없이 ID를 부여하는 migration.
- 서버 ID로 오래된 답변을 정확하게 선택. 잘못된 ID/다른 대화의 ID는 거절.
- 평가/대화 metadata/당시 history snapshot/예시 후보를 하나의 SQLite transaction으로 저장.
- 동일 평가 재시도 중복 방지. 평가 변경은 이전 승인을 취소하고 재검토.
- UI 좋아요/싫어요 변경 및 선택 의견 입력. 이전 브라우저 답변은 질문·답변 원문이
  서버에서 유일하게 일치할 때만 연결. 모호한 다중 turn 평가를 최신 답변에 붙이지 않음.
- `pending/approved/rejected/excluded` 상태 및 검토자/메모/시각을 저장.
- 승인된 같은 query type 예시를 한국어/영문 bigram 유사도로 최대 3개 선택.
  현재 conversation 제외, 질문 중복 제거, 유사도 기준 미달 제외.
- 일반 Composer와 문서 Composer 모두 예시를 별도 입력으로 받음. 예시를 현재 근거/수치/
  인용으로 사용하지 않는 prompt 규칙. 기존 근거 검증과 Answer Supervisor 유지.
- Composer trace reasoning에 사용한 예시 ID 기록. 예시 DB 장애는 예시 없이 계속 진행.
- 로컬 CLI 조회·검토·JSONL 내보내기.

### 검증과 남은 작업

- Backend 전체 **1,015 tests 통과**, frontend **38 tests 통과**, Vite build 통과.
- 신규 Python 모듈/CLI Ruff와 `git diff --check` 통과. 실행 중인 실제 서버 `/health`와
  OpenAPI의 새 message ID/feedback 필드도 읽기 전용으로 확인했다.
- DB 복구 전 SQLite backup: `apps/assistant/output/state/backups/before_feedback_relink_20260913.sqlite3`.
- 3건 재연결 완료. feedback 원본 4개 row가 byte-for-byte 동일함을 조회 비교했고
  `PRAGMA integrity_check=ok`를 확인했다. 복구 이력은 `feedback_relinks`와
  `apps/assistant/output/state/feedback_relink_20260913.json`에 보존했다.
- **좋아요 3 / 싫어요 1 / 검토 대기 3 / 승인 0**. 기존 싫어요는 브라우저 대상 증거가 없어
  임의로 재연결하지 않았고 원래 snapshot을 유지한다.
- 분리된 mock API(8769) + frontend(5176)에서 실제 브라우저 UI를 검증했다.
  두 번째 답변 후 첫 번째 답변에 좋아요를 누르면 첫 답변의 서버 ID가 전송되는 것을 확인했다.
  싫어요 변경, 한국어 의견 저장, HTTP 503 시 기존 평가 유지, 새로고침 후 평가와 마지막
  성공 의견 복원이 모두 동작했다. 실패한 의견은 저장된 의견을 덮어쓰지 않았다.
- 실제 사용자 DB에 합성 평가/테스트 승인을 넣지 않았다. 평가 변화는 이벤트 이력이므로
  `feedback_events`는 답변 수나 사용자 수와 다르다. stats에서 메시지 수와 별도로 표시한다.
- 실제 모델 비교는 임시 DB에서 합성 과거 예시(999 LOT/999분)와 현재 근거(123 LOT/7분)를
  사용했다. 최초 비교 4응답 모두 현재 수치·인용을 유지했다. 수치 외에 timestamp가 날짜로
  줄어드는 경우를 발견하여 few-shot 규칙에 현재 시각 정밀도 보존을 추가했다.
- 후속 검증에서 SQL 예시 사용/미사용과 문서 예시 사용은 통과했다. 문서 기준선 한 번에
  API 장애가 발생했고 안전한 오류 답변이 반환됐다. 해당 기준선만 재확인했으나 review API 단계의 모델 오류가 재발했다. 예시 적용 경로의
  수치/인용/시각 검증은 통과했고, 예시 미적용 문서 기준선 API 불안정은 미해결 한계로 기록한다.
  이미 성공한 예시 적용 검사를 추가 반복하지 않았다.
- 이 비교는 수치/인용/시각 유지에 대한 작은 검증이며 일반적인 답변 품질 향상률을 증명하지 않는다.
- 검토 화면/대량 자동 승인은 현재 범위가 아니다. CLI에서 사람이 재사용 질문/답변을 검토한다.
  기존 3건도 자동으로 승인하지 않았다. 예시를 넣지 않은 기존 답변 동작이 기본 상태다.
- 실제 모델 결과: `apps/assistant/output/evals/feedback_20260913/live_grounding.json`,
  `live_grounding_final.json`, `live_document_baseline_retry.json`. 모델 호출은 합계 14회이며
  테스트 데이터는 모두 합성이고 운영 평가와 분리했다.
- 야간 후속 작업은 추가로 발견된 문제와 검토 후보의 재사용성 확인에 한정한다.
  같은 검사나 모델 호출을 근거 없이 반복하지 않는다. 02:00 이후 새 작업을 시작하지 않는다.

## 운영 방법

프로젝트 루트에서 실행한다. 기본 SQLite 경로는 설정 `ASSISTANT_STATE_STORE_PATH`이며
다른 DB는 `--store /absolute/path.sqlite3`로 지정한다.

```bash
.venv/bin/python apps/assistant/scripts/manage_feedback_examples.py stats
.venv/bin/python apps/assistant/scripts/manage_feedback_examples.py list --status pending
.venv/bin/python apps/assistant/scripts/manage_feedback_examples.py inspect MESSAGE_ID
```

검토자는 원본 질문/답변, SQL/근거, 한계와 평가 의견을 확인한 뒤 후속 질문의 생략된 맥락을
보충한 독립 질문과 재사용할 답변을 각각 UTF-8 파일로 작성한다. 개인정보, 내부 식별자,
특정 시점의 결과를 새 질문의 사실처럼 오해할 표현을 제거한다. 좋아요는 정답 검증과 다르다.
기존 답변처럼 `evidence`가 snapshot에 없는 경우 승인 시 `--source-review-note`로
검토자가 실제 확인한 원본 데이터/문서와 확인 내용을 남겨야 한다. 이를 생략하면 승인되지 않는다.
실패/싫어요 답변은 승인할 수 없다. 검토 대기에는 성공한 긍정 평가가 모이고 승인 전에는 검색되지 않는다.

```bash
.venv/bin/python apps/assistant/scripts/manage_feedback_examples.py approve MESSAGE_ID \
  --reviewer reviewer-name --note '근거 및 비식별화 확인' \
  --question-file /tmp/reviewed-question.txt --answer-file /tmp/reviewed-answer.txt
.venv/bin/python apps/assistant/scripts/manage_feedback_examples.py reject MESSAGE_ID \
  --reviewer reviewer-name --note '예시 사용 중지 사유'
.venv/bin/python apps/assistant/scripts/manage_feedback_examples.py export /tmp/approved-examples.jsonl
```

이 기능은 모델 weight를 학습시키지 않는다. 다음 질문 때 검토된 예시를 prompt에 추가한다.
사실/수치/기간/인용은 매번 현재 조회 근거에서 결정한다. 👎, 실패, 근거 없는 답변은 자동
정답 예시가 되지 않으며 문제 분석 기록으로 남긴다. 새 평가나 검토 취소는 다음 검색에 반영된다.

설정 기본값:

```dotenv
FEEDBACK_FEW_SHOT_ENABLED=true
FEEDBACK_FEW_SHOT_LIMIT=3
FEEDBACK_FEW_SHOT_MIN_SIMILARITY=0.35
```

기존 잘못된 평가 연결은 실제 브라우저에서 확인한 trace ID와 질문이 해당 대화에서
유일하게 일치할 때만 아래 도구로 복구한다. 기본은 dry-run이다. 먼저 SQLite backup을 보존하고
검증 결과를 읽은 뒤 `--apply`를 추가한다. 원본 feedback은 수정하지 않으며 audit overlay를 기록한다.

```bash
.venv/bin/python apps/assistant/scripts/manage_feedback_examples.py repair-legacy \
  --trace-id BROWSER_MESSAGE_ID --question '화면에서 확인한 질문' \
  --reason '브라우저 평가 버튼/대상 질문 대조 근거'
```

합성 모델 비교는 명시적으로 `--live`가 있어야 모델을 호출한다. 임시 DB만 사용한다.

```bash
.venv/bin/python apps/assistant/scripts/evaluate_feedback_few_shot.py --live \
  --output /tmp/feedback-live-check.json
```

첫 버전은 로컬 SQLite와 문자 bigram 유사도 검색이다. 벡터 검색/모델 학습/개인별 권한·보존
정책은 포함하지 않는다. 예시 승인 권한은 서버 API로 공개하지 않고 로컬 CLI로 제한한다.

UI 검증용 서버(5176/8769)와 임시 브라우저 탭은 종료했다. 사용자 실제 서버(5173/8000)는 계속 실행 중이다.

### 2026-09-14 00:00 후속 점검

- 검토 상태만 확인하던 승인 경로에서 `excluded → rejected → approved` 우회를 발견했다.
  승인 transaction 안에서 원본 `helpful`과 답변 `status=succeeded`를 다시 확인하도록 수정했다.
  검토 거절이나 source review note가 싫어요/실패한 원본을 승인 가능하게 만들지 못한다.
- `inspect` 출력에 현재 재사용 질문/답변과 검토자/메모/검토 시각을 추가했다.
  원본 history와 편집된 예시를 함께 확인할 수 있으며 export 내용과도 대조할 수 있다.
- 관련 테스트 17개 통과. 부정 평가/실패 답변의 거절 후 재승인 차단 2개 회귀 사례와
  원문·편집본 동시 조회를 포함한다. 변경 파일 Ruff 및 diff whitespace 검사 통과.
- 운영 DB는 읽기 전용으로 확인했다. 좋아요 3, 싫어요 1, pending 3, approved 0이며
  새 평가나 상태 변경은 없다. 이번 후속 점검에서 모델 API 호출은 하지 않았다.

### 2026-09-14 00:55 후속 점검

- 평가가 바뀌면 예전 승인 예시의 편집본/검토 메모가 덮어써지는 문제를 수정했다.
  `feedback_example_audit`에 승인·거절·평가 변경 전 snapshot을 같은 transaction으로 남긴다.
  현재 검색 대상에서는 즉시 제외하면서 과거 검토 결과는 `inspect.review_history`로 확인할 수 있다.
- JSONL export가 원본 DB나 WAL/SHM 파일을 출력 대상으로 받으면 원본을 손상시킬 수 있음을
  확인했다. 같은 경로·심볼릭 링크·하드 링크를 포함해 DB/journal 덮어쓰기를 차단했다.
  export를 임시 파일에 완성한 뒤 교체하여 쓰기 실패 시 이전 완성 파일도 보존한다.
- 관련 테스트 21개 통과: 검토 이력 보존, audit 기록 실패 시 승인 rollback,
  DB/journal/hardlink 출력 거절, export 실패 시 기존 파일 보존을 추가했다.
  변경 파일 Ruff 및 diff whitespace 검사 통과. 실제 모델 호출이나 운영 평가 변경은 없다.
- 내보낸 JSONL은 해당 시점의 사본이다. 이후 승인 철회를 반영한 파일이 필요하면 다시 export한다.
  서비스의 동적 few-shot 검색은 항상 현재 승인 상태를 조회한다.

### 2026-09-14 01:15 후보 검토 자료

- [좋아요 3건 검토 자료](feedback_review_queue.md)와 독립 질문/답변 초안 6개 파일을 작성했다.
  모두 검토용 가정 예시이며 운영 데이터나 추가 사용자 평가가 아니다. 자동 승인/등록하지 않았다.
- 진단 후속 질문은 생략된 맥락을 명시하고 관측 근거와 원인 확정 근거를 구분했다.
  영향 계산은 명시한 가정값과 비례식으로, 추세 설명은 시작·종료 차이와 중간 등락으로 분리했다.
- 원문 추세 답변의 공정명 표기와 하한 수치 간 불일치를 검토 항목으로 남겼다.
  원본 정밀도 근거 없이 어느 수치가 맞는지 단정하거나 운영 원문을 수정하지 않았다.
- 영향 계산 산술과 초안의 입력 길이 제한을 확인했다. 문서만 추가했으므로 이미 통과한
  코드 회귀/실제 모델 검사를 다시 실행하지 않았다. 운영 후보 3건은 pending 상태로 유지한다.

### 야간 작업 종료

2026-09-14 02:36 KST에 도착한 종료 후 heartbeat에서 종료 시각 경과를 확인했다.
02:00 이후 새 개발이나 테스트를 시작하지 않고 예약 `fab-few-shot`을 PAUSED로 변경했다.

- 구현: 답변별 평가·의견 저장, 이전 답변 연결 복구, 검토/승인/철회, 동적 few-shot 적용,
  검토 이력 보존, 안전한 JSONL 내보내기와 검토 초안 작성 완료.
- 마지막 확인된 실제 평가: 좋아요 3, 싫어요 1. 복구한 후보 3건은 pending이며 승인 0건이다.
- 검증: 전체 backend 1,015건·frontend 38건 통과 후, 야간 추가 변경의 관련 21건도 통과했다.
  UI 동작·빌드·정적 검사를 완료했다. 추가 변경 이후 전체 suite를 다시 실행한 것으로 집계하지 않는다.
- 실제 모델의 few-shot 수치/인용/시각 검증은 통과했다. 비교용 문서 기준선의 review API 오류는
  별도 한계로 남긴다. 광범위한 품질 향상이나 성능 개선을 검증했다고 주장하지 않는다.
- 기존 싫어요의 정확한 대상은 추가 증거가 없어 원래 snapshot을 보존했다. 승인 대기 예시는
  사람이 원본 근거와 초안을 검토한 뒤 활성화해야 한다.

## PR 검증 (2026-09-14)

- 최종 코드 기준 backend 전체 1,023개, frontend 38개 테스트 및 Vite build 통과.
- 기본 대화 모델을 `gpt-5.6-luna`로 변경했다. 기존 `.env`도 `OPENAI_MODEL`을
  변경하고 백엔드를 재시작해야 한다. SKAX Luna에서 거부하는 temperature 옵션은 생략한다.
- Luna 실제 호출로 일반 답변과 문서 근거 답변·검토를 확인했다(합성 입력, 3회 호출 모두 성공).
  결과는 `apps/assistant/output/evals/feedback_20260913/luna_baseline_20260914.json`에 보존했다.
  이 검증은 기본 호환성 확인이며 전체 시나리오 품질 비교는 아니다.
- 피드백은 수집·검토 구조를 마련한 단계다. 로컬 확인 당시 승인 예시는 0개이며,
  좋아요만으로 예시를 자동 승인하거나 모델을 학습시키지 않는다.
