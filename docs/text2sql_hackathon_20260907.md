# Text2SQL 고도화 해커톤 — 새벽 1시 마감

- 시작: 2026-09-07 22:51 KST. 종료: 2026-09-08 01:00 KST (사용자가 사용량 부족으로 마감 단축).
- 사용자 요청: MARS-SQL, TriSQL, Toss PANDA를 기준으로 테스트 → 고도화 반복. 필요한 DB 아키텍처 정리 허용.
- 작업 브랜치: **fix/adv_t2s**. 사용자가 명시 지정했다. 기존 미커밋 변경을 보존해 이 브랜치로 전환했다. 매 이어가기에서 브랜치를 확인하며 main에 구현하거나 병합하지 않는다.
- 사용자 변경 마감까지 작업/검증/보고를 진행했다. Heartbeat `text2sql-panda-8`은 2026-09-08 01:00 KST 마감 시 PAUSED로 중지했다.
- 외부 LLM: 사용자가 설정된 skax.ai-talentlab.com에 테스트 질문/스키마/결과 샘플 전송을 명시 승인했다.
- 업무 원본은 보존한다. 사용자 변경(특히 apps/web/app.py)을 되돌리지 않는다. 데이터 적재 자동화는 별도 운영 중이므로 행 수 증가는 오류로 간주하지 않는다.

## 근거와 적용 원칙

- MARS-SQL: https://arxiv.org/html/2511.01008v2 — grounding, interactive generation, validation 역할을 분리하고 실행 관찰을 활용한다. 논문의 강화학습/다중 학습 모델을 재현한다고 주장하지 않는다.
- TriSQL: docs/text2sql_paper.pdf, DOI 10.1038/s41598-026-39128-9 — 질문 중심 schema 선택, 구조 우선 생성, 복잡도에 따른 refinement. 학습형 selector/decoder 재현과 서비스 구조 응용을 구분한다.
- PANDA: https://toss.tech/article/da-assistant-panda — 표준 데이터와 업무 의미 정비, 관련성×신뢰도 기반 선택, 반복 검증, 결과/조회 기준/해석 제공. 공개 글의 향후 목표를 실측 성능으로 인용하지 않는다.

## 시작 기준선

- 전체 테스트 373 pass; 기존 deterministic 실제 DB 평가 79/79.
- FAB10~13 물리 업무 테이블 78개, 공통 메타 32개 논리 정의.
- 실제 API: FAB10 Dry_Etch toolgroup 32행, WIP report trend 3행; FAB11 simulation trend 18행. 대화 의도 전환 및 스트리밍 API 검증 완료.
- 위 점수는 고정 템플릿 회귀 성공률이다. 일반 LLM Text2SQL 정확도로 사용하지 않는다.
- 주요 격차: 메타 설명 대부분 자동 이름 치환; 용어/관계/지표 JSON 빈 상태. LLM에 FAB 전체 컬럼 전달. 독립적인 SQL 전 계획 검증 없음. 의미 정답 평가 및 비용/지연 평가 부족.

## 순서와 성공 판단

1. 근거가 있는 업무 메타 의미/용어/지표/시간 단위 정리, FAB별 가용성 확인.
2. 질문별 관련성과 출처 신뢰도에 따른 결정론적 후보 순위 및 작은 schema context. 새 테이블의 접근 가능성 유지, 선택 근거 기록.
3. SQL 전에 구조화 계획 검증, 생성 SQL과 계획의 테이블/컬럼/조건/집계 일치 점검.
4. 복잡도와 오류 종류에 따라 제한된 재탐색/수정, 성공 결과의 의미 검증.
5. FAB/공정/지표/기간/집계/후속 질문/빈 결과/오류 복구를 포함한 독립 평가. 실측 정확도와 미지원/모호성 구분, 비용/지연 기록.
6. 가능한 실제 화면 경로 검증 및 DB/서비스 문서 정리. 종료 전 최종 회귀 및 결과 보고.

## 회차 기록

### 회차 1 — 의미 메타 기반 마련

- 논문 2편 및 PANDA 원문 검토. 메타 저장만으로 충분하지 않으며 의미 정의와 후보 선택을 우선하기로 결정.
- `db/semantic_metadata.py` 추가: 코드/적재 구조 근거, table grain, 업무 동의어, 지표의 단위/시간 합산 규칙, 시뮬레이션 출처, 모델/시뮬레이션 키 차이를 정의했다. 업무 검토 완료로 과장하지 않고 `code_grounded`로 표시한다.
- 공통 DB에 `semantics JSONB`를 가산적으로 추가했다. 기존 업무 데이터는 변경하지 않았다. 관리자가 작성한 설명을 보존하며 semantics.managed_by=editor이면 해당 의미/지표를 자동 갱신하지 않는다.
- 실제 결과: 논리 32개 모두 동의어/의미 정의 보유, 9개 논리 테이블에 지표 정의. 78개 실제 테이블에서 123개 컬럼 설명을 제공한다. 전체 컬럼 설명을 완성했다는 의미는 아니다.
- 검증: `.venv/bin/pytest apps/assistant/tests -q --disable-warnings` → 377 pass. 변경 파일 Ruff pass. 실제 DB 메타 동기화 및 모든 지표 참조가 해당 FAB 실제 컬럼에 존재함 확인.
- 재현: `.venv/bin/python apps/assistant/scripts/sync_metadata_catalog.py`. 스키마 증거는 `apps/assistant/output/evals/hackathon_catalog_baseline.json`, 의미 반영 증거는 `hackathon_catalog_semantics_v1.json`.
- 다음: 후보 순위와 schema 축소를 구현/평가한다. 현재는 전체 FAB 컬럼을 계속 전달하므로 비용 감소를 주장하지 않는다. 후속 단계는 SQL 전 구조화 계획 및 의미 일치 검증이다.

## 이어가기

### 회차 2 — 설명 가능한 테이블 후보 선택

- `db/schema_retrieval.py`와 Text2SQL 생성 경로 연결. 테이블명/동의어/설명/컬럼/명시 slot 후보를 점수화하고 메타 검토 수준의 제한된 가중치를 적용한다. 상위 6개를 기본으로 명시 테이블과 필수 후보는 초과해도 보존한다. 근거가 전혀 없으면 전체 스키마로 시작한다.
- 축소 스키마에서 unsupported/validation failure이면 실제 FAB 전체 스키마로 딱 한 번 넓혀 다시 생성한다. 후보 검색은 DB 권한 정책으로 사용하지 않는다.
- 실제 메타를 대상으로 개발용 12질문: 기대 테이블 top1 12/12, 전달 컬럼 59.4~82.0% 감소. `apps/assistant/output/evals/hackathon_retrieval_v1.json`에 질문·기대 테이블·순위 근거 저장. 개발용 동의어 중심 smoke이므로 일반화 정확도/독립 holdout/토큰 비용 감소로 해석하지 않는다.
- 전체 테스트 383 pass, 변경 Ruff pass. explicit unknown table, product_1/10 경계, 모든 명시 테이블 보존, 무근거 시 전체 탐색, 실제 생성 경로의 1회 broadening 회귀를 검증했다.
- 다음: 독립 평가 fixture/script와 관측 trace를 정비하고 실제 LLM 경로에서 선택/실행을 검증한다. 이후 SQL 전 구조화 계획 검증 구현. 아직 SQL 실행 오류에 대한 내부 재생성이나 의미 정답 보장은 추가하지 않았다.

이 파일과 git diff를 확인하고 완료한 작업을 반복하지 않는다. 매 회차 변경, 재현 명령, 실제 결과, 남은 오류를 기록한다. 01:00 KST 이후에는 새 기능을 시작하지 않고 검증 및 최종 보고로 마무리한다.

### 회차 3 — SQL 전 구조화 계획 도입, 실제 의미 실패 발견

- `sub_agent/semantic_plan.py`: strict Pydantic 구조로 테이블, projection, 집계, group, 조건, 조인, 시간 기준과 결과 grain을 생성/검증한다. 실제 컬럼/FAB, 숫자 집계의 타입, 평균/비율 SUM, 연결되지 않은 조인, 검증되지 않은 합성/model 조인을 거절한다.
- 실제 OpenAIText2SQLClient의 DB 메타 경로에서 계획 호출 → 코드 검증 → SQL 생성 호출 순서를 적용했다. 주입된 단순 test client/기존 deterministic 경로에는 적용하지 않는다. QueryPlan에 semantic_plan과 grounding 기록 필드 추가.
- 테스트: 전체 389 pass, 변경 Ruff pass. 가짜 컬럼/집계/조인 계획의 SQL 생성 차단 및 실제 client 호출 순서 확인.
- 실제 LLM+DB 테스트 `fab11 시뮬레이션에서 영역별 평균 대기시간을 조회해줘`: API/SQL 실행은 6.86초, 108행 succeeded. 그러나 SQL이 area, avg_queue_minutes로 GROUP BY하여 시점별 값을 나열하고 있어 영역별 요약이라는 의미 기대에 부족하다. 이를 **정답 성공으로 집계하지 않는다**. 출력 `apps/assistant/output/evals/hackathon_semantic_plan_live_v1.json`.
- 다음 최우선: 시간 범위/최근 snapshot과 기간 평균을 구분하는 plan 계약, 최종 SQL과 plan의 구조 일치 검증, 명시 AVG/시간평균 질문으로 실제 재검증. 현재 타입 검증만으로 의미 정답을 보장하지 못한다. 계획/SQL/실행 관찰을 포함하는 독립 평가가 필요하다.

### 회차 4 — 집계 구조 일치와 실제 평균 오류 수정

- SQLGlot 27.29.0을 설치하고 pyproject 의존성으로 고정했다. PostgreSQL AST의 실제 테이블과 출력 aggregate lineage를 사전 계획과 비교한다. 사용되지 않는 CTE의 AVG로 누락 집계를 숨기는 경우, AVG→SUM 변경, 측정값 자체 GROUP BY로 grain이 달라지는 경우를 거절한다.
- 프롬프트에 구간 평균의 명시적 비가중 시간평균은 허용하되 전체 LOT 가중 평균과 구분하도록 명시했다.
- 실제 LLM/DB: 명시 시간평균 질문 6행, 독립 기준 SQL 값과 일치(8.35초). 원래 실패 질문 그대로 재실행해 AVG+area GROUP BY 6행 성공. 결과 grain/시간평균 한계가 실제 사전 계획에 기록된다.
- 증거: `hackathon_semantic_average_v2.json`, `hackathon_semantic_original_v2.json`. 전체 테스트 395 pass, 변경 Ruff 및 diff check pass.
- 제한/다음: 현재 AST 검증은 테이블/출력 집계와 일부 grain만 검증한다. 임의 predicate 동치, 조인 cardinality, 시간 범위/최신 snapshot 강제, 복합 식 lineage는 아직 보장하지 않는다. 계획 검증 실패 뒤 같은 스키마에서 수정하는 전용 feedback loop와 독립 holdout 평가를 이어간다.

### 회차 5 — 필터 보존과 반개방 시간 범위

- 계획의 정적 비교/IN/null 조건을 SQL의 필수 WHERE 조건과 대조한다. OR에만 있는 조건이나 다른 값으로 변경된 조건은 통과하지 않는다. 단일 사용 CTE 내부의 필수 조건도 확인한다. predicate의 값 개수 계약을 검증한다.
- 실제 LLM/DB: fab11 etch, 2026-09-06 00:00 KST 이상/09-07 00:00 KST 미만, 대기 시간평균 **73.1871428571428571분**. 독립 기준 SQL과 일치. `hackathon_predicate_live_v1.json` 저장.
- 전체 404 tests pass, Ruff/diff check pass. 시간 범위의 `<`가 `<=`로 바뀌면 거절하는 회귀 포함.
- 다음: 명시적 최신 snapshot 계약, 검증 오류를 보고 같은 schema에서 수정하는 bounded loop, 더 넓은 독립 평가. 현재 필터 검증은 정적 상수 중심으로 동적 시간식/범용 논리동치/outer join 전파는 아직 처리하지 않는다.

### 회차 6 — 최신 snapshot 계약과 과도한 거절 수정

- SemanticPlan.latest_by에 테이블/시간 컬럼을 명시한다. 같은 테이블의 MAX(timestamp)와 동등한 필수 조건이 있어야 하며 ORDER BY/LIMIT만으로 최신 전체 snapshot을 증명하지 않는다.
- 실제 LLM은 올바른 최신 FAB12 전체 SUM SQL을 생성했으나 MAX 안에 반복한 `fab_id='fab12'` 조건 때문에 초기 검증기가 과도하게 거절했다. 동일 FAB 명시 조건은 허용하고 다른 FAB/area 조건은 거절하도록 수정했다.
- 같은 생성 SQL을 수정한 검증기에 재검증하고 실제 DB에서 **1183 LOT** 조회, 독립 기준과 일치. 초기 실패 `hackathon_latest_live_v1.json`, 재검증 `hackathon_latest_revalidated.json` 보존. 수정 후 전체 LLM 파이프라인 재실행은 다음 회차에서 한다.
- 전체 406 tests pass, 변경 Ruff/diff check pass. 최신 MAX의 MIN 변경, 다른 시간 컬럼, 영역 필터, OR 우회, 정렬만 사용, 다른 FAB 조건을 거절하는 테스트 포함.
- 다음: 실제 end-to-end 최신 질문 재검증, 실패 계획/grounding도 보존하는 진단 trace, bounded same-schema repair loop 및 독립 평가 확대.

### 회차 7 — 오류 분류에 따른 제한적 수정과 시도 기록

- SQL/계획 검증 실패는 같은 스키마에서 구체 오류와 이전 SQL/계획을 전달하여 수정하고, 지원 불가로 판정한 축소 스키마는 전체 FAB 스키마로 넓힌다. 후보 생성은 최대 2회. API 실패 시 추가 무한 재시도를 만들지 않는다.
- QueryPlan.generation_attempts에 시도별 결과/SQL/문제/테이블/계획/grounding을 기록한다. SQL 검증 실패 결과에도 사전 계획과 grounding을 보존한다.
- 실제 전체 LLM→계획 검증→SQL→실행 경로에서 FAB12 최신 전체 WIP **1183 LOT**, 기준 SQL 일치, 생성 1회 성공. `hackathon_latest_e2e_v2.json`.
- 전체 408 tests pass, 변경 Ruff/diff check pass. 의도적으로 AVG를 SUM으로 생성한 경우 오류 피드백으로 두 번째 후보 AVG에 성공하며, 반복 위반은 2회 후 종료한다.
- 다음: 평가 fixture/script를 만들어 실제 다양한 질문에 대해 표 기반 의미 정답, 명확화/빈 결과, 복잡도별 실패·지연·LLM 호출량을 측정한다. 내부 DB 실행 실패의 직접 repair는 아직 상위 graph 경로에 의존하며 메타 관계 cardinality와 복합 SQL 동치 검증은 남아 있다.

### 회차 8 — 재현 가능한 실제 의미 평가, 6/8 기준선

- `scripts/evaluate_text2sql_semantics.py`, `tests/fixtures/text2sql_semantic_regression.json` 추가. 8개 질문의 단일 측정값/차원/행 중복/순위를 별도 reference SQL과 비교한다. LLM에 reference SQL을 전달하지 않는다. 전후 reference 변화는 stable=false로 구분하고 API 호출 수/시간/입력 문자 수를 기록한다. 입력 문자 수는 토큰/요금이 아니다.
- 실행: `.venv/bin/python apps/assistant/scripts/evaluate_text2sql_semantics.py --output apps/assistant/output/evals/hackathon_semantic_regression_v1.json`
- 결과 **6/8**. 영역 평균, 이벤트 유형별 COUNT, 영역 MAX, 재작업 조건 COUNT, 빈 결과, 반개방 시간평균 통과. 약 4.8~11.4초/질문. 개발 회귀셋이며 독립 holdout 정확도가 아니다.
- 발견 1: latest_fab12는 전체 합계를 요구했지만 area GROUP BY로 6행을 반환. SQL-plan 일치만으로 question-plan 의미 오류를 잡지 못한다.
- 발견 2: top_wip_fab13 SQL은 기준과 같은데 계획이 interval_end eq LATEST를 static filter로 남겨 과잉 거절했다. latest_by 정규화가 필요하다.
- 전체 offline 테스트 410 pass, 변경 Ruff/diff check pass.
- 다음: 질문의 전체 합계/grain 계약을 계획 전에 명시하고 위 두 실패를 동일 평가 질문으로 수정/재검증. comparator는 단일 측정값 회귀용으로 alias를 무시하므로 다중 지표 테스트에 그대로 확대하지 않는다.

### 회차 9 — 2개 실패의 원인 수정 및 동일 질문 재검증

- 질문의 명시적 whole-factory 합계 요구를 request_requirements로 전달/검증해 area/toolgroup으로 나누거나 WIP를 AVG하는 계획을 거절한다. 영역별 출력/전체 기간 평균 요구는 해당 규칙에서 제외한다.
- 최신 marker `LATEST` 및 구조적으로 같은 소스의 정확한 MAX subquery를 latest_by로 정규화한다. 다른 테이블/시간 컬럼/MIN/영역 조건은 같은 최신 계약으로 간주하지 않는다. 계획 텍스트를 실행하지 않는다.
- 1차 재평가에서 latest query의 MAX subquery 문자열 변형을 추가 발견하고 수정했다. 최종 동일 질문 latest_fab12 6.535초 pass, top_wip_fab13 6.809초 pass. 이전 6/8 결과와 재검증 2개를 합쳐 새로운 동일회차 8/8 점수라고 주장하지 않는다.
- 증거 `hackathon_semantic_regression_v2_failures.json`, `hackathon_semantic_regression_v3_latest.json`. 전체 offline 413 tests pass, 변경 Ruff/diff check pass.
- 다음: 8개 전체 반복 평가 및 변형 질문 확대. 전체 최신/시점별 합계와 영역별 breakdown 경계를 더 넓게 평가하고, AST projection/group/order/limit 누락 검사와 조인 의미/실행 오류 복구를 개선한다.

### 사용자 일정 변경

사용량 부족으로 종료를 2026-09-08 01:00 KST로 앞당겼다. 원래 8시간/06:51 마감보다 이 지시를 우선한다. 실패 사례 중심 검증과 마무리에 집중하고 불필요한 전체 LLM 평가 반복 및 상태 보고를 줄인다.

### 회차 10 — 출력 차원과 그룹 기준 보존

- 사전 계획에 있는 출력 차원의 누락/상수 대체와 GROUP BY 추가/삭제를 AST로 검사한다. 단일 소스 CTE의 wildcard 전달, GROUP BY alias/ordinal은 해석한다.
- 전체 offline 418 tests pass, 변경 Ruff/diff check pass. 실제 8개 전체 재평가를 `hackathon_semantic_regression_v4.json`에 저장한다. 사용자 사용량 안내 전에 시작한 이 평가를 마친 뒤에는 실패 중심 검증으로 전환한다.

- 회차 10 실제 재평가 완료: **8/8 통과**, 4.799~11.130초/질문. 개발 회귀 결과이며 일반화 정확도로 과장하지 않는다.

### 회차 11 — 외부 호출 없는 집계 검증 보완

- 계획한 AVG 외에 선언하지 않은 SUM을 출력에 끼워 넣어도 통과하던 검증 누락을 수정했다. 출력 집계 집합의 누락뿐 아니라 추가도 검사한다.
- 관련 semantic plan/retrieval 테스트 40개 통과, Ruff/diff check pass. 외부 LLM 재평가를 반복하지 않았다. 전 회차 실제 8/8은 당시 코드의 결과로 유지한다.

### 회차 12 — 저장 SQL 재검증과 문서 동기화

- v4의 실제 생성 SQL/사전 계획 8개를 현재 AST 검증기에 재실행해 8/8 통과했다. `hackathon_saved_sql_replay.json`. 새 LLM/DB 실행 정확도 측정과는 구분한다.
- architecture/database/metadata 설계 문서의 '독립 계획 검증은 후속' 설명을 현재 구현 범위로 수정했다. deterministic 경로, 제한적 AST 검증, 개발 회귀 지표의 한계도 명시했다.
- 외부 LLM 추가 호출 없음.

### 회차 13 — 계획과 실제 JOIN 키 대조

- 단순 inner/left join의 필수 equality ON 키와 조인 방향/종류를 사전 계획과 비교한다. 키 변경, inner→left 변경, OR 조건, 1=1 조인은 거절한다. 이 검증은 실제 키의 유일성/cardinality 검증을 대체하지 않는다.
- 관련 계획 테스트 33개 통과, Ruff/diff check pass. 외부 LLM 호출 없음. 복합 SQL/derived join의 범용 동치 검증은 여전히 제한적이다.

### 회차 14 — 실제 키 감사와 공통 관계 메타

- 읽기 전용 DB 감사: FAB10~13 모두 toolgroup 중복 0, snapshot 복합키 중복 0, snapshot에 대응되지 않는 event 0. 증거 `hackathon_join_key_audit.json`.
- PM/breakdown/route→toolgroups, event→snapshot의 근거 있는 키 관계를 공통 메타에 추가했다. 후자는 interval_end+fab_id+area 세 키를 모두 사용하며, join 후 snapshot WIP 반복 합산을 경고한다.
- 조회 시 같은 FAB의 실제 대상/컬럼이 있는 관계만 바인딩한다. 공통 DB에는 논리 관계를 저장하여 FAB별 정의를 복제하지 않는다. 초기 감사는 이후 데이터의 영구 유일성을 보장하는 제약과 구분한다.
- 관련 metadata/access 테스트 13개와 Ruff 통과. 실제 metadata sync 및 4개 FAB 대상 바인딩 검증 완료. 외부 LLM 호출 없음.

### 회차 15 — 관계 메타로 복합키/중복 합산 검사

- 사전 계획의 조인이 메타 관계에 있는 복합키를 모두 포함하는지 검사한다. event→snapshot의 시간 키 누락을 SQL 생성 전에 차단한다.
- many-to-one detail 조인 후 target snapshot_count를 SUM하는 단순 계획은 중복 합산으로 거절한다. 집계 선행 CTE의 정교한 동치 증명은 지원 범위 밖이며 snapshot 직접 조회/계획 재구성이 필요하다.
- 관련 계획 테스트 34개 통과, Ruff/diff check pass. 외부 호출 없음.

### 회차 16 — 전체 회귀와 COUNT 의미 구분

- 누적 변경에서 전체 421 tests pass 및 저장된 실제 SQL 8/8 재검증. 증거 `hackathon_saved_sql_replay_v2.json`.
- 이후 단일 테이블 행 개수를 count_rows('*')로 표현해 COUNT(*)/COUNT(1)을 지원했다. COUNT(nullable_column)과 혼동하지 않는다. 관련 계획 테스트 35개 통과.
- 잘못 편집된 관계의 식별자/키 구조가 전체 메타 탐색을 실패시키지 않도록 유효한 관계만 제공한다. 관련 metadata/access 13개 테스트 및 Ruff 통과. 외부 LLM 호출 없음.

### 회차 17 — 실제 스트리밍/화면 경로에서 발견한 검증 충돌

- 실제 `/api/chat/stream`에서 fab12 최신 공장 전체 WIP=1183 조회는 성공했으나, 답변에 인용된 SQL의 LIMIT 1을 근거 없는 수치로 검사하여 최종 실패했다. 증거 `hackathon_stream_integration_v1.json`.
- 성공한 evidence SQL과 동일한 인용만 공백/괄호 포맷을 정규화해 수치 검사 대상에서 제외한다. 변경된 SQL 숫자 및 새로 지어낸 답변 숫자는 계속 검사한다. 저장된 실패 응답 재검증 통과.
- 이어 실제 Streamlit 화면에서 최신 시각 projection과 SUM이 함께 선언됐지만 GROUP BY가 없어 계획/SQL 계약이 충돌했다. 최신 필터로 고정된 timestamp를 합계와 함께 출력할 경우 MAX(timestamp) aggregate로 정규화한다. 일반 timestamp projection에는 적용하지 않는다.
- 누적 전체 테스트 425개 통과(2 warnings), 변경 파일 Ruff 및 diff check 통과. 수정 후 실제 화면 재검증 진행 중. 개발 회귀 8/8 결과를 일반 정확도나 화면 전체 성공률로 해석하지 않는다.
- 추가 화면 재검증에서 timestamp GROUP BY와 MAX가 함께 계획된 동등 표현도 발견했다. mandatory global latest 조건을 계속 검사하면서 grouped timestamp가 MAX(timestamp)를 대신하는 좁은 경우만 허용한다. 명시적인 latest_by가 있는 `<latest_interval_end>` 표기 또한 정규화했다. 해당 계획 테스트 37개 통과.
- **실제 Streamlit 화면 재검증 최종 성공**: 질문 `fab12 시뮬레이션 가장 최근 스냅샷의 공장 전체 WIP 합계를 알려줘` → text2sql succeeded, 1행/2컬럼, `SUM(wip_lots), MAX(interval_end)` → 최종 답변 1,183롯, snapshot 2026-09-07 12:00:00+00:00. simulation 설명 유지. 이 성공은 한 화면 사례이며 확률적 모델의 모든 질의 성공을 의미하지 않는다.

### 회차 18 — 집계식의 숨은 값 변형 검사

- 단순 SUM/AVG 등 계획은 원본 컬럼 집계 계약이다. SQL이 같은 컬럼을 참조하더라도 `SUM(wip_lots*2)`, `SUM(DISTINCT wip_lots)`, `SUM(wip_lots)*2`, CASE/COALESCE로 값을 바꾸면 계획과 같다고 인정하지 않는다.
- 계산식/NULL 대체가 필요한 요청은 앞으로 명시적 expression 계약을 확장해야 한다. 현재 일반 계산식 동치를 임의로 추정하지 않는다.
- 전체 431 tests pass(2 warnings), Ruff/diff check pass. 저장된 실제 SQL 8/8 재검증: `hackathon_saved_sql_replay_v3.json`. 이번 회차 외부 LLM/DB 호출 없음.
- 다음 주기: 남은 검증 한계와 변경 diff 정리, 새 실패 근거가 있을 때만 실제 모델 재호출. 종료는 2026-09-08 01:00 KST이며 main 병합 없이 fix/adv_t2s에서 마무리한다.

### 회차 19 — 편집된 컬럼 의미와 실제 스키마 결합

- `semantics.column_meanings`에 저장된 편집 설명이 있어도 컬럼 설명에는 코드 기본값만 들어가던 경로를 수정했다. PostgreSQL comment > 저장된 업무 설명 > 코드 설명 순으로 결합한다.
- 수집용 columns/fab_status와 보존되는 editor semantics의 역할을 문서화했다. 물리 구조/타입은 발견 결과가 우선이며, 사라진 컬럼의 메타가 새 컬럼을 만들어내지 않는다.
- 관련 metadata/retrieval 13개 테스트 및 Ruff pass. DB 변경/외부 LLM 호출 없음.

## 요구사항별 현재 검증 범위 (마감 전 감사)

| 요구사항 | 현재 근거 | 남은 범위 |
| --- | --- | --- |
| fix/adv_t2s에서 진행 | git branch 확인 | main 병합/배포는 요청 범위 밖 |
| FAB suffix와 공유 {fab} 메타 | 78개 테이블/32개 논리 정의, 실제 FAB catalog 조회 및 기존 DB 회귀 | 신규 FAB는 명시 allowlist/적재 등록 필요 |
| 질문 FAB 추론 및 치환 | 명시 FAB/컨텍스트/대화 우선순위 테스트, 실제 fab12 화면 조회 | 복수 FAB 비교는 아직 단일 FAB 계약 밖 |
| 전체 가용 데이터 조회·잘못된 접근 거절 제거 | 실제 schema discovery, 이전 실패 복구 테스트, toolgroup 및 simulation 실제 조회 | 네트워크 장애/실제로 없는 데이터는 조회 가능하다고 주장하지 않음 |
| 논문 2편과 PANDA 응용 | 근거 링크 및 구조 매핑, 메타 ranking → typed plan → SQL/근거 검증 | 논문 RL 학습/벤치마크 재현은 하지 않음 |
| 메타 의미·관계·집계 정확성 | 의미 정의, 복합키/중복 집계 guard, 실제 join key 감사 | 업무 전문가의 SSOT 승인, 일반 표현식/복잡한 join 동치 검증 필요 |
| 테스트 → 개선 및 실경로 검증 | 실제 의미 회귀 6/8→8/8, 실제 Streamlit 성공, 로컬 431 tests + 회차19 관련 검사 | 8문항은 개발 회귀셋이며 독립 일반 정확도를 증명하지 않음 |
| 사용량 절약 | 이후 저장 SQL replay와 로컬 검사 중심 | 실제 token usage 미계측; 입력 문자 수는 토큰 수가 아님 |
| 데이터 보존 | 초기 suffix 변경 시 OID/행 수 보존 확인, 이후 업무 DML 없음 | 별도 simulation 자동 적재 때문에 행 수는 계속 증가할 수 있음 |
| 마감/보고 | 01:00 KST 최종 검사·문서 정리, heartbeat PAUSED 확인 | 최종 답변으로 결과 전달 |
- 추가 읽기 전용 보존 감사: migration 기록의 78개 테이블 모두 현재 OID 동일, 행 수 감소 없음. `hackathon_data_preservation_audit.json`. 내용 전체의 checksum 보장을 의미하지 않는다.
- 리뷰용 요약 `docs/text2sql_hackathon_results.md` 작성. 계획→SQL→검증 흐름, 개발 회귀와 실제 화면 증거, 범용 정확도/표현식/토큰 계측 한계를 명시했다.
- 회차19 후 전체 검증: 432 tests pass(2 warnings). 브랜치 fix/adv_t2s 재확인.

### 회차 20 — 기존 실제 DB 회귀 재검증

- `evaluate_text2sql_deterministic.py --execute --json`: 79/79 통과. 실제 SQL이 필요한 61문항 모두 결정론적 SQL 경로를 유지한다.
- 증거 `hackathon_deterministic_db_recheck.json`. 외부 LLM 호출 없음. 이 결과는 기존 고정 계약 회귀이며 일반 자유 질의 정확도가 아니다.

### 회차 21 — 정렬·동률·상위 N 계약

- SemanticPlan에 order_by(output/direction), result_limit 추가. 모델 응답 JSON에는 필수 필드로 요청하되 기존 저장 계획은 기본값으로 읽을 수 있다.
- 실제 SQL의 정렬 우선순위·방향·명시 동률 기준·LIMIT·OFFSET을 검사한다. 정렬의 실제 소스/집계를 비교하므로 SELECT 별칭 변경과 ORDER BY 순번도 지원한다. 명시적 상위/하위 N개 요청은 계획의 개수와 대조한다.
- 관련 계획 테스트 50개, 전체 440개 pass(2 warnings), Ruff/diff check pass.
- 실제 모델/DB 한 문항: 최신 fab13 WIP 상위 3개, 동률 area 오름차순, LIMIT 3 계약 통과. 응답은 metrology 439, cmp 353, etch 352 순서로 gold와 일치했다. 단, interval_end를 부가 출력하여 기존 엄격한 컬럼 수 비교는 실패했다. 최초 결과 `hackathon_ranking_contract_live.json`을 그대로 보존했다.
- 평가셋에 해당 문항의 선택적 조회 시각 `allowed_context_columns=[interval_end]`를 명시했다. 그 외 추가 컬럼은 계속 실패하며, 필수 reference 컬럼은 허용 목록에 있어도 제외하지 않는다. strict_result_matches_reference와 result_matches_reference를 함께 기록한다. 관련 평가 테스트 3개 pass.
- 저장 결과 재평가: SQL/계획 계약 true, strict shape false, requested result true. `hackathon_ranking_contract_revalidated.json`. 재평가는 추가 LLM/DB 호출 없이 진행했으며 새 8/8 실측으로 주장하지 않는다.
- 정렬/평가 보완 후 전체 441 tests pass(2 warnings), 수정한 핵심 DB/계획/생성/검증/평가 파일 Ruff pass, diff check pass. 이후 새 변경이나 실패가 없으면 동일한 실제 모델 검사를 반복하지 않는다.

### 회차 22 — 새 질문 두 개의 실제 의미 확인

- 코드 구현 후 별도로 작성한 두 질문/기준 SQL을 실행 전에 `text2sql_semantic_probe_20260908.json`에 고정했다. 통계적으로 독립적인 대규모 holdout 벤치마크는 아니다.
- FAB11의 설비군과 매칭되는 PM 모델 설정 건수를 영역별로 집계: 7.924초, 기준 SQL과 일치. 메타의 PM→toolgroups 관계를 활용하는 실제 경로를 확인했다.
- FAB12의 rework_flag=false 이벤트의 영역별 평균 대기시간 하위 2개: 6.322초, 값/순서 모두 기준 SQL과 일치. 필터+집계+정렬+개수를 함께 확인했다.
- 두 질문 모두 strict 결과 비교 2/2 통과. `hackathon_additional_probe_v1.json`. 기존 8문항 점수와 합쳐 일반 정확도로 주장하지 않는다.

### 회차 23 — 컬럼 의미의 데이터 종류 경계

- 이름이 wip_lots라고 해서 모든 테이블에 '합성 WIP' 의미를 붙이지 않도록, 기본 컬럼 의미를 확인된 simulation/report/model/release 계열로 한정했다. 미확인 신규 테이블은 동일한 이름을 사용해도 자동으로 그 의미/단위를 상속하지 않는다.
- 관련 metadata/retrieval/access 25 tests pass, Ruff pass. 공통 메타 재동기화: 32개 논리 정의/78개 물리 테이블. 업무 데이터 변경 없음.

### 회차 24 — 편집자가 비운 지표 목록 보존

- metadata sync가 editor 소유의 빈 metrics를 기본 지표로 다시 채우던 조건 순서를 수정했다. editor 소유권은 빈 목록에도 우선한다.
- 실제 PostgreSQL 트랜잭션에서 toolgroups 메타 한 행의 metrics를 비우고 editor로 표시한 뒤 실제 sync_metadata를 실행했다. 빈 목록/소유권 유지 확인 후 전체 롤백했고 원래 metrics/semantics 복원도 확인했다. 업무 테이블은 변경하지 않았다.
- 증거 `hackathon_editor_metadata_preservation.json`, Ruff pass.
- 같은 편집 정책을 읽기 단계 enrich에도 적용했다. editor가 명시한 빈 metrics를 코드 기본값으로 다시 채우지 않는다. 해당 회귀를 포함한 semantic metadata 7개 테스트 pass, Ruff pass. code 소유의 빈 목록은 기존처럼 기본 정의로 보완한다.
- 상위 10%/퍼센트 요청을 top-10개로 오인하지 않도록 명시 N개 추출의 비율 예외를 추가했다. 기존 계획 테스트 50개 통과.


## 마감 결과 — 2026-09-08 01:00 KST

- 사용자 지정 fix/adv_t2s 브랜치에서 마무리했다. main 병합/배포는 수행하지 않았다. 기존 미커밋 변경을 보존했다.
- 최종 전체 테스트 **443 passed, 2 warnings**, 핵심 변경 파일 Ruff 및 git diff --check 통과. 테스트 원문은 `hackathon_final_pytest.txt`.
- 실제 결정론적 DB 회귀 79/79, 개발 의미 회귀 8/8, 이후 별도로 작성한 조인/집계 정렬 질문 2/2 확인. 서로 다른 범위의 점수를 합쳐 일반 정확도로 보고하지 않는다.
- 최종 코드로 저장 SQL/계획 11건 재검증 모두 통과. `hackathon_final_saved_sql_replay.json`. 모델/DB를 새로 실행한 점수가 아니다.
- 실제 Streamlit에서 최신 fab12 WIP 조회와 최종 답변을 확인했다(회차17). 검증 당시 1,183롯, snapshot 2026-09-07 12:00 UTC이며 이후 별도 자동 적재로 최신 값은 달라질 수 있다.
- 78개 업무 테이블 동일 OID/행 수 감소 없음 확인. 메타 editor 보존 검증의 임시 변경은 전체 롤백 후 복원 확인했다.
- 코드 110개 파일의 SHA256 및 Python/라이브러리 버전을 `hackathon_validation_manifest.json`에 기록했다. 마감 감사에서 해당 파일 변경 없음 확인.
- 자동화 text2sql-panda-8은 PAUSED 확인. 별도 FAB 데이터 적재 자동화는 건드리지 않았다.
- 최종 요약과 한계는 `docs/text2sql_hackathon_results.md`에 정리했다. 일반 복잡 SQL 동치, 다중 FAB 비교, 전문가 SSOT 검토, 전체 오류 복구 및 토큰 계측은 후속 범위다.
