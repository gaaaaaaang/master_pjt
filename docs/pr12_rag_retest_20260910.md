# PR #12 RAG 재평가 — 2026-09-10

[PR #12](https://github.com/gaaaaaaang/master_pjt/pull/12)의 병합 커밋 `374e1000ea761dc6ea3bbcd98b00191e43f46d8a`를 별도로 export하여 **RAG만 새로 실행**했다. 기존 결과를 최신 성능으로 재사용하지 않았다. 검색 근거 누락은 개선됐고, 답변 근거 충실도 평균도 높아졌다. 관련성의 상승 폭은 작아서 전반적인 답변 품질 안정화를 확정하기는 어렵다.

## 이전 평가와 비교

이전 대화의 `4ebf833` 실제 평가와 **동일 fixture·corpus SHA256**, `gpt-4.1`, `text-embedding-3-large`, Ragas 0.2.15, local 검색, feature reranker를 사용했다. 현재도 dense/Milvus는 미설정이다. RAG 생성·검토에 temperature=0을 명시하는 것은 PR 자체의 변경이다. 미커밋 Text2SQL·프론트엔드 수정은 평가에 포함하지 않았다.

답변 가능한 질문 7개를 기준으로 비교한다. 이 중 KB 업데이트 질문은 일부 요구에 대한 문서 근거가 없어 `partial`이 적절하며, 분모에서 제외하지 않았다.

| 지표 | 이전 | PR #12 재평가 | 차이 |
|---|---:|---:|---:|
| Ragas Faithfulness | 0.8286 | **0.8881** | +0.0595 |
| Ragas Answer Relevancy | 0.4425 | **0.4504** | +0.0079 |
| Ragas Context Precision, without reference | 0.8571 | **1.0000** | +0.1429 |
| 필수 evidence marker 전체 확보 | 5/7 | **7/7** | +2문항 |

전체 10문항(답변 불가 3개 포함)의 점수도 함께 보존한다.

| 지표 | 이전 전체 10개 | 이번 전체 10개 |
|---|---:|---:|
| Faithfulness | 0.6467 | **0.6550** |
| Answer Relevancy | 0.3098 | **0.3153** |
| Context Precision, without reference | 0.6000 | **0.8000** |

이번 10개 모두 채점 완료, API/metric 오류 및 결측 점수는 0개였다. 최종 상태는 `supported` 6개, `partial` 1개, `insufficient` 3개다. 답변 불가 3개는 모두 `insufficient`를 유지했다. 정상적인 보류 답변도 생성 평가 지표에서 낮은 점수를 받을 수 있으므로 전체와 부분집합을 구분했다.

Context Precision은 정답 context annotation 없이 답변과 검색 근거를 비교하는 Ragas variant다. 1.0이 전체 검색 정확도 100%라는 뜻은 아니다. 완전한 gold answer/context가 없어 Context Recall과 Answer Correctness는 추가 산출하지 않았다.

## 문항별 관찰

| 문항 | 이번 Faithfulness | 이번 Relevancy | 확인 내용 |
|---|---:|---:|---|
| PM 승인·복구 기록 | 0.8000 | 0.4945 | supported 유지; 이전 Faithfulness와 동일 |
| Hold 보류·해제 비교 | 1.0000 | 0.5437 | 근거 1→2개, 보류·해제 필수 marker 모두 확보 |
| 장비 정지 시 역할 구분 | 1.0000 | 0.4137 | 관련성 0.2314→0.4137 |
| 대체 장비·첫 lot 추적 | 1.0000 | 0.5320 | supported 유지 |
| Timelink 배정·품질 판단 | 0.6667 | 0.5197 | Faithfulness 1.0000→0.6667 하락 |
| KB 업데이트·버전 관리 | 0.7500 | 0.0000 | 근거 0→2개; insufficient→partial |
| AutoSched attach 필드 | 1.0000 | 0.6491 | supported 유지; 관련성 0.7327→0.6491 |

**확인된 개선:** KB 질문의 `PB-KB-001` 검색 근거가 복구됐다. 답변은 업데이트 후보·기록·청크 구성·태그 기준을 설명하면서, 버전 번호·개정 이력 기준은 문서에서 확인되지 않는다고 명시한다. Hold 질문도 `PB-HL-001`, `PB-HL-002`를 함께 확보했다. 필수 marker 판정은 이전과 이번의 실제 반환 evidence에 동일한 `covered_units` 함수를 적용한 결과다.

**남은 확인 사항:** Timelink의 점수는 하락했지만, 이번 답변의 Scheduler 우선 배정과 Engineer 품질·rework 판단은 반환 원문에도 있다. 이번 실행은 Ragas 내부 claim별 판정까지 저장하지 않아 하락 원인을 생성 오류나 judge 오류 중 하나로 단정하지 않았다. 필요 시 이 문항의 원자 주장별 재검토가 우선이다.

KB의 Relevancy 0은 검색 실패를 뜻하지 않는다. PR의 기존 분석은 부분 답변이 Ragas noncommittal 처리로 0이 될 수 있음을 보였지만, 이번 실행의 내부 판정은 보존되지 않았으므로 이번 0점의 원인으로 확정하지 않는다. 점수를 높이려고 한계 설명을 제거하거나 `supported`로 바꾸지 않았다.

## 회귀·인용·실행 검증

- 격리된 병합 커밋의 `test_rag*.py`: **149 passed**, 실패 0, dependency deprecation 경고 2개.
- 반환된 **32개 인용 모두** 해당 검색 evidence의 원문 및 source_document/page_number와 일치했다. 이는 인용 원문·메타데이터 검증이며 의미적 타당성 100%를 뜻하지 않는다.
- 생성 API를 사용하는 8문항 모두 `generation_attempts=1`, `validation_errors=[]`였다. 근거가 없는 2문항은 직접 보류 경로다. 이번 실행에서 재생성은 필요하지 않았다.
- 검색·생성·내부 검토 시간은 전체 10문항 평균 **4.84초**. 후속 Ragas 채점 시간과 HTTP/SSE·전체 그래프는 제외했다.
- 같은 코드의 반복 실험이나 같은 시각의 paired baseline 재실행은 하지 않았다. 기존 개발셋과 동일 모델 judge의 단일 재평가이므로 상승 폭 전부를 PR의 인과 효과로 해석하지 않는다.

## 결과와 재현

- [이번 원문 답변·검색 근거·Ragas 점수](../apps/assistant/output/evals/pr12_rag_retest_20260910/ragas.json)
- [전후 집계·문항별 비교·인용 감사](../apps/assistant/output/evals/pr12_rag_retest_20260910/summary.json)
- [회귀 검사 기록](../apps/assistant/output/evals/pr12_rag_retest_20260910/regression.json)
- [이전 평가 원본](../apps/assistant/output/evals/adv_integrate_kpi_20260910/ragas_live.json)

프로젝트 root에서 기존 `.env`와 임시 Ragas 환경을 사용했다. 재실행 시 결과 파일은 다른 이름으로 지정한다.

```bash
/private/tmp/adv-integrate-ragas-env/bin/python \
  apps/assistant/scripts/evaluate_adv_integrate_kpi.py rag \
  --source-root /private/tmp/pr12-rag-retest-374e100 \
  --output /tmp/pr12-rag-rerun.json
```

PR 안에 저장된 고도화 중간 버전의 0.9714/0.4640은 이번 재평가 값이 아니다. 이번 병합 커밋의 새 측정값은 위 표의 **0.8881/0.4504/1.0000**이다.
