# FAB 데모 진행 및 검증 기록

> 후속 업데이트: 외부 모델 전송 승인을 받고 추가 개선 및 실모델 재시험을 완료했다.
> [승인 후 재시험 기록](final_adv_live_model_retest.md)에 현재 결과를 정리했다. 아래는 최초 4시간 작업 기록이다.

브랜치: `feat/final_adv` · 작업: 2026-09-12 17:28~21:28 KST, 4시간.
변경 사항은 기존 수정과 함께 미커밋 작업 트리에 보관했다. 새 커밋·푸시·배포는 하지 않았다.
이 문서는 검증한 범위와 남은 확인 항목을 구분한 실행 안내다.

## 실패 원인과 변경

| 확인한 문제 | 변경 |
|---|---|
| FAB11~13 질문에 FAB10 중심의 AutoSched 조회가 적용되거나 시뮬레이션 컬럼 의미가 맞지 않음 | 실제 카탈로그에 있는 공정 스냅샷과 지표 계약으로 SQL을 구성하고 검증한다. 존재하지 않는 제품·라인 구분은 구체적으로 설명한다. |
| SQL 값은 맞지만 소수점 표시, 공정 개수, 결과 요약을 근거 없는 수치로 오인 | 반올림한 측정값과 개수 표현을 구분하고, 전체 조회 결과에서 계산한 요약과 차트 근거를 검토한다. |
| 후속 질문에서 FAB·공정·기간·개별 지표의 집계 방식이 바뀜 | 사용자 질문과 완전하게 반환된 SQL 결과의 범위를 대화 저장소에 보존한다. |
| 앱과 시험이 서로 다른 문서 인덱스를 사용 | 별도 60개 청크 인덱스로 실행 설정을 맞추고, 원문·시뮬레이션 문서·공개 참고자료의 성격을 표시한다. |
| 모델 연결 실패가 여러 단계에서 반복되어 오래 기다림 | 한 요청의 동일 모델 주소에서 연결 실패가 발생하면 중복 대기를 생략하고, 검증된 데이터 결과를 전달한다. 문서 인용 검토가 미완료이면 이를 명시한다. |

DB 조회는 기존 읽기 전용 실행기와 SQL 검증을 거친다. 데이터가 없는 질문과 잘못된 질문 해석,
모델 연결 장애를 구분해 설명하며, 단순히 성공 상태만으로 요청한 결과가 나왔다고 판단하지 않는다.

## 검증 결과

아래 실험은 서로 다른 수준의 검증이며, 합쳐서 하나의 성공률로 보고하지 않는다.

최초와 동일한 SQL 기본 24문항의 완료 상태는 15/24에서 24/24로 개선됐고, 개선 실행의
24개 결과는 원자료 검산도 통과했다. 이후 더 넓힌 SQL 612문항과 채팅 34문항은 별도 시험이다.
초기 SQL 검산 보고서의 2/24는 새 결과 열·집계 계약과의 일치 수이므로, 과거 답변의
수치 정확도가 2/24였다고 해석하지 않는다.

| 검증 | 결과 | 의미와 남은 범위 |
|---|---:|---|
| 실모델 34문항, iteration 6 | 정상 응답 28/32, 범위 밖 질문 2/2 | 이 실행 이후의 수정은 아직 실모델 재시험 전이다. |
| 위 실모델 실행의 SQL 원본 검산 | 조회한 29건 모두 값 일치; 대상 30건 중 1건은 불필요한 확인 질문으로 미조회 | 최종 해석 문장의 모든 주장까지 검증한 숫자는 아니다. |
| 14개 지표 × 네 FAB × 현황·평균·합계·순위 조합 | 296/296 SQL 값 일치 | 로컬 DB SELECT와 독립 Decimal 재집계; 모델·HTTP 차단. |
| 지표별 시간·일 단위 추세와 기간 순위 | 224/224 SQL 값 일치 | etch/전체 시간별, 공정별 일별, 기간 집계 순위. |
| 짧은 지표명과 평균·합계 혼합 | 28/28 SQL 값 일치 | 평균과 합계를 지표별로 분리한다. |
| FAB 전체 조건 검색 | 64/64 SQL 값 일치 | 개별 공정을 먼저 제외하지 않고 전체 합계·평균에 임계값을 적용한다. 위 세 집계 시험과 합친 최신 실행은 612/612. |
| 추가 표현 30개 | 29개 검산 통과, 1개 일반 모델 질의 필요 | 장비별 습도는 공정 영역 값으로 대체하지 않았다. |
| 모델 장애 상황의 기본 34문항 | 정상 질문 30/32, 범위 밖 질문 2/2; SQL 30/30 값 일치 | 문서 지식·대응 절차 2건은 검색 후 모델 인용 검토를 완료하지 못한다. 이전 31/32 집계에는 절차 요청을 수치 조회로 잘못 완료한 1건이 있었으며 정정했다. |
| 모델 장애 상황의 연속 대화 | 27/27 범위·행 수·필수 결과 확인 | 실제 SSE 라우트의 이벤트 생성기를 프로세스 내부에서 실행. 브라우저·HTTP 전송 및 실모델 시험과 구분한다. 마지막 3턴 혼합 집계는 원자료 검산도 통과. |
| 대화 저장소 재로딩 | 27/27 | 매 턴 별도 SQLite 저장소를 새 메모리 객체로 읽어 FAB·공정 참조·지표·집계를 복구했다. 기존 사용자 대화는 변경하지 않았다. |
| 영향 계산의 표기·방향·물리 범위 | 64/64 | 저장한 네 FAB 원자료를 기준으로 독립 Decimal 계산과 대조. 1차 관계식의 계산 검증이며 인과 예측 성능은 아니다. |
| 화면 질문 가이드의 실제 문장 | 데이터 대화 44/44 완료, 수치 검산 32/32 | 네 FAB × 13문장, 총 52개를 SSE 라우트·SQLite 재로딩으로 확인. 나머지 문서 질문 8개는 모델 장애로 인용 검토 미완료. |
| 네 FAB 동시 대화 | 104/104 범위·상태 계약 통과, 수치 검산 64/64 | 별도 대화 4개에서 각각 13문장을 두 번 실행. 데이터 답변 88건 완료, 문서 질문 16건은 모델 장애를 정확히 알리는지 확인한 것이며 답변 완료로 세지 않는다. |
| 모델 연결 시간 초과 | 7/7 서비스 검사 + 7/7 SSE 라우트 검사 | 실제 모델 클라이언트에 MockTransport와 가짜 URL·키를 사용. 질문당 전송 시도 1회 후 같은 요청의 중복 대기 3~6회 생략; 새 요청은 다시 시도한다. 실모델 지연 측정은 아니다. |
| 백엔드 회귀시험 | 865개 통과 | 실제 모델 정확도와 다르다. |
| 프런트엔드 시험 | 33개 통과, build 성공 | 기존 결과 화면은 좁은 화면·단위 분리·표 검색/정렬 확인. 새 질문 가이드는 20개 FAB/흐름 조합의 정적 React 렌더링 확인; Mac 잠금으로 브라우저 시각 검증 대기. |
| 실행 준비 상태, 21:12 KST | 네 FAB·60개 문서 청크 확인, 웹·API HTTP 200 | 로컬 데이터와 서비스 상태 검사이며, 외부 모델 연결은 재검사하지 않았다. |
| 최종 코드의 27분 반복 시험 | 2,808개 요청 계약 검사, 오류 0 | 데이터 답변 2,376건 완료, 수치 검산 1,728건 통과. 문서 질문 432건은 모델 검토 미완료를 확인했으며 답변 성공 건수에서 제외. |

백엔드 애플리케이션 소스(`apps/assistant/src`) 전체의 Ruff 검사는 통과했다. 저장소 전체에는 이번에 수정하지 않은
문서 생성·보고서 점검 스크립트와 기존 의미 평가 테스트의 스타일 항목 30개가 남아 있다.

실모델 iteration 6의 중앙 응답 시간은 19.618초, P95는 33.082초, 최대는 34.102초였다.
기존 iteration 4의 P95 43.092초보다 낮았지만 통제된 A/B 시험은 아니므로 특정 변경의 효과로 단정하지 않는다.
현재 추가 모델 전송은 자동 승인 검토의 거절로 중단되어 사용자에게 전송 범위 확인을 요청한 상태다.
이후 수정에는 소수점·개수 검증, 모델 마스터 설비 대수 집계, 정비 시간 분류, 비가동률 구분,
모델 장애 대체 응답, 문서 성격 표시, 복수 지표와 개별 집계의 후속 질문 유지,
SQL 결과 공정 참조, 변경되지 않은 사이드바 FAB 기본값의 잘못된 재적용 방지가 포함된다.
추가로 “공정의 WIP”의 조사 표현을 “정의” 요청으로 오인하는 문제와, 모델 장애 시
점검·대응 순서 요청을 단순 지표 조회로 대체하는 문제를 수정했다.

상태 외에 요청한 결과물도 검증한다. `audit_final_demo_contracts.py`는 추세의 차트,
영향 추정의 계산, 진단의 문서 근거, 지식·절차의 인용 및 모델 검토 존재를 확인한다.
실모델 iteration 6은 이 추가 검사에서도 기존과 같은 30/34(정상 28건 + 범위 밖 2건)이다.
이는 모든 해석 문장의 정확성을 보증하는 검사가 아니다.

한 질문의 연속 모델 호출은 HTTP 연결을 재사용하고 종료 시 닫는다. 질문 간 연결·쿠키는 공유하지 않는다.
취소 시 진행 중인 동기 호출의 연결은 호출이 끝난 뒤 닫고, 새 호출은 시작하지 않는다.
[HTTPX의 연결 재사용 및 종료 지침](https://www.python-httpx.org/advanced/clients/)에 맞춰 구현했다.
임시 loopback 서버 12회 호출에서 TCP 연결은 12개에서 1개로 줄었다.
로컬 통신 시간 0.145초→0.015초는 전송 계층 시험이며 실제 모델 P95 개선 수치로 사용하지 않는다.

한 질문에서 모델 주소로의 네트워크 연결 또는 읽기가 시간 초과되면, 그 질문의 후속 단계는
같은 주소로 재접속하며 시간을 반복 소비하지 않는다. 기존 모델 장애 대체 경로와 근거 검증은
유지한다. 다음 질문은 새 연결을 시도하며, 다른 모델 주소와 HTTP 응답 오류·잘못된 답변 형식의
재검토 경로는 이 중단 규칙의 대상이 아니다. API 시도와 생략한 호출은 사용량 기록에서 구분한다.

## 데모에서 설명할 범위

기획 근거는 [질문 유형 설계](https://skax.ai-talentlab.com/master-mentee/task/3338)와
[사용자 시나리오 설계](https://skax.ai-talentlab.com/master-mentee/task/3339)다.
현황, 공정 지식, 진단, 영향 추정, 대응 절차, 추세·비교, 후속 질문의 7개 유형을
SC-001~004 흐름에 연결한다.

FAB10~13의 현재 지표는 **생성된 시뮬레이션 스냅샷**이다. 이 데모는 실제 공장 조치를
수행하지 않는다. “현재”는 해당 FAB에 가장 최근 적재된 구간을 뜻하며 답변의 시각을 함께 읽는다.
제품·설비·라인별 실측과 공정 영역별 합성 관측을 구분한다.

## 시연 흐름

화면 상단의 **질문 가이드**에서 FAB10~13을 선택하면 아래 유형들을 순서대로 입력할 수 있다.
질문을 선택하면 입력창에만 넣으며 전송은 사용자가 한다. 가이드의 FAB을 바꾸면 첫 질문부터
시작한다. “그 공정”, “그 기간” 같은 후속 질문은 바로 위 단계의 답변을 받은 같은 대화에서 사용한다.
가이드를 다시 열면 마지막으로 질문을 입력창에 넣었을 때 선택한 FAB과 흐름을 유지한다.

| 흐름 | 질문 | 확인할 결과 |
|---|---|---|
| SC-001 현황 | `FAB11 지금 WIP 몇 개야?` | 동일한 최신 시각의 공정 영역 합계와 KST 기준 시각 |
| SC-001 상세 | `FAB12 식각 공정의 현재 수율과 가동률은 얼마야?` | 식각→etch 매핑, 두 지표와 단위 |
| SC-004 추세 | `FAB12 최근 7일 공정별 수율 추세를 그래프로 보여줘` | 6개 공정의 실제 반환값, 변동과 부분 관측 구분 |
| SC-004 순위 | `FAB13에서 지금 WIP이 가장 많은 공정 3개를 보여줘` | 내림차순 3개, 동률은 공정명 순서 |
| SC-002 진단 | `왜 FAB13 etch 공정 Queue Time이 늘었어? 데이터와 문서 근거로 원인 후보를 설명해줘` | 관측·문서의 원인 후보·추가 확인 항목을 구분 |
| 대응 절차 | `FAB11 etch 공정 Queue Time이 길어졌을 때 운영자가 어떤 순서로 점검하고 대응하면 좋을지 문서 근거로 알려줘` | 문서에 있는 역할과 절차, etch 전용 문서 유무 |
| SC-003 영향 | `FAB11 etch 가동률이 5%p 떨어지면 처리량에 얼마나 영향이 있어?` | 기준값→변화량→추정값, 1차 비례 가정 |
| 공정 지식 | `Cycle Time Degradation이 뭐야? WIP이나 가동률과 어떤 관계가 있어? 문서 근거로 설명해줘` | 용어 설명, 평균값 관계, 가정과 문서 인용 |

주간 비교와 후속 질문은 위의 로컬 연속 대화 시험으로 확인했다. 최신 수정의 실모델 재시험은 남아 있다. 위 문장은 문구별 고정 응답이 아니라,
동일한 스키마와 지표 계약을 사용하는 대표 예시다.

### 발표용 진행 순서

FAB12를 대표로 선택하고 같은 대화에서 아래 순서로 진행하면 7개 질문 유형을 모두 보여줄 수 있다.
각 질문은 화면 가이드에서 선택할 수 있는 문장이다. 발표 시간을 정할 때 모델 응답을 기다리는
시간과 근거를 설명하는 시간을 함께 잡는다. 최신 버전의 실모델 리허설은 아직 완료되지 않았다.

1. **현황에서 상세로 → 첫 질문**: 전체 WIP과 관측 시각을 확인한다. 현재 저장된 기준값은 261 LOT다.
2. **현황에서 상세로 → 두 번째·세 번째 질문**: etch의 수율·가동률을 조회하고 “같은 공정의 WIP”으로 이어 간다. 공정과 FAB을 다시 말하지 않아도 범위가 유지되는지 보여준다.
3. **변화의 원인과 대응 → 세 질문을 순서대로**: 24시간 Queue Time 차트, 데이터와 문서의 원인 후보, 점검 순서를 확인한다. 실제로 증가했는지는 차트의 관측값을 먼저 읽으며, 원인을 확정된 사실로 표현하지 않는다.
4. **가정이 바뀌면 → 두 질문을 순서대로**: 현재 가동률·완료 LOT를 기준으로 5%p 감소 시 처리량을 계산한다. 비례 가정을 둔 계산임을 함께 설명한다.
5. **공정 개념 이해 → 질문**: Cycle Time Degradation의 설명과 문서 인용을 확인한다.

시간이 더 있으면 **기간과 공정 비교** 흐름을 별도 대화에서 시작해 수율이 낮은 공정의 현재 WIP과
주간 비교까지 보여준다. FAB11·FAB13으로 전환할 때도 각 흐름의 첫 질문부터 시작한다.

모델 연결이 끊기면 SQL로 검증한 데이터 결과는 계속 표시할 수 있다. 문서 인용 검토가 필요한
지식·대응 절차는 연결 복구 후 새 질문으로 다시 시도한다. 이때 문서 검색만 끝난 상태를
완성된 답변으로 소개하지 않는다. 재접속은 새 질문에서 이루어지므로 페이지 새로고침은 필수가 아니다.

## 숫자 검산 기준

2026-09-12 00:00 KST에 끝나는 최신 관측 구간을 독립 재집계한 WIP은 다음과 같다.
데이터가 추가 적재되면 값은 달라질 수 있다.

| FAB | 전체 WIP (LOT) | 가장 많은 공정 |
|---|---:|---|
| FAB10 | 142 | etch 33 |
| FAB11 | 188 | cmp 83 |
| FAB12 | 261 | deposition 141 |
| FAB13 | 159 | photo 80 |

시간에 걸친 WIP을 합산하지 않는다. 시각별 서로 다른 공정 영역의 WIP을 먼저 합한 뒤,
기간 평균이 필요하면 시간 관측값을 평균한다. 수율·가동률·평균 대기 시간은 관측 구간의
비가중 산술평균이다. 완료/투입 LOT는 선택한 구간의 합계다. 기간별 관측 수와 구간 길이가
다를 때 이를 동일한 조건의 성능 개선으로 단정하지 않는다.

## 문서 인덱스 재현

초기 실제 앱 설정은 구형 `master_pjt.jsonl`(35개 청크)을, 단위 테스트는
`master_pjt_v2.jsonl`(55개 청크)을 사용했다. 기존 PDF 원문 해시와 재생성 청크 ID가
v2와 동일함을 확인했다. 공개 자료 요약 5개를 더한 별도 60개 인덱스를 만든다.

```sh
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/ingest_rag_documents.py \
  --skip-insert --store-path apps/assistant/output/rag/master_pjt_final_adv.jsonl
```

`.env`의 `RAG_LOCAL_STORE_PATH`를 `apps/assistant/output/rag/master_pjt_final_adv.jsonl`로
설정하고 백엔드를 재시작한다. 기존 인덱스 파일은 보존한다. 이 실행은 로컬 BM25 검색이며,
새로운 Milvus 인덱스나 임베딩을 생성했다고 보고하지 않는다.

추가 자료는 `apps/assistant/data/knowledge/process_flow_reference_ko.md`이며,
MIT OpenCourseWare의 시간 측정·Little의 법칙·M/M/1 설명과 NIST FAB 시뮬레이션 연구를
원문 링크와 함께 요약했다. 회사 승인 SOP가 아니며, 특정 FAB에 보정된 예측 모델도 아니다.

## 검증 방법

시연 직전에는 먼저 준비 상태를 확인한다. 이 명령은 데이터를 재생성하거나 DB를 변경하지 않는다.
`--probe-model`을 추가하면 DB 행 없이 짧은 모델 연결 확인 요청도 보낸다.
이번 작업에서는 추가 외부 전송 확인이 해결되기 전까지 이 옵션을 실행하지 않는다.

```sh
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/check_demo_readiness.py \
  --live --output /tmp/fab_demo_readiness.json
```

`ready=true`는 네 FAB의 최신 공정 관측값·문서 인덱스·연결 조건이 충족되었다는 의미다.
자유 질의 답변의 정확성이나 데이터의 실시간성을 보증하는 값은 아니다. 보고서의 기준 시각을 함께 확인한다.

데모 날짜가 달라지면 `age_hours`와 마지막 관측 시각을 먼저 확인한다. 저장된 데이터가 최근
24시간 밖에 있으면 그 기간의 조회가 비는 것이 맞다. 같은 자료로 다시 시연할 때는
`FAB12 etch 공정의 2026-09-11 Queue Time 추세를 보여줘`처럼 관측 자료가 있는 날짜를
명시하고 과거 시뮬레이션 조회라고 설명한다. 준비 상태 검사 자체는 데이터를 새로 만들지 않는다.

```sh
.venv/bin/pytest -q apps/assistant/tests
npm --prefix apps/web test
npm --prefix apps/web run build
```

다음 실모델 평가 명령은 실제 설정된 외부 모델에 질문과 조회 근거를 전송한다.
이번 작업에서 요청한 전송 범위 확인이 완료된 뒤 실행한다.

```sh
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/evaluate_final_demo.py \
  --live --mode sql --workers 1 --output /tmp/final_demo_sql.json
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/audit_final_demo_values.py \
  --capture --raw /tmp/final_demo_reference.json --report /tmp/final_demo_sql.json
```

최신 수정의 실모델 재시험은 아래 두 실행으로 마무리한다. 첫 실행은 34개 고정 질문,
두 번째는 실제 HTTP SSE의 7턴 연속 대화다. 동시에 여러 배치를 실행하지 않는다.

```sh
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/evaluate_final_demo.py \
  --live --mode chat --workers 1 --freeze-source \
  --fixture apps/assistant/tests/fixtures/final_demo_complete.json \
  --output /tmp/final_demo_chat.json
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/evaluate_demo_conversations.py \
  --live --url http://127.0.0.1:8000 --output /tmp/final_demo_conversation.json
```

모델·DB의 실제 호출은 `--live`로 명시한다. 독립 검산은 생성된 SQL을 재사용하지 않고
원본 스냅샷과 모델 행을 Python으로 재집계한다. 각 실험의 원문 답변·SQL·소요 시간·소스 해시는
`apps/assistant/output/evals/final_adv_20260912/`에 보관한다. 특정 실행의 통과율을
전체 자유 질의 정확도로 일반화하지 않는다.

저장한 실험의 결과물 계약은 외부 호출 없이 재검사할 수 있다.

```sh
.venv/bin/python apps/assistant/scripts/audit_final_demo_contracts.py \
  apps/assistant/output/evals/final_adv_20260912/chat_complete_iteration6.json
node apps/web/tests/question-guide-render.mjs /tmp/fab_question_guide_render.json
```

외부 모델 없이 SQL 집계 계약을 넓게 확인하려면 다음 명령을 사용한다. 원본 스냅샷과 카탈로그를
저장한 평가 폴더가 필요하며, 생성기는 질의 구현을 가져오지 않고 명시한 지표 계약에서 296개 조합을 만든다.

```sh
.venv/bin/python apps/assistant/scripts/build_snapshot_contract_grid.py \
  --output /tmp/fab_contract_grid.json
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/evaluate_snapshot_variants.py \
  --execute --fixture /tmp/fab_contract_grid.json \
  --catalog apps/assistant/output/evals/final_adv_20260912/catalog_baseline.json \
  --raw apps/assistant/output/evals/final_adv_20260912/reference_raw.json \
  --output /tmp/fab_contract_grid_result.json
```

모델 장애 대체 경로는 아래처럼 별도 실행한다. 기존 서버 설정과 사용자 대화는 바꾸지 않는다.
이 명령은 모델 클라이언트와 모든 HTTP를 차단하고, localhost DB와 로컬 BM25만 허용한다.
문서 답변의 인용 검토에 모델이 필요한 경우에는 미검증 답변을 성공으로 처리하지 않는다.

```sh
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/evaluate_demo_offline.py \
  --local-db --fixture apps/assistant/tests/fixtures/final_demo_complete.json \
  --output /tmp/fab_model_unavailable.json
```

집계 생성기에 `--mixed-metrics`를 주면 복합 지표 28문항, `--temporal-metrics`를 주면
추세·기간 순위 224문항을 별도 생성한다. 두 옵션은 동시에 사용하지 않는다.
연속 대화의 SSE 라우트와 저장소 재로딩은 다음 명령으로 검증한다. 임시 SQLite를 사용하고 종료 후 지운다.

```sh
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/evaluate_demo_offline.py \
  --local-db --conversation --stream-route --restart-memory-every-turn \
  --fixture apps/assistant/output/evals/final_adv_20260912/offline_conversation_final_fixture.json \
  --raw apps/assistant/output/evals/final_adv_20260912/reference_raw.json \
  --output /tmp/fab_sse_restart.json
```

“그 공정”은 완전하게 반환된 성공 SQL 결과에서 대상이 하나일 때만 자동 연결한다.
대상이 여럿이면 공정명을 확인하며, “그 공정들”은 조회된 대상들을 이어받는다.
답변 본문에 등장한 예시나 실패한 조회의 행을 참조 대상으로 저장하지 않는다.
질문에서 FAB12를 지정한 뒤 사이드바의 FAB11 값이 그대로 전달되어도 후속 질문은 FAB12를 유지한다.
사이드바를 실제로 다른 FAB으로 변경하거나 질문에서 새 FAB을 지정하면 새 선택을 적용한다.

저장된 결과를 이용한 UI 확인 페이지는 Vite 실행 후 `/tests/visual/`에서 열 수 있다.
실모델 재실행 결과가 아니라는 표시를 유지하며, 실시간 시연 성공을 대체하는 자료로 보고하지 않는다.

### 동시 대화와 장시간 반복 시험

다음은 외부 모델을 호출하지 않는 로컬 시험이다. 첫 명령은 네 FAB의 별도 대화 104턴을,
두 번째는 같은 이벤트 루프에서 질문 가이드 52문장을 27분 동안 반복한다.
두 시험 모두 임시 대화 저장소를 쓰며, 실제 DB는 SELECT로만 읽는다.
문서 답변의 모델 검토 미완료는 예상한 장애 상태로 확인하지만, 답변 성공 건수에 포함하지 않는다.

```sh
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/evaluate_demo_concurrent.py \
  --fixture apps/assistant/output/evals/final_adv_20260912/question_guide_fixture.json \
  --raw apps/assistant/output/evals/final_adv_20260912/reference_raw.json \
  --output /tmp/fab_concurrent.json
PYTHONPATH=apps/assistant/src .venv/bin/python apps/assistant/scripts/soak_demo_local.py \
  --fixture apps/assistant/output/evals/final_adv_20260912/question_guide_fixture.json \
  --raw apps/assistant/output/evals/final_adv_20260912/reference_raw.json \
  --duration-seconds 1620 --interval-seconds 30 --output /tmp/fab_stability.json
```

반복 시험의 메모리·스레드·열린 파일 수는 관측 기록이다. 실모델 부하 시험이나 장기 메모리 누수의
부재를 보증하는 시험으로 해석하지 않는다. 시험 중 애플리케이션 소스가 바뀌면 실행을 중단한다.

최종 실행은 20:56~21:23 KST에 54회 순환을 마쳤다. 10회차 이후 Python 추적 메모리는
2.978~3.007 MB였고, 프로세스의 최대 상주 메모리는 120.297 MB, 스레드는 2개,
열린 파일은 9개로 관측됐다. 같은 코드에서 모델이 실제로 응답하는 경우의 자원 사용량은
별도 측정이 필요하다.

최신 보고서와 재현 입력 파일의 경로·해시는
[`validation_index.json`](../apps/assistant/output/evals/final_adv_20260912/validation_index.json)에 모았다.
서로 다른 코드 시점의 실험을 한 성공률로 합치지 않도록 실모델·로컬 검산·장애 시험을 구분한다.

## 기획 예시와 데모 데이터의 대응

원래 설계의 A/M2 라인·A제품은 사용자 상황을 설명하는 예시다. 현재 공통 스냅샷의 조회 단위는
`FAB + area(공정 영역)`이므로 데모에서는 FAB11~13과 etch·photo 등의 실제 존재하는 식별자를 사용한다.
라인·제품 수율을 임의로 생성하거나 공정 값을 같은 값으로 간주하지 않는다.
SC-003은 원래 PoC 우선순위에서 제외되어 있었으며, 여기서는 보조 시연으로 가동률 변화의
1차 처리량 민감도를 보여준다. 공정 지연으로 인한 수율 손실 1.8%p 같은 인과 수치는
보정 모델과 대응 데이터가 없으므로 재현했다고 주장하지 않는다.

## 이후 추가 요청: FAB 간 비교와 대화 복원

사진의 FAB11·FAB13 비교 질문, 브라우저 영구 저장, 운영 중심 답변 표현을 추가로 개선했다.
최신 검증은 [대화·비교 후속 개선 기록](final_adv_conversation_followup.md)을 참조한다.
위의 4시간 실행 결과는 당시 코드에 대한 기록으로 유지한다.
