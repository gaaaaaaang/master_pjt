# 초기 아키텍처

```text
사용자
  -> frontend 채팅 UI
  -> FastAPI /api/chat 또는 /api/chat/stream(SSE)
  -> LangGraph
  -> Planner -> Supervisor
  -> 선택 Agent -> Agent Reflection
  -> Post-execution Supervisor
       -> continue: 다음 계획 Agent
       -> retry_same_agent: 같은 Agent (agent별 최대 1회)
       -> replan: Planner (최대 1회)
       -> alternate_agent: 호환 Agent (최대 1회)
  -> Verifier/Self-reflection
       -> compose: Answer Composer
       -> replan: Planner
       -> retry_target: 특정 Agent
       -> human_review: 검토 필요 상태로 Composer
  -> Answer Supervisor
       -> 원 질문/계획/근거/한계와 최종 답변 직접 대조
       -> 누락 또는 왜곡 시 근거 범위에서 1회 교정 후 재검증
```

`/api/chat`과 `/api/chat/stream`은 동일한 LLM LangGraph를 실행한다. Planner와 Supervisor는
각각 독립된 Azure Chat Completions structured-output 호출로 계획과 실행 승인을 결정한다.
`/api/chat/stream`은 각 LangGraph node 완료 시 trace event를 보내고, Text2SQL event에는
semantic query plan, 생성 SQL, 조회 column/row count/sample row를 포함한다. Visualization
event는 chart type과 x/y encoding 및 조회 row를 포함한다. Reflection은 SQL/tool 근거와 한계를
LLM으로 검토하고, Composer는 그 검토 지시를 반영해 최종 답변을 생성한다. Composer 이후 Answer
Supervisor가 사용자 질문의 식별자, 지표, 비교 대상, 날짜 기준이 답변에 반영됐는지 deterministic
검사와 LLM structured review를 함께 수행한다. 최종 event는 answer, SQL, chart, evidence,
limitations, reflection, answer_review를 함께 반환한다.
Deterministic answer review는 explicit/relative 날짜 문맥, status 조회 sample의 실제 요청 metric 값,
사용자에게 보이는 limitation, Impact 입력/계산/가정 경계도 검사한다. 결과는 question alignment,
evidence grounding, limitation visibility, provenance calibration 네 품질 차원과 `quality_score`로
기록된다. Scenario evaluator는 LLM의 승인 결과와 별개로 이 verifier를 재실행한다.

`evaluate_answer_quality.py`는 SC-001~SC-004마다 grounded positive 답변과 의도적으로 결함 있는
negative 답변을 함께 평가해 false accept와 false reject를 모두 측정한다. Local release gate는
Text2SQL EX/EM/intent/coverage, RAG와 CaseSearch Recall@3/MRR, Impact contract, Answer Quality의
기본 18개 metric을 `agent_quality_baseline.json`과 비교한다. baseline 하락, minimum 미달, metric 누락은
각각 실패이며 결과는 `output/evals/release_quality.json`에 기록된다. 외부 Azure 호출이 필요한
full graph 평가는 이 local gate와 합쳐서 주장하지 않고 `not_measured`로 유지한다.

Baseline v2는 adversarial fixture의 agent별 pass rate를 포함해 총 26개 metric을 검사한다.
Adversarial normalization은 FAB/제품/설비의 공백·하이픈 표기, slash/dot 날짜, `지난 일주일`,
한국어/영어 percentage-point 표현을 canonical slot으로 바꾼다. RAG는 장비 멈춤/정지/비가동
paraphrase를 equipment-down intent로 확장한다. Visualization은 첫 행만이 아니라 모든 row의
encoding field를 확인하고 numeric string을 유한 수로 정규화하며 NaN/누락 값을 거부한다.
Text2SQL adversarial 11건은 composite DB 실행 모드에서 실제 read-only EX까지 수행한다.

Baseline v3는 unrelated query 8건의 retrieval abstention을 더해 총 30개 metric을 검사한다.
Local RAG는 모든 score가 0일 때 임의 top-1을 반환하지 않는다. FAB identifier만 겹치는 문서도
관련 근거로 보지 않으며, identifier를 제외한 semantic overlap 또는 issue-intent match가 있어야
결과를 반환한다. 빈 RAG 결과는 LangGraph에서 `data_unavailable`과 limitation으로 전파된다.
Positive Recall@3/MRR과 negative abstention을 별도 metric으로 유지해 과도한 검색과 과도한 거절을
동시에 감시한다.

Baseline v5는 SC-004 날짜 해석 변형을 추가한다. Text2SQL은 명시적 달력 월과 단일/범위 분기를
exclusive-end `[start, end)` 범위로 정규화하고, 분기와 여러 달 범위는 기본 월 버킷으로 집계한다.
`일별`, `주별`, `월별` grain은 각각 실제 SQL 날짜 표현식과 chart x축에 반영된다. 역전된 날짜,
존재하지 않는 월이나 분기는 SQL 생성 전에 `needs_clarification`으로 차단한다. `이번 분기`와
`지난 분기`, `this quarter`와 `last quarter`도 현재 달력 기준의 bounded range로 변환한다.

Baseline v6는 diagnosis synthesis 5건과 5개 metric을 추가해 총 35개 metric을 검사한다.
관측 SQL, playbook 후보, 유사사례의 source coverage와 missing evidence를 분리하고 모든 결론을
`candidate_only`로 제한한다. 사례 `issue_type`은 진단 taxonomy로 필터링하며 `station_down`은
`equipment_down`으로 정규화한다. `case_type=verified`여도 source가 synthetic, simulation,
generated, mock이면 `unverified_reference`로 강등하고 verified corroboration으로 세지 않는다.

Baseline v7은 multi-turn context 6건과 5개 metric을 추가해 총 40개 metric을 검사한다.
Conversation memory는 `FAB 10`/`FAB-10`, 공정과 설비의 공백·하이픈 표기, product/route,
`start-date`/`due-date`를 canonical context로 저장한다. 우선순위는 명시 request field, 현재 발화의
식별자, 과거 대화 context 순서이며, 대화 중 FAB·제품·공정·설비를 바꾼 뒤의 지시어가 이전 대상으로
회귀하지 않는지 별도 switch accuracy로 측정한다.

Baseline v8은 Answer Quality fixture를 12건으로 확장한다. Answer Supervisor의 deterministic
verifier는 ISO/slash/dot 날짜와 주 단위뿐 아니라 `YYYY년 M월`, 단일/범위 분기, 이번/지난
월·분기를 질문의 필수 date context로 검사한다. 의미가 같은 `2020년 1분기`와 `2020 Q1` 표기는
허용하지만 기간 자체가 최종 답변에서 사라지면 question alignment 실패로 처리한다.

Baseline v9는 Visualization adversarial fixture를 5건으로 확장한다. 복수 지표 temporal chart는
long-form `x/metric/value`와 color encoding을 유지하고 React renderer가 metric별 polyline과 point를
분리한다. 단일 지표라도 실제 결과 column이 series로 지정되면 같은 multi-line 계약을 사용하며,
지원하지 않는 chart type은 임의 line chart로 표시하지 않고 거부한다. 브라우저 deterministic E2E에서
발견한 Composer fallback의 식별자·기간 누락도 수정해, verifier와 동일한 canonical context를
`요청 범위`로 포함한 뒤 Answer Supervisor를 통과하도록 했다.

Baseline v10은 SC-002의 자연어 변형을 retrieval positive fixture에 추가해 Local RAG 10건과
CaseSearch 6건을 모두 Top-1으로 고정한다. 알람·재공 정체, 납기 준수율 악화, 예방정비 지연 표현을
동의어 intent로 확장하고 한국어 조사를 제거한다. 명시적 PM intent는 일반 equipment-down보다
우선해 `pm_delay` 문서를 선택하며, unrelated query 8건의 abstention 1.0도 함께 유지한다.

Baseline v11은 CaseSearch가 일반 도메인 명사 하나만 겹치는 사례를 반환하지 않도록 질문과 사례가
Queue/WIP/병목/다운/PM/납기 중 하나의 incident signal을 공유해야 한다는 반환 계약을 추가한다.
제품 수율 오염, 작업자 교대 품질, 설비 센서 보정처럼 현재 case corpus가 지원하지 않는 근거리
negative 3건을 추가해 retrieval abstention 11/11을 고정한다.

Baseline v12는 diagnosis synthesis가 diagnostic taxonomy가 없는 RAG chunk와 similar case를
원인 후보, source coverage, verified corroboration에서 제외하도록 한다. 제외 근거는
`excluded_evidence`로 보존하고 `evidence_eligibility_rate`를 추가해 총 41개 release metric을
검사한다. SQL 관측만 있는 경우와 playbook 후보만 있는 경우도 별도 fixture로 고정해 부분 근거를
실제 원인 확정으로 확대하지 않는다.

Baseline v13은 candidate-only synthesis에 유효한 원인 후보가 0개일 때 Answer Supervisor가
최종 답변에 후보 제시 불가를 명시하도록 검사한다. SQL 관측값만 있는 상황의 정상 답변과 근거 없이
장비 고장 후보를 추가한 결함 답변을 함께 고정해 Answer Quality 14건의 positive accept와 negative
reject를 모두 1.0으로 유지한다.

Baseline v14는 SC-003 변화량 방향을 질문 전체 키워드가 아니라 수치에 가장 가까운 증가/감소 절에
결합한다. 명시적 `+/-` 부호와 방향어가 충돌하거나 방향 자체가 없으면 계산을 거절하고,
utilization baseline/projected 값은 0~100%, cycle time은 양수 범위를 벗어나지 않아야 한다.
Impact fixture는 정상 계산과 안전 거절을 합쳐 9건으로 확장한다.

Baseline v15는 SC-004 line chart의 ISO 날짜/시간 x축을 오름차순으로 정규화한다. 동일
`(x, series)`가 중복되면 프런트에서 한 행을 조용히 버리지 않도록 chart 생성을 거절하고, 존재하지
않거나 x/y와 충돌하는 series 및 multi-metric에 추가 source series를 지정한 계약도 거절한다.
Visualization adversarial fixture는 8건, 전체 adversarial fixture는 27건으로 확장한다.

Baseline v16은 Planner가 하나의 primary query type을 유지하면서 명시적 복합 요청의 호환 agent를
삭제하지 않도록 한다. diagnosis와 수치 impact가 함께 있으면 Impact를, diagnosis와 trend가 함께
있으면 Visualization을 필수 route 뒤에 canonical graph 순서로 보존한다. LLM plan normalization과
deterministic fallback 양쪽을 검사하며 Planner adversarial 5건, 전체 29건을 고정한다.

Baseline v17은 compound diagnosis의 downstream route에 맞춰 Text2SQL 하위 intent를 선택한다.
Visualization이 포함된 diagnosis는 `trend` SQL과 chart intent를 만들고, Impact가 포함된 diagnosis는
`status` SQL로 현재 numeric baseline을 조회한다. 두 compound 질문의 template, slot, SQL fragment를
DB 실행 대상 adversarial fixture에 추가해 Text2SQL 13건, 전체 31건을 고정한다.

Baseline v18은 compound route를 LangGraph 통합 수준에서 검증한다. diagnosis+trend는 Text2SQL,
RAG, CaseSearch, Visualization을 순서대로 실행하고 정렬된 line chart를 남겨야 한다.
diagnosis+impact는 같은 앞단 근거 수집 후 status baseline으로 Impact를 실행하고 계산 evidence를
남겨야 한다. Azure 호출은 stub 처리하되 실제 dispatcher와 specialist node를 사용한다.

Baseline v19는 Answer Supervisor의 question alignment를 식별자·지표·날짜뿐 아니라 요청 작업
facet까지 확장한다. compound 답변은 trend/chart와 impact calculation 부분을 모두 다뤄야 하며,
성공한 Impact evidence가 있으면 baseline이 아닌 계산 estimate 값 하나 이상과 계산 경계를 답변에
포함해야 한다. 정상/누락 쌍 4건을 추가해 Answer Quality 18건을 고정한다.

Baseline v20은 SC-001 상태 답변의 SQL 값 grounding을 요청 지표별·대상별로 검증한다. 여러 지표 중
하나의 값만 제시하거나 여러 Product 중 일부 대상의 값만 제시한 답변은 거절한다. WIP처럼 여러 SQL
컬럼이 같은 지표를 표현하는 경우에는 실제 반환된 대체 컬럼 값 중 하나를 허용하며, 정상/누락 쌍
4건을 추가해 Answer Quality 22건을 고정한다.

Baseline v21은 SC-001 상태 답변에 포함된 숫자 claim을 질문의 사용자 입력값과 성공한 SQL sample
row의 numeric cell에 대조한다. FAB/Product/설비 식별자와 날짜 내부 숫자는 claim에서 제외하고,
질문에 명시된 임계값은 허용하지만 SQL에 없는 증감률이나 운영 수치는 거절한다. positive/negative
2건을 추가해 Answer Quality 24건을 고정한다.

Baseline v22는 SC-004에서 성공한 chart specification을 `visualization_spec` evidence로 기록한다.
명시적인 그래프·차트 요청에 대해 최종 답변이 생성 성공을 주장하려면 이 evidence가 있어야 하며,
생성 실패 시에는 답변에 불가 사실을 명시해야 한다. positive/negative 2건을 추가해 Answer Quality
26건을 고정한다.

Baseline v23은 SC-003 Impact 답변의 모든 숫자 claim을 질문 변화량, SQL baseline, 계산 evidence의
inputs·estimates·formulae에 대조한다. 계산식 상수와 모델 차수 표기는 허용하지만 근거 없는 추가
영향값은 거절한다. positive/negative 2건을 추가해 Answer Quality 28건을 고정한다.

Baseline v24는 Impact의 `delta`·`change` estimate 부호와 답변의 증가·감소 표현을 대조한다.
음수 estimate를 증가로 설명하는 답변은 거절하고, 음수 부호 대신 절댓값과 감소 표현을 조합한
자연어 답변은 허용한다. positive/negative 2건을 추가해 Answer Quality 30건을 고정한다.

Baseline v25는 SC-002 diagnosis 답변의 숫자 claim을 질문, SQL 관측 row, RAG·유사 사례와
diagnosis synthesis evidence에 대조한다. `SIM-001` 같은 사례 식별자의 숫자는 claim에서 제외하고,
근거에 없는 추가 운영 수치는 거절한다. positive/negative 2건을 추가해 Answer Quality 32건을
고정한다.

Baseline v26은 한 문장 안에 입력 변화와 결과 변화의 방향어가 함께 있을 때 estimate 숫자에 가장
가까운 방향어를 결합한다. 입력의 감소 표현이 결과의 잘못된 증가 표현을 가리지 못하도록 하며,
positive/negative 2건을 추가해 Answer Quality 34건을 고정한다.

Baseline v27은 line chart의 metric/series별 시작값·종료값·절대 변화·변화율을 정렬된 query row에서
결정적으로 계산해 `visualization_spec` evidence에 기록한다. SC-004 trend 답변 숫자를 원본 SQL 값과
이 파생 summary에 대조해 근거 없는 변화율을 거절한다. Answer Quality 36건과 adversarial 32건을
고정한다.

Baseline v28은 visualization summary의 `percent_delta` 부호와 trend 답변의 증가·감소 표현을
대조한다. 다중 series에서는 해당 metric/series가 같은 문맥에 있는 변화율만 검사해 선 간 방향을
혼동하지 않는다. positive/negative 2건을 추가해 Answer Quality 38건을 고정한다.

Baseline v29는 DB 실행 release gate 앞에 read-only PostgreSQL preflight를 둔다. DB가 내려간 경우
Text2SQL EX/EM 회귀로 오분류하지 않고 metric 계산 전에 `evaluation_status`를
`infrastructure_unavailable`로 종료하며, 접속 비밀을 제외한 오류 유형만 결과에 기록한다.

Baseline v30은 원인+추세+영향 복합 질문에서 변화량이 없더라도 명시적 Impact 요청을 보존한다.
Impact는 계산 불가 사유를 구조화해 반환하며, LLM Planner가 Impact 또는 Visualization을 누락해도
질문 기반 route normalization이 복구한다. adversarial 33건을 고정한다.

Baseline v31은 명시적 비교 표현이 없는 복수 Product status 질문도 `products` slot 전체를 보존한다.
최신 AutoSched snapshot에서 `part IN (...)`으로 각 Product 행과 요청된 WIP·cycle time 컬럼을
반환하며, Text2SQL local PostgreSQL EX/EM fixture를 59문항으로 확장한다.

Baseline v32는 한국어 조사와 붙은 복수 장비 ID 및 추가 숫자 suffix를 보존한다.
Text2SQL은 장비별 복수 지표 상태·추세 SQL을 생성하고, Visualization은 `장비 / 지표` 조합
series를 구성한다. Answer Supervisor는 각 장비 구간마다 요청 지표 값이 실제 SQL 결과와 함께
답변에 포함됐는지 검증한다. Text2SQL fixture는 62문항으로 확장한다.

Baseline v33은 복수 장비 추세 답변을 `visualization_spec.trend_summary`와 다시 대조한다.
각 요청 장비·지표 조합에 근거값이 하나도 없으면 Answer Supervisor가 답변을 거절하며,
deterministic Composer와 Planner의 복수 장비 추세 경로도 같은 계약으로 회귀 검증한다.

Baseline v34는 SC-002 검색에서 대기열·체류, 막힘, 장비 정지, 정기점검 지연 등 한국어
표현 변형을 canonical incident concept로 확장한다. CaseSearch는 질문의 issue intent와
사례 metadata의 `issue_type`을 대조해 명시적 intent가 다른 사례를 제거하고, 지원 사례가
없는 PM 요청에는 빈 결과를 반환한다.

Baseline v35는 SC-003 Impact에서 상대 percent와 복수 변화량을 각각 metric에 결합한다.
가동률과 cycle time을 함께 요청하면 최신 `autosched_perf` 행과 같은 report/period의
`autosched_stngrp.util_percent` 평균을 한 baseline 행으로 결합한다. PostgreSQL duration
형식의 `cycleavg`는 시간으로 변환하고 unit provenance를 남기며, Answer Supervisor는
복합 계산에서 지원되는 estimate가 하나라도 누락되면 답변을 거절한다.

Baseline v36은 SC-002 Diagnosis 후보를 verified case, playbook, 관측 metric 정합성,
교차 출처 지지로 합산해 우선순위를 만든다. 유효 taxonomy 필터를 Top-K보다 먼저 적용하고,
출처가 완전히 일치하지 않으면 `partially_aligned` 또는 `competing_hypotheses`로 표시한다.
Answer Supervisor는 복수 후보 답변이 `candidate_rankings`의 1순위 후보를 누락하면 거절한다.

Baseline v37은 SC-004 line chart의 x축을 일관된 temporal 또는 numeric domain으로 제한한다.
숫자 문자열은 수치 순서로 정렬하고, 서로 다른 문자열로 표현된 동일 시점은 정규화된 축 키로
중복 검출한다. 순서 의미가 없는 범주형 x축은 추세선으로 렌더링하지 않고 거절한다.

Baseline v38은 multi-turn release-plan 문맥에서 Text2SQL과 동일한 한국어 날짜 기준 별칭을
사용한다. `납기/납기일`은 `due_date`, `투입일/릴리즈일`은 `start_date`로 저장하며 현재 turn의
날짜 기준 전환이 과거 문맥보다 우선한다. 상속 정확도는 별도 release metric으로 관리한다.

Baseline v39는 deterministic Planner가 `현재/지금/값/수치`가 포함된 FAB metric 질문을
`뭐야`라는 표현만으로 knowledge lookup으로 오분류하지 않도록 status 신호를 우선한다.
Impact 질문에 비교 차트·그래프·시각화가 명시되면 Visualization을 downstream agent로 추가한다.

Baseline v40은 Text2SQL status 질문의 metric 임계값과 Top-N을 deterministic slot으로 보존한다.
허용된 numeric metric과 `>=`, `<=`, `>`, `<`만 SQL predicate로 조립하고, 상위/하위 방향을
각각 NULLS LAST의 DESC/ASC 정렬로 변환하며 사용자 지정 1~200 범위 LIMIT을 적용한다.

Baseline v41은 임계값의 `퍼센트/percent` 표기를 `%`와 동일하게 처리한다. 상위/하위 개수가
1~200 범위를 벗어나면 기본 LIMIT으로 조용히 대체하지 않고 clarification을 반환해 사용자 조건의
의미가 바뀐 SQL 실행을 차단한다.

Baseline v42는 Answer Supervisor가 status 질문의 임계값과 Top-N 선택 조건을 최종 답변과
대조한다. 결과 metric 값이 맞더라도 비교값·연산 방향 또는 상위/하위 개수를 생략하면
`question_alignment` 실패로 거절한다.

Baseline v43은 CaseSearch 질문과 사례 양쪽에 `equipment_type` 또는 `process_group`이 명시된
경우 서로 다른 대상을 같은 issue type이라는 이유만으로 반환하지 않는다. 사례에 대상 메타데이터가
없는 경우는 기존 recall을 유지하고, 명시적 충돌만 제외해 target-specific precision을 강화한다.

Baseline v44는 status 질문의 명시 metric을 선택된 AutoSched 테이블 schema와 대조한다. 대상
테이블에 없는 임계값·정렬 metric은 조건을 제거한 SQL로 바꾸지 않고 `data_unavailable`을 반환하며,
상위/하위 N에 정렬 metric이 없으면 clarification을 요구한다.

Baseline v45는 개수가 없는 `높은 순`, `낮은 순`, `상위`, `하위`, 오름차순·내림차순 표현도
status 정렬 방향 slot으로 보존한다. metric이 명시되면 해당 metric을 첫 ORDER BY로 사용하고,
metric 없는 정성적 순위 요청은 임의 기준으로 정렬하지 않고 clarification을 반환한다.

Baseline v46은 status 임계값에서 `>=`, `<=`, `>`, `<` 기호와 `at least`, `at most`,
`above`, `below` 계열 영문 표현을 지원한다. 모든 표현은 기존 숫자 parser와 비교 연산자
allowlist로 정규화되어 사용자 입력이 SQL 조각으로 직접 전달되지 않는다.

Baseline v47은 임계값의 단위를 `threshold_unit` slot으로 보존한다. utilization, ontime,
down, PM, processing 비율은 0~100 범위를 벗어나면 clarification을 반환하고, WIP·cycle time 등
비율이 아닌 지표에 percent 단위가 붙으면 단위를 제거한 SQL로 실행하지 않는다.

Baseline v48은 Answer Supervisor의 선택 조건 검증을 Text2SQL 표현 범위와 맞춘다. 기호·영문
임계값과 개수 없는 정성적 정렬을 최종 답변의 한국어·영문 동치 표현과 대조하며, 결과값이 맞아도
비교 방향이나 정렬 방향을 생략한 답변은 `question_alignment` 실패로 거절한다.

Baseline v49는 ConversationMemory에 metric context를 추가한다. 현재 발화의 명시 metric을 우선하고,
후속 지시 표현이나 metric 없는 bare threshold가 있을 때만 직전 metric을 상속한다. 이 context는
Planner와 Text2SQL에 전달되어 `그중 80% 이상만 상위 5개` 같은 후속 질문을 status SQL로 보존한다.

Baseline v50은 Impact에서 같은 metric의 변화량이 한 시나리오에 여러 번 등장할 때 첫 변화만
자의적으로 적용하지 않는다. 승인된 순차·합성 규칙이 없으므로 해당 metric의 모든 계산을 보류하며,
cycle time의 0 이하 projection도 명시적으로 회귀 평가한다.

Baseline v51은 incident RAG chunk의 metadata와 본문에 선언된 모든 `issue_type`을 함께 읽는다.
질문에 명시 issue intent가 있고 chunk의 구체 issue type이 모두 불일치하면 local 및 Milvus 결과에서
제외한다. `all`만 선언된 공통 지침은 허용하며 incident evidence alignment는 별도 metric으로 관리한다.

Baseline v52는 RAG evidence에 `query_issue_intents`와 `matched_issue_types`를 기록하고 Diagnosis가
이를 원래의 첫 `issue_type`보다 우선한다. 복합 chunk에서는 질문과 매칭된 issue만 후보화하며,
`issue_aligned=false` 근거는 candidate와 source coverage에서 제외한다.

Baseline v53은 bar/grouped-bar의 범주 순서를 SQL 결과 순서로 고정해
`encoding.x.sort`에 기록한다. 동일 `(x, series)` 범주 키가 중복되면 렌더러가 첫 값을 임의로
선택하지 않도록 chart 생성을 거절한다. 웹은 단일-series `bar`를 line으로 그리던 분기를 분리하고,
서버가 지정한 범주 순서를 그대로 사용하는 전용 bar renderer를 사용한다.

Baseline v54는 chart spec의 `encoding.y.domain`을 전체 y 값과 0 기준선에서 계산한다. 단일 line,
bar, grouped-bar 렌더러는 이 domain을 공통으로 사용해 음수 변화량도 유효한 좌표와 양수 높이의
막대로 표현한다.

Baseline v55는 multi-series line의 전체 x축 대비 series별 누락 시점을 `series_gaps`로 기록하고
`visualization_spec` evidence까지 전달한다. 웹은 누락 시점 전후 관측치를 하나의 선으로 연결하지
않고 연속 관측 구간별 polyline으로 분할한다.

Baseline v56은 Visualization의 `series_gaps`를 material limitation으로 graph state에 올린다.
Answer Supervisor는 sparse trend 답변이 누락 series와 누락 x 값을 명시했는지 검사하며, endpoint
변화만 제시해 중간 관측 누락을 숨기는 답변은 최종 승인하지 않는다.

Baseline v57은 line series별 `point_count`, 전체 축의 `axis_point_count`, `coverage_rate`,
`assessment`를 `series_coverage`로 기록한다. 2점 미만 series는 `trend_summary`에서 제외하고
`insufficient` limitation으로 전달한다. Answer Supervisor는 한 시점 값을 변화 없음으로 해석하는
답변을 거절하고, 관측 부족으로 추세를 판단할 수 없다는 공개를 요구한다.

Baseline v58은 공통 x축 대비 series coverage가 0.5 미만이면 관측치가 2개 이상이어도
`assessment=insufficient`, `reason=coverage_below_0.5`로 분류해 `trend_summary`에서 제외한다.
Answer Supervisor는 관측 수·전체 축·누락 시점과 추세 판단 불가를 공개한 답변만 승인한다.

Baseline v59는 Text2SQL line chart intent에 `grain`, `range_start`, `range_end_exclusive`,
`missing_policy`를 보존한다. Visualization은 명시 범위에서 단일-series calendar gap을 검출한다.
AVG 계열은 `gap`으로 남기고 COUNT 계열은 빈 bucket을 0으로 채우되 `source_rows`와
`imputed_points` provenance를 함께 제공한다. 기대 축은 최대 1000개로 제한한다.

Baseline v60은 Answer Supervisor가 `imputed_points`를 근거로 COUNT의 빈 calendar bucket을
0으로 보정했다는 사실을 최종 답변에 명시하도록 강제한다. 보정값을 실제 관측값처럼 제시하는
답변은 거절하고, 빈 구간과 zero-fill 동작을 함께 공개한 답변만 승인한다.

Baseline v61은 네 개의 명시 날짜를 서로 겹치지 않는 두 비교 범위로 파싱하고, 각 범위를
`comparison_period`로 만드는 조건부 집계 SQL을 생성한다. 겹치는 범위는 clarification으로
거절한다. Answer Supervisor는 비교 결과의 각 기간 라벨과 요청 지표 값이 함께 제시됐는지 검사해
한 기간의 결과만 설명하는 답변을 승인하지 않는다.

Baseline v62는 verified incident에 `verified_at`, `verified_by`, `evidence_refs`를 요구한다.
CaseSearch는 이 계약이 불완전한 verified 레코드를 적재 시점에 거절하고, Diagnosis는 외부 evidence도
동일 계약과 non-synthetic source를 모두 만족할 때만 `verified_analogy`로 인정한다. 최종 diagnosis
답변은 사용한 verified case ID를 명시해야 하며, incident ID의 숫자는 운영 수치 주장으로 세지 않는다.

Baseline v63은 incident store 전체에서 `case_id` 유일성을 강제하고, synthetic/simulated source가
`verified`를 선언한 레코드를 거절한다. `verified_at`은 timezone offset이 있는 ISO-8601 timestamp여야
한다. CaseSearch release 평가는 매 실행 시 전체 store를 로드하므로 이 무결성 위반도 gate 실패로
처리된다.

Baseline v64는 상위 CaseSearch 결과가 simulated 사례만으로 채워졌을 때, 최하위 선택 점수의 75%
이상인 verified 사례가 있으면 한 슬롯을 예약한다. 동일 관련도의 verified provenance가 저장 순서 때문에
Diagnosis에서 사라지는 문제를 막되, 임계값 미만의 낮은 관련도 사례는 승격하지 않는다.

Baseline v65는 질문과 incident metadata에 FAB가 모두 명시되면 exact FAB match를 요구한다. 같은
관련도의 verified 사례끼리는 `verified_at` 최신순으로 반환한다. fab10 전용 simulated corpus에도
`fab_id` provenance를 부여하고 fab11 질의를 negative fixture로 추가해 cross-FAB 사례 혼입을 막는다.

Baseline v66은 CaseSearch evidence에 구조화된 `cause`, `actions`, `outcome`을 보존한다. 동일 issue의
verified 사례가 서로 다른 cause를 기록하면 Diagnosis를 `verified_case_conflict` 및
`conflicting_candidate`로 강등하고 `resolve_verified_case_conflict`를 요구한다. Answer Supervisor는
두 case ID와 원인 충돌을 함께 공개하지 않은 최종 답변을 거절한다.

Baseline v67은 verified contract에 timezone-aware `incident_at`을 추가하고 `verified_at`이 사건보다
빠른 레코드를 거절한다. 질문의 명시 날짜 구간과 사건 시점을 비교해 기간 밖 사례의 검색 점수를
0.8배로 낮추고 `verified_historical_analogy`로 분리한다. 두 비교 기간 사이 공백은 aligned로 보지
않는다. Answer Supervisor는 과거 사례가 현재 질문의 직접 시간 정합 근거가 아님을 공개하도록 한다.

Baseline v68은 Impact 변화량과 방향어 사이에 capacity, output, throughput, 납기 같은 결과 지표가
끼어 있으면 그 방향어를 입력 변화 방향으로 결합하지 않는다. `utilization이 5%p 변하면 capacity가
감소하나`와 같은 질문은 utilization 감소 시나리오로 오해해 계산하지 않고 방향 clarification이 필요한
`data_unavailable`로 유지한다.

Baseline v69는 Impact baseline에 서로 다른 FAB, product, part, route, station 또는 기간 값이 섞이면
숫자 열의 평균으로 단일 영향값을 만들지 않는다. 혼합 차원을 provenance에 기록하고 대상·기간별
baseline을 요구하는 `data_unavailable`을 반환하며, 동일 대상의 반복 관측에 대해서만 평균을 허용한다.

Baseline v70은 Answer Supervisor가 Impact의 혼합 baseline 차원과 분리 재계산 필요성을 최종 답변에서
검사한다. 단순히 데이터가 부족하다고만 답해 실제 거절 원인을 숨기는 응답은 허용하지 않는다.

Baseline v71은 혼합 station-group baseline 거절을 adversarial gate에 포함하고, Impact의
`mixed_dimensions`를 evidence뿐 아니라 agent run과 stream trace에도 기록한다.

Baseline v72는 RAG와 CaseSearch의 issue intent 추출에 원문 국소 부정 범위를 적용한다. “장비 down은
아니고 Queue Time 증가”에서 equipment-down 근거를 제외하되, 부정 뒤 같은 issue가 다시 긍정되면
제거하지 않는다.

Baseline v73은 RAG knowledge-base 자동 선택에서도 부정된 incident issue의 확장 토큰을 제외한다.
“장비 고장은 아니고 CMP 공정 설명”은 incident playbook이 아니라 process basics로 라우팅한다.

Azure orchestration 호출이 실패하면 query type별 필수 route invariant를 사용하는 deterministic
Planner로 강등한다. Supervisor, recovery, final reflection, Composer, Answer Supervisor도 기존
deterministic 검증과 tool summary를 사용해 bounded fallback을 수행하며 trace에
`execution_mode=deterministic_fallback` 또는 `fallback_used=true`를 기록한다. 이 경로는 가용성을
유지하기 위한 것이며 LLM 기반 SQL 생성을 대체하지 않는다.
Composer fallback은 성공한 Text2SQL evidence의 sample row를 최대 5행까지 사용자 답변에 포함해,
LLM 장애 시에도 status 답변이 metric 이름만 반복하고 실제 값을 누락하지 않도록 한다.

대화 상태와 feedback은 local SQLite state store에 저장한다. 한 user/assistant exchange는 같은
transaction으로 기록되고 최근 bounded history만 graph에 전달한다. FAB, process, product, route,
equipment, date basis context는 후속 질문의 structured request context로 복원된다. 사용자가 남긴
helpful/unhelpful feedback은 해당 assistant turn과 당시 history snapshot에 연결되어 다음 Planner,
Reflection, Composer 입력에 포함된다. 테스트 환경은 별도 임시 state DB를 사용한다.
기본 RAG, incident case, state DB 경로는 현재 작업 디렉터리가 아니라 assistant package root에서
계산한다. Live scenario evaluator는 `--live`가 없으면 Azure/DB 호출 전에 종료한다.

Local RAG는 knowledge base를 분리한 뒤 chunk별 `issue_types`, `playbook_ids`, FAB/source
metadata를 추출한다. 검색은 한국어 조사 정규화와 한영 FAB 용어 확장, corpus IDF coverage,
metadata issue match, 목차 감점을 결합하며 중복 chunk를 제거한다. Chunk ID는 절대 경로가 아닌
문서 파일명, knowledge base, index, content로 생성해 개발/배포 경로가 달라도 동일하게 유지한다.

SC-002 diagnosis는 CaseSearch까지 실행한 뒤 `diagnosis_synthesis` evidence를 만든다. SQL 결과는
관측값, incident playbook은 원인 후보, incident case는 유사 사례로 서로 다른 배열에 보존하며,
현재 데이터와 incident를 직접 연결하는 검증 근거가 없으므로 `conclusion_level=candidate_only`로
고정한다. Composer와 Answer Supervisor는 원인을 확정 사실로 표현하지 않고 후보/추정 및 추가
확인 필요성을 답변에 포함해야 한다.
Synthesis는 verified incident와 simulated reference case를 별도 reliability tier로 유지하며,
simulation playbook도 실제 FAB SOP로 취급하지 않는다. simulation-only evidence는 root-cause
corroboration이 될 수 없고 최종 답변에 해당 출처 한계를 표시해야 한다. SQL이 성공했더라도
sample row가 없으면 operational observation으로 계산하지 않는다.

SC-003 Impact는 Text2SQL row의 numeric column 평균을 baseline으로 사용하되 집계법, 입력 row/column,
source table, 계산 입력, 공식, 가정과 limitation을 `impact_calculation` evidence에 함께 기록한다.
승인된 1차 민감도는 utilization percentage-point 변화와 cycle-time percentage 변화뿐이다.
Queue Time/output, downtime/output, cycle-time/ontime은 필요한 operational metric 또는 보정된 인과
모델이 없으면 숫자 추정 없이 `data_unavailable`을 반환한다.

Visualization contract는 단일 y뿐 아니라 다중 y 비교를 지원한다. 다중 지표 결과는 원본 wide
row를 `x/metric/value` long-form으로 변환하고 `encoding.color=metric`인 grouped bar spec으로
내보내므로 제품별 Cycle Time/Ontime 같은 비교가 한 chart에서 series별로 유지된다.
명시적인 AutoSched `Period_N` 복수 비교도 period 축 grouped bar로 정규화한다.

Text2SQL은 parsed slot과 schema catalog가 완전히 결정하는 status, master-data, release-plan,
trend/compare 질의를 deterministic fast path에서 먼저 처리한다. Current 질의는 최신
`report_time` snapshot을 강제하고 station ID는 exact match한다. 제품·기간 비교와 lotrelease
date-basis 집계는 chart contract까지 함께 생성한다. `deterministic_only` 평가 모드는 LLM client를
구성하지 않으며, 62문항 local PostgreSQL gate에서 SQL 실행과 semantic contract를 검증한다.
Fast path가 지원하지 않는 질의만 기존 allowlisted LLM SQL 경로로 내려간다.
Operational trend의 명시적 `YYYY-MM-DD` 범위는 종료일을 포함하도록 SQL의 exclusive end를 하루
뒤로 정규화한다. 최근 N일 제품 비교는 `report_time::date` 범위 내 numeric metric을 제품별 AVG로
집계하고 grouped-bar chart contract를 생성한다.

각 선택 agent 실행 직후 공통 `sub_agent/reflection.py` 계약으로 Planner의 intent/action,
agent output, success criteria, 신규 evidence와 limitation을 검증한다. 결과는 실행 순서대로
`agent_reflections`에 누적한다. `pass`가 아닌 결과는 `supervisor_reviews`에도 기록하고,
post-execution Supervisor가 bounded recovery action을 선택한다. 결정은 `supervisor_decisions`에,
재시도 횟수는 `retry_counts`와 budget state에 남겨 최종 Reflection/Composer 및 API 응답에 전달한다.
`data_unavailable`, `unsupported`, `needs_clarification`, `skipped`는 동일 agent 재시도 대상이 아니다.
Planner 승인 후에는 고정 agent chain을 순회하지 않고 `execution_steps` cursor 기반 Dispatcher가
다음 agent를 직접 호출한다. 최종 Reflection도 같은 retry/replan budget을 공유하며,
`termination_reason`과 `reflection_decisions`에 최종 종료 또는 재분기 이유를 기록한다.

## 단계별 구현 순서

1. SMT2020 Excel을 staging table에 적재하고 조회 가능한 스키마를 고정합니다.
2. SC-001 Text2SQL과 read-only SQL 검증을 구현합니다.
3. 공정 문서와 대응 매뉴얼을 별도 collection으로 임베딩합니다.
4. SC-002 원인 진단에서 SQL 근거와 RAG 근거를 결합합니다.
5. SC-004 시계열 조회와 차트 응답을 연결합니다.
6. Planner/Supervisor와 SC-003 영향도, feedback, self-reflection sub-agent를 확장합니다.

## 안전 경계

- DB 계정은 read-only로 제한합니다.
- 설비 제어와 생산 조치 자동 실행은 제공하지 않습니다.
- 모든 답변은 SQL, 문서, 계산 결과 중 사용한 근거와 한계를 반환합니다.

## 상세 설계

- Text2SQL sub-agent의 LangGraph state, node, prompt, validation, fallback 설계는
  `docs/text2sql_agent_design.md`를 기준으로 합니다.

## FAB 물리 테이블명과 범위 결정 (2026-09-07)

FAB 스키마는 유지하며 테이블은 `fab10.toolgroups_fab10`처럼 FAB 접미사를 사용한다.
`app/db/fab_catalog.py`의 공통 패턴 `{fab}.{logical_table}_{fab}`을 SQL 작성 전에 바인딩한다.
Text2SQL은 현재 질문의 FAB/팹/M 표기, 요청 FAB, 최근 사용자 대화 순서로 조회 범위를 정한다.
복수/지원 밖/미지정 FAB은 clarification을 반환하며, 다른 FAB으로 자동 대체하지 않는다.
LLM은 바인딩된 테이블/컬럼 context와 allowlist를 받고, 반환 근거의 테이블명은 SQL에서 추출한다.
SQL template, loader 및 시뮬레이션 snapshot 적재도 새 물리 이름을 사용한다.
공통 메타 DB `agent_meta.table_catalog`와 실제 PostgreSQL 구조를 결합해 기존 정적 목록 밖의
snapshot/event 및 기타 업무 테이블까지 조회 후보로 제공한다. 실제 DB 메타를 사용하는 LLM 경로는
후보 순위 → 구조화 의미 계획 → 코드 검증 → SQL 생성 → SQL-plan AST 대조 → 실행 순서로 동작한다.
Planner/Supervisor는 과거 실패만으로 새 조회를 막지 않으며 실제 도구 결과로 가용성을 판단한다.
복구의 `alternate_agent`는 specialist 교체로, 다른 테이블 접근 정책과 무관하다.

## PANDA 기반 Text2SQL 고도화 (2026-09-07)

- `db/semantic_metadata.py`: 코드 근거의 설명/동의어/지표 단위/집계 규칙/출처를 공통 메타와 결합한다. 업무 검토 완료 SSOT로 간주하지 않는다.
- `db/schema_retrieval.py`: 관련성 및 제한된 검토 가중치로 양수 점수 후보를 최대 6개 선택한다. 명시 테이블·필수 후보·검증된 관계 이웃은 추가 보존하고, 관련성 근거가 없으면 전체를 제공한다. 실제 area 값의 작은 도메인을 제공하며 중복 설명과 순위 trace는 모델 입력에서 제거한다.
- `sub_agent/semantic_plan.py`: 테이블/컬럼/조건/집계/조인/최신 기준/결과 grain을 SQL 전에 검증한다. SQLGlot AST로 실제 테이블, 출력 집계·차원 수, 그룹, 정적 조건, 최신 MAX 계약을 대조하고 계획 외 행 필터를 거절한다. CTE의 실제 사용 소스와 별칭 계보를 추적한다. 범용 SQL 동치 증명은 아니다.
- 최신 범위는 `latest_scope=global|filtered`로 구별한다. 기본 시각 열은 메타의 `default_time_column`이며 명시 시각 열 요청이 우선한다. NULL 포함 행 수 `COUNT(*)`와 `COUNT(column)`을 구분한다. 영역 단위 breakdown 설정과 toolgroups를 연결할 때 설정 수는 `COUNT(DISTINCT source_row_id)`로 중복을 피한다. PM의 toolgroup 연결과는 다른 관계다.
- 날짜 범위의 스냅샷 시작/종료 열 혼용과 명시 NULL 행 수 조건 누락을 계획 단계에서 검사한다. 행 개수 질문의 명확한 NULL 조건은 유일한 실제 컬럼에 바인딩하며, 모호한 출처·반대 조건은 거절한다. CTE 이후의 집계값 변형 및 선언한 지표 별칭의 역할 바뀜도 SQL 검증 대상이다.
- `OpenAIText2SQLClient`: 실제 메타 경로에서 계획과 SQL을 별도 호출한다. 기존 deterministic fast path와 주입된 단순 테스트 client는 별도 경로다.
- 후보 생성 최대 2회: 검증 실패는 같은 스키마에서 수정, 축소 스키마 지원 불가는 전체 스키마로 재탐색한다. `QueryPlan.generation_attempts`에 시도별 근거를 남긴다.
- `scripts/evaluate_text2sql_semantics.py`: 별도 기준 SQL과 실제 결과를 비교한다. fixture/실행 시작 코드 SHA256, 전후 데이터 변화, 호출 시간/횟수/입력 문자 수, 조회 가능/negative 결과를 분리 기록한다. 복수 수치는 명시된 출력 순서 계약으로 값의 역할 바뀜을 감지한다. 임의 순서의 다중 측정값 의미 동치까지 추론하지는 않는다.

작업 이력과 실제 검증 결과/남은 한계는 `docs/text2sql_hackathon_20260907.md`와 추가 일반화 평가 `docs/text2sql_generalization_20260908.md`를 참고한다. 자체 작성한 소규모 검증 결과와 LLM의 confidence 값은 일반 서비스 정확도의 통계적 보장이 아니다.

### RAG와 통합 그래프의 검증 경계 (2026-09-08)

동적 dispatcher와 agent supervisor를 통해 RAG를 실행하며 검색 어댑터는 evidence, trace, limitations를 함께 반환한다. Incident 근거에는 선언된 issue와 질문의 정합성을 제공하고, 명시한 playbook ID는 해당 원문 조회를 유지한다. 문서 전용 Composer가 원문 span과 생성 후 주장을 검증한 경우 최종 supervisor는 검증 결과를 사용해 인용 연결을 보존한다. 실제 SQL/사례가 포함된 혼합 답변은 기존 최종 답변 검토를 수행하며 `not_applied_mixed_evidence`로 표시한다. SSE 최종 응답에는 대화 기록과 재시도 상태뿐 아니라 citations, grounding, model_usage가 포함되며 새 채팅 화면의 근거 탭에서 원문 인용을 확인할 수 있다.
