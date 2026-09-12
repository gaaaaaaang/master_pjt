# FAB Assistant 승인 후 실모델 재시험

2026-09-12 · `feat/final_adv` · 기존 변경을 보존했고 커밋·푸시는 하지 않았다.

사용자가 질문, 로컬 FAB 공정 조회 결과, 관련 문서 발췌를 현재 설정된
`skax.ai-talentlab.com` 모델 API에 보내는 것을 명시적으로 승인했다.
승인 이후 실제 `gpt-4.1` 호출을 수행했다. 이전 문서의 외부 전송 차단 상태는 해소됐다.

## 결과

| 검증 | 결과 |
|---|---|
| FAB11 → FAB13 → 사진의 비교·진단·차트 → 암시적 비교 → 24시간 추세 | 수정 후 5/5 완료 |
| FAB10 현황, FAB12 현황·후속 질문·영향·추세·순위, FAB13 진단, 문서 지식 | 재시험 포함 8/8 완료 |
| 중복을 제거한 실모델 질문 | 13/13 완료 |
| 원시 DB 자료를 별도 계산한 수치·범위 검증 | 12/12 일치 |
| 문서 지식 답변 | 인용 4개, 원문 인용 및 모델 검토 통과 |
| 실제 localhost HTTP/SSE + 모델 API 비교 질문 | 29.043초, 이벤트 20개, 조회 12행, 차트 전달 |
| 최종 백엔드 회귀시험 | 890개 통과 |
| 프런트엔드 시험 / 빌드 | 37개 통과 / 성공 |
| 애플리케이션 및 신규 검증 코드 Ruff | 통과 |

13/13은 개선 과정에서 성공한 재시험을 모은 결과이며, 한 번에 모두 성공한 비율이나
최종 소스에서 전체 13개를 재실행한 결과는 아니다. 각 실행의 소스 해시와 원래 실패 기록을 보존했다.
매 턴 격리된 SQLite 대화 저장소를 다시 읽어 후속 질문의 FAB·공정·기간이 유지되는지 확인했다.
브라우저 저장 복원은 앞선 후속 개선 시험에서 확인했으며, 이번 HTTP 시험은 브라우저 조작 시험은 아니다.

사진 질문의 핵심 수치는 동일 관측 시각인 2026-09-12 00:00 KST 기준
FAB11 188 LOT, FAB13 159 LOT, 차이 29 LOT이다. 공정별 차이는 CMP +62,
Photo −48, Etch +6, Deposition +5, Implant +4, Metrology 0 LOT로 원시 자료와 일치했다.
영향 분석의 수치 검증은 명시된 비례 가정의 산술 검증이며 실제 인과 예측을 입증한 것은 아니다.

## 재시험에서 발견하고 수정한 문제

- 첫 비교 실행은 3/5였다. 원인 후보 부재 안내 누락, 공정별 차이를 원인으로 단정하는 표현,
  조회 결과의 행 수를 근거 없는 측정값으로 오인하는 검토 규칙을 수정했다.
- 모델 검토가 승인해도 문장 안에 원인 후보 부재와 추측이 함께 남는 사례를 실제 HTTP 답변에서 발견했다.
  해당 표현을 검출하고, 현재 FAB 비교에서 원인 표현만 문제가 있으면 검증된 조회 요약과
  추가 점검 항목으로 재구성한 뒤 수치·범위 검증을 다시 거치도록 했다.
  임의 수치까지 섞인 답변은 이 보완으로 통과시키지 않는다. 대문자 `999LOT` 검출 누락도 수정했다.
- 모델이 이중 이스케이프한 문단 줄바꿈을 복원하고, 반복적인 데이터 생성 설명은
  출처 정보에 보존하면서 본문은 관측 시각·운영 지표·인과 판단 한계를 중심으로 표시한다.
  마지막 HTTP 답변에 남은 “시뮬레이션 기반 최신 스냅샷 값” 변형은 표시 규칙을 추가해
  저장된 실제 응답으로 로컬 재검증했다. 이 마지막 표시 수정 뒤 외부 모델을 다시 호출하지는 않았다.
- 비교와 회귀 시험을 동시에 연속 호출하자 실제 HTTP 429 `rate_limit_tpm`이 발생했다.
  이는 모델 API의 분당 토큰 한도다. 같은 요청 안에서 이미 429가 난 주소를 반복 호출하지 않도록 했고,
  새 질문에서는 다시 호출할 수 있다. 실패 항목은 선행 문맥과 함께 질문 사이 25초 간격으로 재시험했다.

최종 HTTP 응답은 원인 단정 없이 비교값과 차트를 전달했고 모델 검토를 통과했다.
검토 규칙으로 모든 자연어 해석의 정확성을 보장하는 것은 아니다. 수치 일치, 차트 전달,
대화 범위 복원과 원문 인용 검증을 별도로 확인한 결과로 해석해야 한다.
데모에서는 한 질문의 답변이 끝난 뒤 다음 질문을 진행하고, 빠른 연속 시연에는 호출 간격을 둔다.

## 재현과 산출물

`apps/assistant/output/evals/final_adv_live_approved/validation_index.json`에 최종 검사와 파일 해시를 기록했다.
`accepted_cases.json`은 성공한 13개 실행의 원래 보고서와 소스 해시를 보존한 모음이고,
`accepted_cases_value_audit.json`은 별도 계산 결과다.
`comparison_iteration1.json`, `regression_iteration1.json`, `http_sse_iteration1.json`,
`http_sse_iteration2.json`을 포함한 실패·중간 응답도 삭제하지 않았다.
`http_sse_final.json`은 실제 HTTP 결과, `http_sse_presentation_replay.json`은 이후 표시 규칙의 로컬 재검증이다.

저장소 루트에서 실행한다. 실모델 스크립트는 승인된 HTTPS 호스트와 모델 배포 경로만 허용하고,
실제 모델 호출을 모킹하지 않는다. LangSmith 전송은 끄고 로컬 문서 검색과 localhost DB를 사용했다.
다음 두 실모델 시험은 동시에 실행하지 않는다.

```sh
PYTHONPATH=apps/assistant/src:apps/assistant/scripts .venv/bin/python apps/assistant/scripts/evaluate_fab_followup_live.py --live --suite comparison --pause 25 --output /tmp/fab_comparison_retest.json
PYTHONPATH=apps/assistant/src:apps/assistant/scripts .venv/bin/python apps/assistant/scripts/evaluate_fab_followup_live.py --live --suite regression --pause 25 --output /tmp/fab_regression_retest.json
.venv/bin/python apps/assistant/scripts/audit_fab_followup_live.py --raw apps/assistant/output/evals/final_adv_live_approved/raw_reference.json --report apps/assistant/output/evals/final_adv_live_approved/accepted_cases.json
```

고정된 WIP 기대값과 원시 자료 캡처는 이번 적재 상태 기준이다. DB 적재가 바뀌면 새 캡처와 기대값을 사용해야 한다.
