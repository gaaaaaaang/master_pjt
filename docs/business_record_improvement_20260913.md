# 현장 질문 조회 개선 및 실제 실행 결과

검증일: 2026-09-13

LOT·장비 식별자를 보존하고, 실제 데이터가 있는 출처를 선택하며, 요청 항목별 근거와 결측을 구분하도록 공통 조회 경로를 개선했다. 모델용 계측 경로를 합치는 조회도 추가했다. 업무 데이터가 없는 항목을 모두 답할 수 있게 된 것은 아니다.

현재 DB의 시뮬레이션 ID로 치환해 기존 `/api/chat/stream`을 실제 호출했다. 신규 현장 데이터는 적재하지 않았다. 아래 결과는 수정·재검증을 거친 각 질문의 최종 확인본이며, 중간 실패 기록은 로컬 평가 디렉터리에 보존했다. API 성공 상태를 정답률로 계산하지 않는다.

## 변경 내용

- LOT·개별 장비·자원 그룹 식별자를 분리하고 SQL의 정확한 대상 조건을 검증한다.
- 제한·사유·설정자 조회가 FAB 전체 집계 템플릿으로 대체되지 않도록 한다.
- 출처별 존재 확인과 실제 코드 도메인으로 스키마 선택을 보강한다.
- 설정자·사유·가용성·작업 코드의 실제 업무 필드와 이벤트 설명·역할을 구분한다.
- 최신 대상 조회 전에 다운·idle 같은 결과 조건을 임의로 넣지 못하게 한다.
- 공통 구조의 경로 테이블 UNION ALL, 항목별 answer_coverage, 부분·빈 결과 상태와 반환 한도를 지원한다.
- 최종 답변에서 사유·설정자 의미 왜곡과 빈 조회 결과의 과장을 검증한다.

## 원문 7개 최종 확인

| 번호 | 실행 상태 | 반환 행 | 확인 결과 |
| --- | --- | ---: | --- |
| 1 | succeeded | 1 | 제한 시작 이력 조회. 실제 설정자 식별정보는 데이터에 없음. |
| 2 | succeeded | 1 | 관련 Hold 이벤트 조회. 업무 제한 사유는 확인할 수 없음을 명시. |
| 3 | succeeded | 2 | 장비 조건 안에서 최신 이벤트 조회. 과거 다운 이벤트를 현재 비가동으로 대체하지 않음. |
| 4 | succeeded | 1 | 해당 LOT의 최신 hold_start/started 이벤트 조회. 실시간 공식 상태와 구분. |
| 5 | unsupported | 0 | 가용성 정의 부재를 미지원으로 구분. 빈 자원 0개라고 만들지 않음. |
| 6 | unsupported | 0 | 작업 코드 마스터 부재를 미지원으로 구분. 설비군·이벤트 종류로 대체하지 않음. |
| 7 | succeeded | 200 | 제품 경로 테이블을 UNION ALL로 조회. 모델 계측 매핑 200행, 전체 건수·공식 현장 매핑과 구분. |

기존 6번 답변도 실제 작업 코드가 아니라 설비군을 반환한 것이어서 정답으로 볼 수 없다. 현재는 데이터/정의 부족을 분명하게 알린다.

## 검증 범위와 남은 한계

- 자동 테스트: `pytest -q` 999개 통과. 새 업무 기록 회귀 테스트 43개 포함. 기존 라이브러리 deprecation 경고 2개.
- 원문 7개와 변형·회귀 질문 6개를 실제 API에서 실행하고 답변·SQL·행을 확인했다. GUI 상호작용 및 신규 현장 데이터 인수 테스트는 이번 검증에 포함하지 않았다.
- 중간 검증에서 여러 테이블 계획 누락, 역할·사유 표현 혼동, 빈 결과 과장과 외부 모델 호출 오류가 있었다. 수정 뒤 영향받은 질문을 재검증했다. 모델 출력의 모든 변형에 대한 보장은 아니다.
- 개인 설정자, 업무 제한 사유, 공식 장비/LOT 현재 상태, 자원 점유·예약·자격, 등록 작업 코드, 공식 계측 매핑은 현장 데이터와 업무 정의가 필요하다.
- 계측 매핑 200행은 반환 상한이다. 공장 전체 등록 건수로 해석하지 않는다. 최신 이벤트도 공식 현재 상태를 대신하지 않는다.
- 자동 승인 검토가 재검증 호출을 한 차례 거절했으나, 사용자 승인과 기존 서비스 경로를 재확인한 뒤 승인되어 검증을 진행했다.

[내일 데이터 연결 기준](business_record_data_contract.md)

## 변형·회귀 질문

| 질문 | 상태 | 반환 행 |
| --- | --- | ---: |
| LOT FAB10-PH-09121558-015의 Hold 이력과 누가 설정했는지 알려줘. | succeeded / partial | 1 |
| 장비 FAB10_ETCH_TG01_EQ03의 최신 이벤트와 상태를 확인해줘. | succeeded / partial | 1 |
| LOT TEST-DOES-NOT-EXIST-999 현재 상태를 확인해줘. | succeeded / empty | 0 |
| fab11 Dry_Etch toolgroup 목록을 보여줘. | succeeded / complete | 27 |
| fab10 etch에 등록된 operation code 목록을 알려줘. | unsupported / unsupported | 0 |
| 계측 공정 경로와 단계별 설비군 매핑을 보여줘. | succeeded / partial | 200 |

## 원문 1: FAB10-IM-09121559-015의 공정 진행 제한 이력을 조회하고, 제한 설정자를 알려줘.

실제 실행 소요: 32.42초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/acceptance/q1.json)

FAB10-IM-09121559-015 LOT의 공정 진행 제한(hold) 이력은 다음과 같이 조회됩니다.

- 2026-09-13 00:59:22+09:00에 "hold_start" 이벤트가 "started" 상태로 기록되었습니다. 이때의 기록 주체(actor_role)는 "scheduler"로 저장되어 있습니다.

단, 시스템의 현재 데이터에서는 제한 설정자(Setter)에 해당하는 실제 사용자명 또는 개별 식별 정보는 제공되지 않으며, actor_role(예: scheduler)은 이벤트 기록 역할만을 나타냅니다. 즉, 요청하신 "설정자"의 구체적인 식별자는 별도의 비즈니스 필드로 관리되고 있지 않아 조회할 수 없습니다.

이 데이터는 시뮬레이션 기반 공정 이벤트 기록임을 참고하시기 바랍니다.

### 실행 SQL

```sql
SELECT "fab10"."fab_process_raw_events_fab10"."event_time", "fab10"."fab_process_raw_events_fab10"."event_type", "fab10"."fab_process_raw_events_fab10"."event_status", "fab10"."fab_process_raw_events_fab10"."actor_role" FROM "fab10"."fab_process_raw_events_fab10" WHERE "fab10"."fab_process_raw_events_fab10"."lot_id" = 'FAB10-IM-09121559-015' AND "fab10"."fab_process_raw_events_fab10"."event_type" IN ('hold_start', 'hold_release') ORDER BY "event_time" ASC, "event_type" ASC, "event_status" ASC
```

## 원문 2: FAB10-IM-09121559-015에 설정된 공정 진행 제한 사유를 알려줘.

실제 실행 소요: 34.99초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/acceptance/q2.json)

FAB10-IM-09121559-015 LOT에 대해 조회된 "공정 진행 제한 사유"는 데이터 내 공식 사유(reason) 또는 업무 코드가 별도로 제공되지 않습니다. 관측된 최신 이벤트는 2026-09-13 00:59:22+09:00의 hold_start이며, narrative(합성 이벤트 설명)는 "fab10 implant scheduler scheduler_decision reported normal hold_start."입니다. 본 narrative는 표준 공식 제한 사유가 아니며, event 역할(actor_role)은 "scheduler"로 기록되었으나, 공식 사유 필드는 데이터에 포함되지 않았습니다.

따라서 본 데이터에서는 요청하신 공식 제한 사유 필드가 존재하지 않으므로, 실제 공장 업무 사유 또는 코드가 필요할 경우 SSOT(공장 공식 시스템) 데이터를 확인해야 합니다.

### 실행 SQL

```sql
SELECT "fab10"."fab_process_raw_events_fab10"."event_type", "fab10"."fab_process_raw_events_fab10"."event_status", "fab10"."fab_process_raw_events_fab10"."event_time", "fab10"."fab_process_raw_events_fab10"."actor_role", "fab10"."fab_process_raw_events_fab10"."narrative" FROM "fab10"."fab_process_raw_events_fab10" WHERE "fab10"."fab_process_raw_events_fab10"."fab_id" = 'fab10' AND "fab10"."fab_process_raw_events_fab10"."lot_id" = 'FAB10-IM-09121559-015' AND "fab10"."fab_process_raw_events_fab10"."event_type" IN ('hold_start', 'hold_release') ORDER BY "event_time" DESC, "event_type" ASC
```

## 원문 3: FAB10_ETCH_TG01_EQ03 장비의 현재 비가동 사유를 알려줘.

실제 실행 소요: 29.32초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/acceptance/q3.json)

FAB10_ETCH_TG01_EQ03 장비의 최신 보고 기준 상태에서는 비가동(다운) 관련 이벤트가 기록되어 있지 않습니다. 조회된 이벤트들은 2026-09-13 00:00:00+09:00 시점의 process_start 및 queue_enter로, 정상 동작을 나타내며 다운(event_type: equipment_down 등) 상태나 다운 사유 코드/설명은 제공되지 않았습니다.

이는 시뮬레이션 공정 데이터 기준이며, 실제 공장 또는 운영 시스템의 실시간 상태 및 공식 비가동 사유 정보와는 다를 수 있습니다. 또한, 다운 사유는 공식 코드로 분류되어 있지 않고, event_type나 narrative 필드 설명에서만 간접적으로 추정되는 구조임을 참고해 주세요.

### 실행 SQL

```sql
SELECT "fab10"."live_process_events_fab10"."equipment_id", "fab10"."live_process_events_fab10"."interval_end", "fab10"."live_process_events_fab10"."event_type", "fab10"."live_process_events_fab10"."state", "fab10"."live_process_events_fab10"."narrative" FROM "fab10"."live_process_events_fab10" WHERE "fab10"."live_process_events_fab10"."equipment_id" = 'FAB10_ETCH_TG01_EQ03' AND "fab10"."live_process_events_fab10"."interval_end" = (SELECT MAX("fab10"."live_process_events_fab10"."interval_end") FROM "fab10"."live_process_events_fab10" WHERE "fab10"."live_process_events_fab10"."equipment_id" = 'FAB10_ETCH_TG01_EQ03')
```

## 원문 4: FAB10-IM-09121559-015의 현재 상태를 확인해줘.

실제 실행 소요: 26.68초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/acceptance/q4.json)

LOT ID "FAB10-IM-09121559-015"의 FAB10 현재 상태에 대해 조회된 최신 시뮬레이션 데이터 결과는 아래와 같습니다:
- 이벤트 종류: hold_start
- 이벤트 상태: started
- 작업 영역: implant
- 이벤트 시각: 2026-09-13 00:59:22+09:00

이 결과는 해당 LOT이 implant 공정에서 "대기(HOLD)가 시작된" 단계임을 의미합니다. 현재 완료/진행 여부 단정은 불가하며, 반환값은 실제 공장 실측 또는 운영 마스터가 아닌 시뮬레이션 데이터를 통한 가장 마지막 이벤트 기록입니다.

**중요: 본 결과는 조회 쿼리 한계로 1개 행(row limit=1)만 반환되었으며, 전체 공정 이력이나 종합 상태를 모두 대표하지 않습니다.** 행 제한 도달로, 반환된 값 외의 추가 상태나 이력은 불포함되어 있음을 안내드립니다.

### 실행 SQL

```sql
SELECT "fab10"."fab_process_raw_events_fab10"."event_type", "fab10"."fab_process_raw_events_fab10"."event_status", "fab10"."fab_process_raw_events_fab10"."area", "fab10"."fab_process_raw_events_fab10"."event_time", "fab10"."fab_process_raw_events_fab10"."narrative" FROM "fab10"."fab_process_raw_events_fab10" WHERE "fab10"."fab_process_raw_events_fab10"."lot_id" = 'FAB10-IM-09121559-015' AND "fab10"."fab_process_raw_events_fab10"."fab_id" = 'fab10' AND "fab10"."fab_process_raw_events_fab10"."event_time" = (SELECT MAX("fab10"."fab_process_raw_events_fab10"."event_time") FROM "fab10"."fab_process_raw_events_fab10" WHERE "fab10"."fab_process_raw_events_fab10"."lot_id" = 'FAB10-IM-09121559-015' AND "fab10"."fab_process_raw_events_fab10"."fab_id" = 'fab10') ORDER BY "event_time" DESC, "event_type" ASC LIMIT 1
```

## 원문 5: etch 기준으로 자원 그룹 FAB10_ETCH_TG01 내 사용 가능한 빈 자원을 알려줘.

실제 실행 소요: 30.62초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/acceptance/q5.json)

현재 FAB10의 etch 공정에서 자원 그룹 FAB10_ETCH_TG01 내 '사용 가능한 빈 자원'의 공식적인 목록을 제공할 수 없습니다. 이는 현 데이터 및 schema에 장비별 '가용' 또는 '빈' 상태를 authoritative하게 정의하거나 판별할 수 있는 상태 코드(예: idle, available)가 존재하지 않기 때문입니다. 이벤트 테이블에는 마지막 event_status 값(active, completed, started, recovering 등)만 제공되며, 이를 통해 유휴/미점유 상태를 직접 확인할 수 없습니다.

따라서 요청하신 기준에 부합하는 현재 사용 가능한 빈 자원 목록은 데이터 근거가 없어 출력이 불가합니다. 이 한계는 현 시스템과 데이터 설계상 제공되는 정보 범위의 제한 때문임을 안내드립니다.

### 실행 SQL

```sql
-- 실행 SQL 없음: 해당 업무 데이터/정의 미지원
```

## 원문 6: etch 기준으로 운영 시스템에 등록된 작업 코드 종류를 알려줘.

실제 실행 소요: 22.49초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/acceptance/q6.json)

fab10의 운영 시스템(마스터 데이터)에는 etch 공정 기준 작업 코드(작업 종류)로 등록된 공식 컬럼이나 테이블이 존재하지 않습니다. toolgroup, route, step, event_type, setup 등은 사업적으로 작업 코드로 인정되지 않으며, 대체로 사용할 수 없습니다.

따라서 요청하신 작업 코드 목록은 데이터베이스 및 사업 정의 기준에서 근거가 확보되지 않아 제공이 불가능합니다. 공식 작업 코드 등록·조회 기능이 필요하다면 관련 마스터 데이터 구조 및 사업 정의의 수정을 검토해야 합니다.

### 실행 SQL

```sql
-- 실행 SQL 없음: 해당 업무 데이터/정의 미지원
```

## 원문 7: 등록된 계측 공정 매핑 정보를 조회해줘.

실제 실행 소요: 27.25초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/rechecks/q7.json)

FAB10의 최신 마스터 데이터 기준으로 계측(metrology) 공정 매핑 정보를 성공적으로 조회했습니다. SMT2020 General Data(모델 입력용 테이블)에서 area가 계측 영역(Def_Met, Litho_Met, TF_Met)에 해당하는 모든 route, step, step_description, toolgroup 등 주요 매핑 속성을 200개 행 반환 받았습니다(반환 한도에 도달하여 전체 population은 아닙니다).

반환 필드는 공정 경로명(route), 단계(step), 단계 설명(step_description), 영역(area), 장비군(toolgroup), 처리 단위(processing_unit), 처리 시간 분포(processingtime_distribution), 평균 처리 시간(mean), 오프셋(offset), 단위(pt_units), 배치 최소/최대(batch_minimum, batch_maximum), 셋업(setup), 셋업 시점(when), 셋업 분포(setup_distribution), 셋업 시간(setup_time) 등 모델 기준 계측 매핑 구조를 포함합니다.

중요 안내: 이 데이터는 simulation/model 정책 기준의 마스터 데이터 스냅샷으로, 실제 생산 DB의 공식/현장 매핑과는 다를 수 있습니다. 실시간 상태가 아니며, 전체 리스트가 아닌 부분 표본이니 참고용으로 활용하세요.

상세한 매핑 정보는 "데이터 · SQL 보기"에 테이블 형태로 제공됩니다.

### 실행 SQL

```sql
(SELECT "fab10"."route_product_3_fab10"."route", "fab10"."route_product_3_fab10"."step", "fab10"."route_product_3_fab10"."step_description", "fab10"."route_product_3_fab10"."area", "fab10"."route_product_3_fab10"."toolgroup", "fab10"."route_product_3_fab10"."processing_unit", "fab10"."route_product_3_fab10"."processingtime_distribution", "fab10"."route_product_3_fab10"."mean", "fab10"."route_product_3_fab10"."offset", "fab10"."route_product_3_fab10"."pt_units", "fab10"."route_product_3_fab10"."cascading_interval", "fab10"."route_product_3_fab10"."c_units", "fab10"."route_product_3_fab10"."batch_minimum", "fab10"."route_product_3_fab10"."batch_maximum", "fab10"."route_product_3_fab10"."setup", "fab10"."route_product_3_fab10"."when", "fab10"."route_product_3_fab10"."setup_distribution", "fab10"."route_product_3_fab10"."setup_time", "fab10"."route_product_3_fab10"."offset_2", "fab10"."route_product_3_fab10"."st_units", "fab10"."route_product_3_fab10"."step_for_ltl_dedication", "fab10"."route_product_3_fab10"."rework_probability_in_percent", "fab10"."route_product_3_fab10"."r_unit", "fab10"."route_product_3_fab10"."step_for_rework", "fab10"."route_product_3_fab10"."processing_probability_in_percent_sampling", "fab10"."route_product_3_fab10"."step_for_critical_queue_time", "fab10"."route_product_3_fab10"."cqt", "fab10"."route_product_3_fab10"."cqtunits" FROM "fab10"."route_product_3_fab10" WHERE "fab10"."route_product_3_fab10"."area" IN ('Def_Met', 'Litho_Met', 'TF_Met')) UNION ALL (SELECT "fab10"."route_product_4_fab10"."route", "fab10"."route_product_4_fab10"."step", "fab10"."route_product_4_fab10"."step_description", "fab10"."route_product_4_fab10"."area", "fab10"."route_product_4_fab10"."toolgroup", "fab10"."route_product_4_fab10"."processing_unit", "fab10"."route_product_4_fab10"."processingtime_distribution", "fab10"."route_product_4_fab10"."mean", "fab10"."route_product_4_fab10"."offset", "fab10"."route_product_4_fab10"."pt_units", "fab10"."route_product_4_fab10"."cascading_interval", "fab10"."route_product_4_fab10"."c_units", "fab10"."route_product_4_fab10"."batch_minimum", "fab10"."route_product_4_fab10"."batch_maximum", "fab10"."route_product_4_fab10"."setup", "fab10"."route_product_4_fab10"."when", "fab10"."route_product_4_fab10"."setup_distribution", "fab10"."route_product_4_fab10"."setup_time", "fab10"."route_product_4_fab10"."offset_2", "fab10"."route_product_4_fab10"."st_units", "fab10"."route_product_4_fab10"."step_for_ltl_dedication", "fab10"."route_product_4_fab10"."rework_probability_in_percent", "fab10"."route_product_4_fab10"."r_unit", "fab10"."route_product_4_fab10"."step_for_rework", "fab10"."route_product_4_fab10"."processing_probability_in_percent_sampling", "fab10"."route_product_4_fab10"."step_for_critical_queue_time", "fab10"."route_product_4_fab10"."cqt", "fab10"."route_product_4_fab10"."cqtunits" FROM "fab10"."route_product_4_fab10" WHERE "fab10"."route_product_4_fab10"."area" IN ('Def_Met', 'Litho_Met', 'TF_Met'))
```

## 변형 1: LOT FAB10-PH-09121558-015의 Hold 이력과 누가 설정했는지 알려줘.

실제 실행 소요: 33.93초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/rechecks/q1.json)

LOT FAB10-PH-09121558-015의 Hold 이력은 다음과 같습니다.

- 2026-09-13 00:58:29+09:00에 'hold_start' 이벤트가 발생했습니다.
- 이벤트 설명(narrative): "fab10 photo scheduler operator_report reported normal hold_start."
- 이벤트에는 actor_role(기록 역할)로 'scheduler'가 저장되어 있으나, 실제로 Hold를 누가(어떤 계정 또는 개인이) 설정했는지는 이 데이터에서 알 수 없습니다.

따라서, 설정자 정보(누가 설정했는지)는 제공된 데이터 및 현재 조회된 테이블의 비즈니스 필드에는 포함되어 있지 않으며, actor_role은 이벤트 기록 관점의 역할만을 나타냅니다. 추가 Hold 해제 이력은 조회되지 않았습니다.

유의: 이력 데이터는 시뮬레이션 이벤트 로그 기준이며, 공식 인사/운영 기록이나 사업적 결정 사유를 증명하지 않습니다.

### 실행 SQL

```sql
SELECT "fab10"."fab_process_raw_events_fab10"."event_time", "fab10"."fab_process_raw_events_fab10"."event_type", "fab10"."fab_process_raw_events_fab10"."event_status", "fab10"."fab_process_raw_events_fab10"."actor_role", "fab10"."fab_process_raw_events_fab10"."narrative" FROM "fab10"."fab_process_raw_events_fab10" WHERE "fab10"."fab_process_raw_events_fab10"."lot_id" = 'FAB10-PH-09121558-015' AND "fab10"."fab_process_raw_events_fab10"."event_type" IN ('hold_start', 'hold_release') ORDER BY "event_time" ASC, "event_type" ASC, "event_status" ASC
```

## 변형 2: 장비 FAB10_ETCH_TG01_EQ03의 최신 이벤트와 상태를 확인해줘.

실제 실행 소요: 27.77초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/variants/q2.json)

장비 "FAB10_ETCH_TG01_EQ03"의 최신 이벤트는 2026-09-13 01:58:31+09:00에 발생한 "process_start"로, 해당 이벤트의 상태는 "active"(정상, severity: normal)입니다.

※ 이 결과는 fab10의 합성(시뮬레이션) 이벤트 데이터베이스에서 쿼리한 최신 1행만 반환된 것으로, 전체 이벤트 이력이나 실시간 상태를 완전히 대표하지 않습니다. 쿼리 row limit(1행)에 도달했으므로 질의 범위의 완전성이 보장되지 않습니다. 자세한 내역은 데이터 테이블(1행)에서 확인할 수 있으며, 실시간 장비 상태를 완전히 반영하지 않을 수 있습니다.

### 실행 SQL

```sql
SELECT "fab10"."fab_process_raw_events_fab10"."event_id", "fab10"."fab_process_raw_events_fab10"."event_time", "fab10"."fab_process_raw_events_fab10"."event_type", "fab10"."fab_process_raw_events_fab10"."event_status", "fab10"."fab_process_raw_events_fab10"."severity", "fab10"."fab_process_raw_events_fab10"."area", "fab10"."fab_process_raw_events_fab10"."toolgroup", "fab10"."fab_process_raw_events_fab10"."lot_id", "fab10"."fab_process_raw_events_fab10"."narrative" FROM "fab10"."fab_process_raw_events_fab10" WHERE "fab10"."fab_process_raw_events_fab10"."fab_id" = 'fab10' AND "fab10"."fab_process_raw_events_fab10"."equipment_id" = 'FAB10_ETCH_TG01_EQ03' AND "fab10"."fab_process_raw_events_fab10"."event_time" = (SELECT MAX("fab10"."fab_process_raw_events_fab10"."event_time") FROM "fab10"."fab_process_raw_events_fab10" WHERE "fab10"."fab_process_raw_events_fab10"."fab_id" = 'fab10' AND "fab10"."fab_process_raw_events_fab10"."equipment_id" = 'FAB10_ETCH_TG01_EQ03') ORDER BY "event_time" DESC, "event_id" DESC LIMIT 1
```

## 변형 3: LOT TEST-DOES-NOT-EXIST-999 현재 상태를 확인해줘.

실제 실행 소요: 22.06초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/empty_verified/q3.json)

LOT TEST-DOES-NOT-EXIST-999의 현재 상태를 fab10 시뮬레이션 이벤트 데이터에서 조회했으나, 해당 LOT은 기록에 존재하지 않아 아무런 상태 정보가 반환되지 않았습니다.

즉, 시뮬레이션 데이터 기준으로 TEST-DOES-NOT-EXIST-999 LOT은 fab10에 존재하지 않으므로 공정 상태, 위치, hold/release 등 관련된 어떠한 실적 정보도 확인할 수 없습니다. 실제 데이터가 아님을 유의해 주세요. 데이터 부재 여부는 쿼리 행이 반환되지 않은 것으로 확인됐습니다.

### 실행 SQL

```sql
SELECT "fab10"."live_process_events_fab10"."lot_id", "fab10"."live_process_events_fab10"."state", "fab10"."live_process_events_fab10"."interval_end" FROM "fab10"."live_process_events_fab10" WHERE "fab10"."live_process_events_fab10"."lot_id" = 'TEST-DOES-NOT-EXIST-999' AND "fab10"."live_process_events_fab10"."interval_end" = (SELECT MAX("fab10"."live_process_events_fab10"."interval_end") FROM "fab10"."live_process_events_fab10" WHERE "fab10"."live_process_events_fab10"."lot_id" = 'TEST-DOES-NOT-EXIST-999') ORDER BY "interval_end" DESC LIMIT 1
```

## 변형 4: fab11 Dry_Etch toolgroup 목록을 보여줘.

실제 실행 소요: 15.13초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/variants/q4.json)

fab11의 Dry_Etch 공정 구간에 대해 SMT2020 General Data 마스터 기준으로 총 27개의 toolgroup(설비 그룹)이 조회되었습니다. 각 toolgroup의 식별 정보(명칭, 보유 장비 수, 위치 등)는 표 형태로 제공되며, 실시간 FAB 상태가 아닌 simulation/model input 기반입니다. 최신 명칭 및 세부 구분은 시스템에 등록된 기준에 근거하므로, 실시간 변동 사항은 반영되지 않을 수 있습니다.

구체적 toolgroup 목록은 별도의 '데이터 · SQL 보기' 표에서 확인하실 수 있습니다. 각 행에는 area, toolgroup 명칭, number_of_tools, toolgrouplocation, dispatching, ranking 정보 등이 포함되어 있어 공정별 설비 그룹 파악이 가능합니다.

### 실행 SQL

```sql
SELECT area, toolgroup, number_of_tools, toolgrouplocation, dispatching, ranking_1, ranking_2, ranking_3, tool_wake_up_ranking
FROM fab11.toolgroups_fab11
WHERE area ILIKE '%Dry_Etch%'
ORDER BY area, toolgroup
LIMIT 50
```

## 변형 5: fab10 etch에 등록된 operation code 목록을 알려줘.

실제 실행 소요: 22.55초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/variants/q5.json)

fab10의 etch 영역에 등록된 operation code(운영 코드)는, 현재 사용 가능한 마스터 데이터 상에 해당 항목을 제공하는 컬럼이 전혀 없어 목록을 확인할 수 없습니다. model master 테이블 등 모든 관련 테이블을 확인했으나, operation code/work_code/operation_id에 해당하는 정보는 제공되고 있지 않습니다.

따라서, fab10 etch 공정의 운영 코드 목록은 마스터 데이터 내에서 지원되지 않으며, 별도의 목록 제공이 불가함을 안내드립니다.

### 실행 SQL

```sql
-- 실행 SQL 없음: 해당 업무 데이터/정의 미지원
```

## 변형 6: 계측 공정 경로와 단계별 설비군 매핑을 보여줘.

실제 실행 소요: 25.67초. [전체 답변·행·이벤트 JSON](../apps/assistant/output/evals/field_questions_20260913/mapping_verified/q8.json)

FAB10 계측(metrology) 공정 경로의 단계별 설비군 매핑은 SMT2020 General Data 기반 simulation/model input 자료를 기준으로, 각 경로(route)와 단계(step)에 대해 area(Def_Met, Litho_Met, TF_Met)와 toolgroup 컬럼이 포함된 테이블 형태로 조회되었습니다. 반환 행 수는 200개로 한도에 도달했으며, 전체 경로 매핑을 모두 포함하진 않습니다.

테이블에는 예시로 다음과 같은 정보가 포함되어 있습니다:
- 경로(route): Route_Product_3 등
- 단계(step): 106, 116, 128 등
- 단계 설명(step_description): 115_Def_Met, 125_Litho_Met 등
- 계측 영역(area): Def_Met, Litho_Met, TF_Met
- 설비군(toolgroup): DefMet_FE_34, Litho_REG_FE_64, TF_Met_FE_61 등

구체적인 데이터 테이블은 “데이터 · SQL 보기”에서 확인할 수 있습니다.

주의사항:
- 이 결과는 simulation/model input 자료이므로 공식 현장 SSOT 계측 경로 매핑이 아니며, 실제 fab 경로 및 설비군과 차이가 있을 수 있습니다.
- 각 route/product마다 매핑 구조가 다를 수 있습니다.
- 계측 영역(area) 정의(Def_Met, Litho_Met, TF_Met)는 모델 기준으로 실제 fab의 계측 영역과 일부 다를 수 있습니다.
- 실시간 fab 상태 또는 전체 경로·설비군 보장이 아니니 참고용으로 활용하시고, 공식 기술 검증이 필요할 경우 fab SSOT 등 추가 확인 바랍니다.

### 실행 SQL

```sql
(SELECT "fab10"."route_product_3_fab10"."route", "fab10"."route_product_3_fab10"."step", "fab10"."route_product_3_fab10"."step_description", "fab10"."route_product_3_fab10"."area", "fab10"."route_product_3_fab10"."toolgroup" FROM "fab10"."route_product_3_fab10" WHERE "fab10"."route_product_3_fab10"."area" IN ('Def_Met', 'Litho_Met', 'TF_Met')) UNION ALL (SELECT "fab10"."route_product_4_fab10"."route", "fab10"."route_product_4_fab10"."step", "fab10"."route_product_4_fab10"."step_description", "fab10"."route_product_4_fab10"."area", "fab10"."route_product_4_fab10"."toolgroup" FROM "fab10"."route_product_4_fab10" WHERE "fab10"."route_product_4_fab10"."area" IN ('Def_Met', 'Litho_Met', 'TF_Met')) ORDER BY "route" ASC, "step" ASC, "area" ASC
```
