# feat/adv_integrate 에이전트 성능 평가 — 2026-09-10

실제 PostgreSQL·LLM 호출로 평가했다. 조회 EX는 **95.45%**, 복합 답변 judge 적절성은 **92.86%**, 계획·라우팅 계약은 **28/28**, 복합 질의 평균 실행시간은 **24.70초**다. 그러나 복합 질의의 시스템 성공 상태는 **10/14(71.43%)**이고, RAG의 답변 관련성은 개선이 필요하다. 소규모 기존 개발 테스트셋에서의 결과이며 운영 전반의 정확도나 KPI 전체 달성을 확정하지 않는다.

## 평가 기준과 범위

- 브랜치: `feat/adv_integrate`, 커밋 `4ebf83350a706afb158653e0c51d5c43b60f9c9e`.
- `git archive`로 내보낸 `/private/tmp/adv-integrate-eval-20260910`의 에이전트 코드를 import했다. 작업 디렉터리의 미커밋 에이전트 수정은 포함하지 않았다.
- 소스 fingerprint: `607d3d3a64679143db742ade8abf4f3a4fc492e31529db22284fc69685c015d6`.
- 모델: 생성과 judge 모두 설정된 `gpt-4.1`. Judge는 별도 호출이지만 동일 모델이므로 모델 간 독립 평가가 아니다. 전문가 채점·반복 실행·judge 간 일치도는 미측정이다.
- 실행 환경의 DB와 문서 corpus를 사용했다. DB 전체 스냅샷을 고정한 것은 아니다. SQL oracle의 실행 전후 결과는 전 문항에서 안정적이었다. 문서 corpus와 fixture hash는 raw report에 있다.
- RAG: 현재 설정의 **로컬 검색 + feature reranker + 실제 grounded 답변 생성**. `VECTOR_DB_URL` 미설정이므로 Milvus dense/hybrid 성능 평가가 아니다.
- 기존 회귀셋을 재사용했다. 새로운 독립 holdout을 수집하지 않았으며, 과거에 validation이라 명명된 셋도 개발에 사용된 이력이 있어 독립 검증셋으로 해석하지 않는다.

## KPI 대비

사용자가 지정한 [Talent Lab 페이지](https://skax.ai-talentlab.com/master-mentee/task/3338)는 로그인 화면으로 연결되어 이번 실행에서 본문을 확인하지 못했다. 아래 목표는 저장소 [기획 문서](planning/problem_definition_and_service_planning.md)의 KPI 표를 사용했다. 페이지 최신본과 동일하다는 확인은 남아 있다.

| KPI | 목표 | 이번 측정 | 목표 대비 | 판단 |
|---|---:|---:|---:|---|
| 단순 조회 정확도 | ≥75% | EX 21/22 = **95.45%** | +20.45%p, 목표의 127.27% | 정답 SQL이 있는 평가셋에서 충족 |
| 복합 추론 적절성 | ≥70% | Judge 13/14 = **92.86%** | +22.86%p, 목표의 132.65% | 이번 rubric 기준 충족 |
| 복합 적절성 + 시스템 성공 | 참고: ≥70% 적용 시 | 10/14 = **71.43%** | +1.43%p | 적절한 설명과 실제 성공을 함께 요구하면 여유가 작음 |
| 질의 라우팅 | ≥90%, 원래 7유형 분류 | 계획 fixture 계약 및 routing judge **28/28 = 100%** | 대리 지표 기준 +10%p | **원래 7분류 KPI 달성 확정 불가** |
| 평균 응답시간 | ≤300초 | 복합 14문항 평균 **24.70초**, 최대 53.27초 | 평균 기준 275.30초 여유 | 이번 그래프 실행 범위 충족 |

현재 구현은 `status/diagnosis/impact/trend/knowledge_lookup/...` 유형을 사용하며, 원래 기획의 대응 추천·후속 맥락을 독립된 분류값으로 모두 출력하지 않는다. 28문항의 실행 agent·상태·slot 계약 통과를 원래 7분류 confusion matrix 정확도로 바꾸어 말할 수 없다. 대응 추천은 별도의 복합 전체 실행에 포함했다.

응답시간은 `Supervisor.run()` 진입부터 최종 결과 반환까지다. 평가 judge 시간은 제외했고, 브라우저·HTTP 왕복·사용자 대기열·대규모 부하를 포함하지 않았다. 여러 평가 프로세스가 동시에 실행된 구간이 있다. 이 수치는 전체 서비스 모든 질문 유형의 평균이 아니다.

## Text2SQL

| 테스트셋 | 정답 SQL 문항 | EX | EM | Soft-F1 |
|---|---:|---:|---:|---:|
| `text2sql_semantic_regression.json` | 8 | 8/8 | 0/8 | raw report에서 문항별 확인 |
| `text2sql_generalization_validation_20260908.json` | 14 | 13/14 | 0/14 | raw report에서 문항별 확인 |
| 합계 | **22** | **21/22, 95.45%** | **0/22, 0%** | **0.4708, 47.08%** |

추가 negative 문항은 2/2 통과했다. SQL을 생성하지 않아야 하는 문항이므로 EX/EM/Soft-F1 분모에서 제외했다. 별도의 79문항 계약 fixture에는 완전한 정답 SQL이 없어 그 계약 점수를 EX/EM으로 사용하지 않았다.

측정 정의:

- **EX:** 기존 독립 SQL oracle과 실제 반환 rows 비교. 행 중복과 값·차원을 비교하고, fixture가 요구한 행/열 순서를 반영한다. 출력 alias는 무시하고 수치 오차는 기존 evaluator 허용치 `1e-6`을 사용한다. 지정된 문맥 열을 허용하는 EX와 엄격 EX 모두 21/22로 동일했다. 정답·예측 결과가 최대 12행이어서 200행 제한에 의한 truncation은 이번 결과에 없었다.
- **EM:** SQLGlot 27.29.0 PostgreSQL 파싱 후 포맷·unquoted identifier 대소문자를 정규화한 AST exact equality. alias, table qualification, predicate, limit는 유지한다. 원문 문자열 일치율이나 Spider EM과 동일하지 않다.
- **Soft-F1:** SQLGlot AST 각 노드의 `(node type, normalized subtree SQL)`를 중복을 보존한 multiset으로 구성한다. 겹치는 feature 수를 `M`, 예측/정답 feature 수를 `P/G`라고 할 때 precision=`M/P`, recall=`M/G`, F1=`2M/(P+G)`이며 문항별 macro 평균이다. 생성 실패는 0점이다. **이 보고서가 정의한 구조 유사도이며 SQLGlot의 공식 지표가 아니다.** 의미 동등성 검증은 EX와 구분한다. [SQLGlot 문서](https://sqlglot.com/sqlglot.html).

EM이 0이어도 EX가 높은 이유는 생성 SQL의 전체 컬럼 경로, 출력 alias, 추가 FAB 필터 때문이다. 예를 들어 정답은 `SUM(wip_lots)`인데 생성은 `SUM(fab12.live_process_snapshots_fab12.wip_lots) AS total_wip_lots`를 사용하고 `fab_id='fab12'` 조건을 추가했다. 같은 DB 결과여도 엄격 AST는 다르다. 이 정의는 중첩 subtree 차이를 반복 반영하므로 작은 구조 변경에도 점수가 크게 내려갈 수 있다.

실패 `v07`: PM 모델과 toolgroups를 조인해 영역별 설정 건수를 요청했으나 `Sort output must identify one declared output column or aggregate alias` 검증 오류로 `unsupported`가 반환됐다. 정답은 11행인데 예측 SQL/rows는 없었다. 구현을 수정해 재채점하지 않고 최초 결과를 보존했다.

## RAG — Ragas 0.2.15

`rag_extension_eval.json` 10문항 전체를 실행했다. 7개는 근거가 기대되는 문항, 3개는 없는 수치/외부 공정값/승인 SOP를 요구하는 답변 불가 문항이다. 모든 지표의 유효 결과는 10/10이고 metric 오류·NaN은 없었다.

| 지표 | 전체 10문항 | 답변 가능 7문항 | 답변 불가 3문항 |
|---|---:|---:|---:|
| Faithfulness | **0.6467** | **0.8286** | 0.2222 |
| Answer Relevancy | **0.3098** | **0.4425** | 0.0000 |
| Context Precision, without reference | **0.6000** | **0.8571** | 0.0000 |

`Faithfulness`, `ResponseRelevancy`, `LLMContextPrecisionWithoutReference`를 실제 Ragas API로 호출했다. Relevancy는 설정된 `text-embedding-3-large`를 사용했고 strictness는 기본값 3이다. Judge temperature는 0이다. Context precision은 **정답 context 기반 precision이 아닌 reference-free variant**다. 완전한 정답 답변·context annotation이 없으므로 Context Recall 및 Answer Correctness는 산출하지 않았다. fixture의 짧은 `review` 문장을 완전한 정답으로 간주하지 않았다. [Ragas metric 문서](https://docs.ragas.io/en/v0.2.0/references/metrics/).

답변 불가 문항은 모두 `insufficient`를 반환했다. 이런 정상적인 보류 응답도 reference-free 생성 지표에서 낮게 나올 수 있어 전체 평균과 답변 가능 부분집합을 함께 표시했다. `insufficient` 3/3은 상태 확인이며 전문가가 검토한 거절 정확도는 아니다.

관찰된 개선 지점:

- `ext_knowledge_update`: `PB-KB-001` 근거가 기대되지만 검색 evidence가 0개이고 `insufficient`; 세 지표 모두 0점. 검색 경로 또는 corpus coverage 확인이 우선이다.
- `ext_pm_approval`: Faithfulness 0.8, Relevancy 0.4988. 승인 조건·복구 기록의 답변 근거 연결을 검토할 필요가 있다.
- `ext_equipment_record`: Faithfulness 1.0이지만 Relevancy 0.2314. 근거에 충실한 것과 질문의 역할별 요구를 직접 충족하는 것은 다르다. 관련성 점수의 원인은 후속 사람 검토로 확인해야 한다.
- Ragas 목표치는 기획 KPI에 따로 정의되어 있지 않아 임의의 pass threshold나 종합 점수를 만들지 않았다.

## Planner·Supervisor와 복합 답변

Judge rubric은 각 항목 1–5점이고, **모든 항목 ≥4이며 critical error가 없어야 통과**다. 입력 artifacts는 데이터로 취급하고 내부 지시·confidence 점수를 채점 근거로 삼지 않도록 지시했다. 별도 judge prompt와 판단 근거는 평가 스크립트·JSON에 보존했다.

| 범위 | 샘플 | 결과 |
|---|---:|---:|
| Planner routing·scope·steps + Supervisor 계획 검토 | 28 | **28/28 통과** |
| 독립 fixture 상태·agent·유형·slot 계약 비교 | 28 | **28/28 일치** |
| Supervisor 오류 주입: 정상 승인·오류 탐지/교정 | 6 | **6/6**, 재검토 judge 6/6 |
| 복합 최종 답변 judge: 근거 정합성·질문 커버리지·불확실성·Supervisor 품질 | 14 | **13/14 통과** |
| 복합 실행 `status=succeeded` | 14 | **10/14** |
| 복합 judge 통과 및 `status=succeeded` | 14 | **10/14** |

계획 문항은 16개 기본 fixture와 12개 variants다. 모든 Planner execution mode는 `llm_chat_completions`였고 Supervisor의 LLM fallback은 표시되지 않았다. 기본적인 좋은 계획 검토에서 100%라고 해서 잘못된 계획을 모두 탐지한다는 의미는 아니다.

전체 실행은 원인 진단 4개, 영향 분석 5개, 대응 추천 5개다. `failed` 3개는 Queue Time 원인, Queue Time 생산 영향, 복합 가동률·cycle-time 영향 질문이며, 설비 down 생산 영향 1개는 `data_unavailable`이다. judge의 `task_completed`는 실패 상태에서도 true가 나오는 사례가 있어 시스템 완료율로 사용하지 않았다.

복합 영향 실패 `sc003_compound_util_cycle_impact`:

- 답변에 `57988 lots`, `-4659 lots`라는 파생 절대량이 들어갔다.
- 실제 Supervisor와 외부 judge 모두 이를 도구 evidence에 직접 제공되지 않은 수치로 판정했다.
- `answer_review.approved=false`, `correction_applied=false`였고 상태는 `failed`인데 원래 답변 문자열이 결과에 남았다.
- 수정 후보는 생성했지만 `Capacity, Cycle Time` estimate 표시 검증에서 다시 거절됐다. **미승인 답변 처리와 복합 영향값 검증 계약**을 함께 점검해야 한다. 단순 산술의 타당성과 도구가 승인한 근거 범위는 구분해서 검토할 사안이다.

추가 Supervisor 오류 주입 결과는 정상 답변 **1/1 승인**, 잘못된 값·FAB·실측 주장·확정 원인·답변 누락 **5/5 탐지 및 교정 적용**이다. 원본 실행은 `supervisor_challenge_validated.json`에 보존했다. 합성 근거를 사용한 소규모 검사이며 실제 질의의 Supervisor 성공률과 합산하지 않는다.

이 평가의 첫 judge 집계는 **5/6**이었다. `SUP-invented-cause`에서 세 평가 차원은 모두 5점이고 이유도 정확한 탐지·교정을 인정했는데, 입력에 의도적으로 넣은 오류를 Supervisor의 `critical_error`로 잘못 세었다. 평가 대상이 Supervisor의 판정·교정임을 명확히 한 rubric으로 **6문항 전부를 judge만 재평가하여 6/6**을 얻었다. agent는 재실행하지 않았다. 첫 판정과 재판정은 `supervisor_challenge_rejudged.json`에 함께 보존했으며, rubric 보정 후 수치를 최초 실행 수치인 것처럼 취급하지 않는다.

별도의 최초 fixture 구성 시도에는 필수 SQL metadata가 빠져 있었으므로 `supervisor_challenge.json`은 정상 성능 산출에서 제외했다.

## 개선 우선순위

1. **복합 실행 완료율:** judge 적절성 92.86%와 시스템 성공 71.43%의 차이를 줄인다. 미승인 원본 반환·교정 실패 원인을 우선 확인한다.
2. **RAG 질문 적합성과 누락 검색:** `ext_knowledge_update` evidence 누락, 역할·승인·복구 조건 질문에 대한 답변 구조를 확인한다. 이후 실제 운영 구성의 Milvus hybrid도 같은 셋으로 별도 측정한다.
3. **Text2SQL join/정렬 검증:** `v07`의 aggregate alias 검증 실패를 재현하고 수정한다. 이번에는 평가 대상 구현을 바꾸지 않았다.
4. **평가셋/KPI 정비:** 원래 7유형과 구현 route의 대응표, 균형 잡힌 별도 검증셋, RAG gold answers/contexts, 다른 judge 모델·사람 검토, 반복 실행을 추가한다.

## 결과 파일과 재현

- [집계 JSON](../apps/assistant/output/evals/adv_integrate_kpi_20260910/summary.json)
- [Text2SQL 원문·정답·예측 SQL·rows](../apps/assistant/output/evals/adv_integrate_kpi_20260910/text2sql.json)
- [Ragas 문항별 답변·근거·점수](../apps/assistant/output/evals/adv_integrate_kpi_20260910/ragas_live.json)
- [Planner·Supervisor 계획 평가](../apps/assistant/output/evals/adv_integrate_kpi_20260910/planner_supervisor.json)
- [복합 실제 실행·judge](../apps/assistant/output/evals/adv_integrate_kpi_20260910/complex_e2e.json)
- [Supervisor 오류 주입 평가](../apps/assistant/output/evals/adv_integrate_kpi_20260910/supervisor_challenge_validated.json)
- [Supervisor judge 재검토·최초 판정](../apps/assistant/output/evals/adv_integrate_kpi_20260910/supervisor_challenge_rejudged.json)
- [평가 스크립트](../apps/assistant/scripts/evaluate_adv_integrate_kpi.py)
- [지표 검증 테스트](../apps/assistant/tests/test_adv_integrate_kpi_metrics.py)

프로젝트 root를 working directory로 두어 `.env`를 로드한다. 소스 export는 위 커밋으로 생성한다. 동일 DB·corpus·모델 배포와 dependency 버전이 있어야 같은 조건이다. 모델 응답의 재현성은 보장되지 않는다.

```bash
.venv/bin/python apps/assistant/scripts/evaluate_adv_integrate_kpi.py sql \
  --source-root /private/tmp/adv-integrate-eval-20260910 --output /tmp/sql-rerun.json
.venv/bin/python apps/assistant/scripts/evaluate_adv_integrate_kpi.py planner \
  --source-root /private/tmp/adv-integrate-eval-20260910 --output /tmp/planner-rerun.json
.venv/bin/python apps/assistant/scripts/evaluate_adv_integrate_kpi.py e2e \
  --source-root /private/tmp/adv-integrate-eval-20260910 --output /tmp/e2e-rerun.json
.venv/bin/python apps/assistant/scripts/evaluate_adv_integrate_kpi.py supervisor \
  --source-root /private/tmp/adv-integrate-eval-20260910 --output /tmp/supervisor-rerun.json
/private/tmp/adv-integrate-ragas-env/bin/python apps/assistant/scripts/evaluate_adv_integrate_kpi.py rag \
  --source-root /private/tmp/adv-integrate-eval-20260910 --output /tmp/ragas-rerun.json
```

Ragas 의존성은 별도 임시 venv에 설치했고 프로젝트 dependency 파일은 바꾸지 않았다. 초기 dependency 실패만 담은 `ragas.json`은 성능 집계에서 제외했다. 새 지표 검증 테스트 8개가 통과했으며 테스트는 포맷 동등성, predicate/table/limit 변형, 결측, 복수 statement, 대칭성을 확인한다.
