# Text2SQL 일반화 추가 90분

- 사용자 추가 요청 시작: 2026-09-08 06:20:40 KST. 마감: 07:50:40 KST (22:50:40 UTC).
- fix/adv_t2s 유지. 기존 사용자 변경과 업무 데이터 보존. 승인된 skax.ai-talentlab.com/로컬 DB 평가 사용.
- 목표: 이전 개발 예제 외의 질문에서 의미 정확성을 높이고, 오답·불필요한 거절·적절한 재질문을 구분한다. 모든 자연어 질문의 정확도를 보장한다고 주장하지 않는다.
- 기존 443개 테스트/DB 회귀 79개, 개발 의미 8문항과 추가 2문항은 기반 회귀로 분리한다.

## 평가 사전 약속

1. 새 평가 질문과 기준 SQL/상태/비교 계약을 실행 전 파일에 고정하고 SHA256을 기록한다.
2. tuning과 validation을 분리한다. validation 결과는 수정 중 보지 않고 마지막에 실행한다. 결과를 본 이후 재수정하면 최초 validation 점수를 보존하며 이후 점수는 개발 회귀로 표기한다.
3. 평가 기준 SQL을 생성 모델에 전달하지 않는다. 실제 값은 조회 전후 기준 SQL이 동일한 경우만 판정한다.
4. answerable 실행 의미 정확도와 negative 재질문/미지원 응답을 따로 집계한다. 재질문을 실행 성공으로 세지 않는다.
5. 여러 수치의 역할이 바뀌어도 통과하는 비교를 허용하지 않는다. 복수 수치 사례는 요청에 출력 순서를 명시하고 순서 있는 컬럼 비교를 사용한다.
6. 실패는 FAB/소스/계획/SQL검증/실행/결과 의미/평가 계약으로 분류하고 원본 결과를 보존한다.
7. 소수의 자체 작성 질문은 일반 서비스 정확도의 통계적 보장이 아니다. 평가 범위와 한계를 함께 보고한다.

## 기준선과 첫 개선

- tuning 24개(조회 가능 20, 재질문/미지원 4), validation 16개(조회 가능 14, negative 2)를 고정했다. `generalization_frozen_manifest.json`에 SHA256 보존. validation은 아직 실행하지 않았다.
- 원본 tuning 기준선 11/24: 조회 가능 9/20, negative 2/4. 후반 6건은 LLM HTTP 429(TPM) 영향으로 별도 구분한다. `generalization_tuning_baseline.json` 원본 유지.
- 실패에서 확인: 불필요한 시각 출력(t01), 잘못된 area 대소문자/최신 범위(t02/t15), 중복 최신 placeholder(t06), 조인 COUNT(*) 미지원(t08), 행 개수 요청에 목록 템플릿 반환(t19).
- t05는 명시 timestamp 컬럼 없는 구간 표현을 모델은 interval_start/interval_end로 해석했고 기준 SQL은 interval_end만 사용했다. 단순 의미 오답으로 확정하지 않고 시간 기준의 모호성으로 표시한다. 기본 시간 기준을 메타에 명시하고 답변에 밝히도록 했다.
- **t08 기준 SQL/기존 관계 메타 오류**: 실제 breakdown.down_event_valid_for_type='area', type_name은 영역이다. PM과 달리 toolgroup 키가 아니다. fab11~13 설정 41개 중 toolgroup exact 매칭 0, 실제 fab13 area exact 매칭 11 확인. 원본 gold를 사후 변경하지 않으며 이 항목의 raw 점수와 수정된 업무 의미 검증을 분리한다.
- 관계 메타를 breakdown.type_name→toolgroups.area로 수정(many-to-many), 설정 건수의 COUNT(DISTINCT source_row_id) 및 fanout 경고 추가. PM은 type_name→toolgroup 유지. 오래된 생성 프롬프트의 잘못된 관계 설명도 수정했다.
- 메타 검색의 0점 채움 제거, 선택 테이블의 검증된 관계 이웃 유지. 모델 입력에서 중복 column_meanings/순위 trace 제거. 감사 trace는 보존한다.
- 실제 낮은 cardinality area 값(value_domains)을 제공하고 global/filtered latest 범위를 구조화했다. filtered MAX는 계획의 모든 해당 소스 필터와 일치해야 한다. 조인 행 개수 COUNT(*) 지원과 키 검증을 함께 유지했다.
- 수치 하나만 요청한 경우 불필요한 FAB/최신 시각 출력은 근거로 분리한다. SQL 출력 개수와 계획을 대조한다. 모델 집계 요청은 단순 목록 fast path가 가로채지 않는다. 명시적 데이터 삭제 요청은 읽기 전용 정책으로 즉시 거절한다.
- 실제 공통 메타 sync 32개 논리/78개 물리 테이블. 업무 데이터 변경 없음. 관련 테스트 63개 통과. 다음 재평가에는 20초 간격을 적용한다.

## 재평가와 추가 구조 검증

- 20초 간격 실패 재평가 v1: 9/12, HTTP429 없음. t05 모호한 시간 기준은 동일, t06은 CTE MAX의 유효한 SQL을 scope 해석이 거절, t18은 NULL 행 개수를 COUNT(nullable_column)로 세어 0 반환.
- SQLGlot의 전체 `sources` 대신 실제 FROM에 선택된 `selected_sources`를 추적해 사용하지 않은 CTE가 컬럼 출처를 모호하게 만들지 않도록 수정했다. 한 컬럼 CTE를 통과하는 scalar MAX 계보도 검증한다.
- 행 수 질문은 nullable 열 COUNT와 구별한다. 명시 COUNT(column) 요청은 보존하며 NULL 행 수는 COUNT(*)로 계획 정규화한다. 수정 후 t06/t18 실 LLM·DB 재평가 v2 2/2 통과. 전체 테스트 453개 통과 시점 기록.
- 원본 fixture는 변경하지 않았다. 별도 `generalization_tuning_adjudication.json`에 t05 모호성/t08 gold 오류를 기록했고, 명확한 interval_end 및 영역별 DISTINCT 설정 집계 2개 별도 probe는 2/2 통과.
- 개발 전체 candidate 평가를 30초 간격으로 실행 중이다. 그 실행이 import한 버전은 report의 시작 코드 SHA256으로 보존된다.
- 추가 코드 점검에서 계획 외 WHERE 조건으로 행을 누락할 수 있는 빈틈 발견: 계획하지 않은 추가 조건/검증 불가능한 OR·상수 조건을 거절한다. CTE 내부도 검사하고 bound FAB 반복 조건만 허용한다. 기존 '추가 WIP>0 조건을 허용' 테스트는 잘못된 허용이므로 명시 계획이 있는 경우만 통과하도록 수정했다.
- 이 변경 후 정상 저장 SQL/정규화된 계획 21건 재검증 21/21 통과(LLM 추가 호출 없음). 새 의미계획 테스트 61개 통과. SQL 생성 단계에도 global/filtered 최신 MAX 규칙을 직접 전달하도록 보강했다. candidate에서 t02의 모델이 최신 범위를 재해석한 실패는 원본에 남기고 별도 수정 검증 대상으로 둔다.
- 전체 candidate 첫 실행은 5개 기록 후 평가 요약 코드의 Decimal JSON 직렬화 오류로 중단됐다. 5개 결과 파일은 보존한다. DB 소수값 실패 사례의 테스트를 추가해 평가기 오류를 수정했으며 candidate_v2 전체를 40초 간격으로 시작했다. 중단된 실행을 전체 평가 점수로 집계하지 않는다.
- 기존 deterministic 실제 DB 회귀는 이번 버전에서도 79/79 통과(`generalization_deterministic_db.json`). 새 WHERE 검사 포함 전체 단위/회귀는 458개 통과, 이후 평가기 Decimal 테스트 1개 추가 통과.
- candidate_v2 t01에서 `${LATEST_INTERVAL_END}` / `LATEST_INTERVAL_END` 중복 표기를 발견했다. 이미 latest_by에 선언된 동일 열만 정규화하며 일반 문자열 필터는 유지한다. 저장 SQL을 새 계획으로 실제 DB 실행한 별도 replay는 기준 결과와 일치했다(LLM 추가 생성 아님).
- CTE 집계 별칭을 바깥 SELECT에서 `+1`/`*0`/COALESCE로 바꾸는 구조가 기존 계보 검사를 통과함을 재현했다. 직접 집계와 동일하게 파생 별칭의 미선언 값 변형도 거절하며 3종 회귀 테스트를 추가했다.
- candidate_v2 t08은 잘못된 gold 문제와 별개로 **실제 모델 오류도 존재**: toolgroups와 매칭되는 설정을 요청했는데 조인을 생략해 매칭되지 않는 합성 설정까지 포함했다. 이 결과를 gold 오류만으로 면제하지 않는다. 모델 설정과 설비군의 매칭을 명시한 건수 요청에는 메타 관계 대상 유지 검증을 추가했다. DISTINCT는 중복 제거 방법이며 매칭 조건을 생략할 근거가 아니다.
- 추가 값 역할 검사: SUM(wip)와 SUM(queue)가 모두 있어도 SQL 별칭을 서로 바꾸면 기존 집계 집합 비교로는 놓쳤다. 계획에 선언한 지표명을 다른 집계에 붙이는 경우 직접 SQL/CTE 모두 거절하는 테스트를 추가했다. 임의 동등 별칭은 계속 허용한다.

## 개발 단계 종료 및 별도 검증 시작

- candidate_v2 개발 전체 24개 완료: **22/24**, 조회 가능 **18/20**, negative **4/4**. HTTP429 0, 기준 전후 변동 0. 이 실행은 최신 표기·매칭 조인 보강 전 import 버전이므로 사후 고친 결과와 합쳐 24/24라고 표기하지 않는다.
- 별도 targeted_repairs는 최신 코드로 **2/2** 통과: t01 원문, t08 원문+수정된 영역 매칭 gold. 원본 frozen fixture와 기존 결과는 보존했다.
- 최신 후보 전체 테스트 **467개**, 저장된 정규화 계획/SQL 구조 재검증 **42/42** 통과. 후자는 새 자연어 생성 평가가 아니다.
- 78개 업무 테이블 OID 유지/행 수 감소 없음, FAB10~13 현재 toolgroup 키 중복 또는 NULL 0 확인. 내용 체크섬 검증은 아니다.
- `generalization_validation_candidate_code.json`에 검증 시작 전 코드 해시를 기록했다. 별도 validation 16개 최초 실행 시작. 최초 결과를 보존하며 그 결과를 본 뒤 고치면 이후 재평가는 개발 회귀로 구분한다.
- 같은 개발 질문 중 계획 LLM 호출이 양쪽에 존재한 19개에서 첫 계획 입력 문자 수 중앙값 33,906→18,823(44.48% 감소). JSON 메시지 문자 수이며 과금 토큰/총 사용량 수치가 아니다. `generalization_prompt_comparison.json`에 쌍별 수치 보존.
- 최초 별도 검증 v04 실패 확인: 질문은 자연어 UTC 범위이며 기준 열을 직접 명명하지 않는다(초기 요약의 'explicit interval_end' 표기는 실제 fixture 문구와 달랐다). 메타 기본 시간 기준 및 계획 time_basis는 interval_end인데, SQL/정적 필터는 시작 경계에 interval_start를 혼용했다. 기준 SUM 443, 생성 SUM 286. 원문/기준 SQL은 변경하지 않고 raw 실패로 보존한다. 최초 검증 실행 중 생산 코드는 변경하지 않으며 종료 후 기본 시간 기준 일관성 개선 대상으로 삼는다.
- 실제 Streamlit 화면 검증: 문맥 FAB10을 선택하고 질문에 FAB13을 명시했다. 계획 FAB13/explicit_user, SQL fab13.live_process_events_fab13의 COUNT(*) WHERE lot_id IS NULL, 결과 864, 최종 답변도 864개 및 시뮬레이션 출처 명시, 전체 run_completed 확인. `generalization_browser_fab_override.json`에 관찰 기록 보존. 브라우저 UI부터 planner/supervisor/Text2SQL/reflection/composer/answer_supervisor까지 통과했다.
- 최초 검증 v12 실패: equipment_id IS NOT NULL 행 개수 요청에서 두 계획 모두 설명에는 조건을 쓰고 filters 목록에는 누락했다. SQL은 정확한 NOT NULL 조건을 생성했지만 새 계획 외 필터 검사에서 거절됐다. 실제 기준은 2592개. 검사 완화 대신 명확한 행 개수 요청의 NULL/NOT NULL 조건을 실제 선택 컬럼에 바인딩해 계획에 보존하는 방향으로 수정한다. 최초 결과는 그대로 남긴다.

## 최초 별도 검증 결과와 이후 수정

- 최초 validation 완료 **14/16**: 조회 가능 **12/14**, negative **2/2**, rate limit 0, 기준 전후 변화 0. v04(시간 경계), v12(NOT NULL 계획 누락) 실패를 원본에 보존했다. `generalization_first_validation_integrity.json`에서 최초 실행 종료까지 코드 해시 불변 확인.
- 이후 변경: 날짜/기간 질문의 스냅샷 범위는 명시한 interval_start/interval_end를 우선하고, 명시가 없으면 해당 메타 default_time_column에 두 경계를 일관되게 맞추도록 계획 검증한다. 혼용 계획은 실행 전에 거절해 수정한다. 명시 시작 시각/완전 포함 구간 및 편집된 기본 시간 열은 보존한다.
- 행 개수 요청의 명확한 NULL/NOT NULL 표현은 실제 선택 테이블에서 유일하게 확인되는 컬럼에 바인딩해 계획 filters에 보존한다. 반대 NULL 조건과 충돌하거나 출처가 모호하면 거절한다. 문자열 'NULL'과 NULL 행 제외 표현은 단순 NULL 필터로 추론하지 않는다.
- 시간 기준 3변형과 이미 본 v04/v12 2건을 합한 5개 개발 회귀 probe를 작성했다. 이는 새로운 별도 검증 점수가 아니며 최초 14/16과 합쳐서 통과율을 만들지 않는다.
- 수정 후 전체 테스트 472개 통과. 현재 메타+현재 계획 검증+실제 DB 기준 SQL로 저장 성공 사례를 재실행: 이번 라운드 저장 사례 **43/43** 통과, 이전 라운드 8개 중 **7/8** 통과. 이전 `top_wip_fab13` 계획은 top-N 필수 필드 추가 전 형식이라 result_limit이 없어 새 검증에서 거절됨. 이전 형식 1건을 조용히 제외하거나 검증을 완화하지 않고 보고서에 구분했다. 전체 51개 중 50개 허용·결과 일치, 1개 이전 계획 계약 거절.
- 수정 후 개발 회귀 5/5 통과. FAB13 시점 집합: 기본 종료 기준 00/02/04시, 명시 시작 기준 02/04/06시, 완전 포함 조건 02/04시. v04 합계 443, v12 NOT NULL 행 수 2592 일치.
- 같은 UI 대화에서 다음 질문으로 equipment_id NOT NULL 행 수를 요청해 2592개 최종 답변/run_completed 확인. 이전 lot_id IS NULL 조건은 새 SQL에 없었다. `generalization_browser_followup_null.json`에 관찰 보존.
- UI 로그에서 equipment_id 안의 'pm'을 PM 모델 도메인/명시 테이블로 오인해 불필요한 PM·toolgroups 메타까지 선택하던 문제 발견. PM 및 논리/물리 테이블 명시 판별에 식별자 경계를 적용했다. pm_percent도 모델 PM 언급으로 오인하지 않으며, pm_fab13/인용된 물리 테이블 이름은 정상 인식한다. 관련 테스트 90개 통과 후 최종 전체 테스트 및 이미 본 v04/v12 반복 검증을 실행했다.
- 최종 실제 화면 기간 질문에서 추가 진입 문제 발견: 직접 Text2SQL은 443을 계산했지만 상위 Planner가 물리 테이블/데이터 계층을 요구하며 needs_clarification으로 실행을 막았다. 이 실패도 `generalization_browser_time_before_planner_fix.json`에 보존했다.
- Planner v3: 물리 테이블/스키마/컬럼/데이터 계층은 사용자 필수 슬롯이 아니라 메타를 읽는 Text2SQL의 탐색 책임으로 정했다. 이러한 슬롯만 빠진 DB 질문과 resolved FAB은 조회로 위임한다. FAB·metric·date_basis 등 업무 모호성은 유지한다. 같은 질문을 추가 힌트 없이 기존 실패 대화에서 다시 실행해 복구를 검증 중이다.
- Planner 수정 후 같은 UI 질문을 추가 테이블 힌트 없이 재실행: 실제 Text2SQL 호출 → FAB12 live_process_snapshots의 interval_end >=00 / <06 → SUM(lot_starts)=443 → 최종 답변 443, 종료시각 기준과 시뮬레이션 한계 명시, run_completed 확인. 이전 실패 응답이 같은 대화에 남아 있어도 정상 복구했다. `generalization_browser_time_after_planner_fix.json` 보존.
- 최종 코드 전체 테스트 **474개** 통과. 식별자 매칭 수정 후 기존 실제 DB 회귀도 **79/79** 통과. 최종 업무 테이블 보존/키 중복 점검 재확인 완료.

## 추가 작업 종료

- 종료: 2026-09-08 07:51:09 KST. 요청한 추가 90분을 완료했다.
- 이 작업의 heartbeat `text2sql-panda-8`는 PAUSED로 확인했다.
- 최종 결과: `docs/text2sql_generalization_results_20260908.md`. 최종 코드/평가 해시: `generalization_final_manifest.json`.
- fix/adv_t2s 유지, 커밋·병합·배포 없음. 최초 별도 검증 14/16 원본 보존, 수정 후 개발 회귀 5/5와 반복 2/2, 실제 화면 복구 확인. 일반 정확도 100% 보장은 주장하지 않는다.
