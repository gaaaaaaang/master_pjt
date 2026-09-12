# 제조 흐름의 Cycle Time, WIP, 가동률 해석

문서 유형: 공개 학술 자료를 한국어로 요약한 프로젝트 참고 문서.
작성·출처 확인: 2026-09-12. 사내 승인 SOP 또는 실제 FAB 실측 자료가 아니다.
아래 식은 개념 설명용이며 이 프로젝트의 특정 FAB에 보정된 예측 모델이 아니다.

## Cycle Time과 Queue Time, Cycle Time Degradation

Cycle Time은 정의한 공정 범위에서 작업이 시작되어 끝날 때까지 걸리는 시간으로, 처리 시간과 대기 시간을 포함한다. Queue Time은 재공이 대기열·버퍼 등에 머무는 시간이다. 공정 단위인지 전체 생산 경로인지 측정 범위를 먼저 맞춰야 한다.

이 프로젝트에서 일반 표현인 **Cycle Time Degradation(사이클 타임 악화)**을 해석할 때는 동일한 제품·공정·측정 기준에서 기준 기간보다 Cycle Time이 길어진 상태를 뜻한다. 이것은 이 참고 문서의 용어 설명이며, SMT2020이나 사내의 별도 공식 KPI 정의를 확인한 것은 아니다. 악화 여부를 판단하려면 비교 기준과 관측 기간이 필요하다. 처리 시간 증가인지 대기 시간 증가인지 구분하면 점검 대상을 좁힐 수 있다.

근거: MIT OpenCourseWare, *Lean Thinking Part II* (2012), 슬라이드 3–5. 원문은 시간 측정의 현장 정의 확인과 처리·대기 시간 구분을 설명한다. ‘Degradation’의 프로젝트 용어 설명은 그 정의에 따른 해석이다.
출처: https://ocw.mit.edu/courses/16-660j-introduction-to-lean-six-sigma-methods-january-iap-2012/f044431285fe865dd395b27f5d3c83ce_MIT16_660JIAP12_1-3part2.pdf

## WIP과 Cycle Time: Little의 법칙

동일한 시스템 경계에서 장기 평균을 일관되게 측정하면 **평균 WIP = 평균 처리율 × 평균 Cycle Time**이라는 관계를 사용한다. 처리율은 완료 LOT 개수 자체가 아니라 단위 시간당 완료 LOT 수이다. WIP과 처리율의 단위가 LOT 및 LOT/시간이면 Cycle Time 단위는 시간이다.

처리율이 같다는 조건에서 평균 WIP이 커지면 평균 Cycle Time도 길어진다. 이 식만으로 WIP 증가가 관측된 지연의 확정 원인이라고 판정하거나, 단일 시각 WIP으로 미래 Cycle Time을 예측할 수는 없다. 기준 기간·시스템 경계·유입과 유출의 안정성을 확인해야 한다.

근거: MIT OpenCourseWare, *Lean Thinking Part II* (2012), 슬라이드 13 및 *M/M/1 Queue* (2016), 1쪽. 평균값의 관계와 단위 해석을 요약했다.
출처: https://ocw.mit.edu/courses/16-660j-introduction-to-lean-six-sigma-methods-january-iap-2012/f044431285fe865dd395b27f5d3c83ce_MIT16_660JIAP12_1-3part2.pdf

## 가동률과 대기 시간의 관계

MIT의 M/M/1 예제는 단일 서버, 지수분포 서비스 시간, 무한 대기 공간, 유입률 λ가 처리율 μ보다 작은 정상상태를 가정한다. 가동률은 ρ=λ/μ, 평균 시스템 체류 시간은 1/(μ−λ), 평균 대기 시간은 λ/[μ(μ−λ)]이다. 이 조건에서 처리 능력을 고정하고 유입을 늘려 가동률이 한계에 가까워지면 대기와 전체 Cycle Time이 급격히 커질 수 있다.

실제 FAB의 재진입 경로·병렬 장비·배치·고장·제품 혼합은 이 단순 모형과 다르다. 따라서 위 식을 FAB11·12·13의 정확한 지연 예측이나 공통 가동률 임계값으로 사용하지 않는다. 관측된 WIP·대기·가동률은 원인 후보를 찾는 단서이며, 확정 진단에는 해당 구간의 이벤트와 공정 조건이 더 필요하다.

근거: MIT OpenCourseWare, *M/M/1 Queue*, Introduction to Manufacturing Systems (2016), 1–2쪽. FAB 적용상의 한계는 단일 서버 가정과의 차이를 명시한 것이다.
출처: https://www.ocw.mit.edu/courses/2-854-introduction-to-manufacturing-systems-fall-2016/927056a1af54772a587fd84ad4951e71_MIT2_854F16_Mm1Queue.pdf

## FAB에서 함께 볼 지표

NIST의 반도체 FAB 시뮬레이션 연구는 투입 간격을 바꾸며 평균 Cycle Time, 병목의 유휴 비율, Cycle Time의 변동, 완료 LOT, 평균 WIP을 함께 비교했다. 처리량을 최대화하는 조건이 최소 WIP·최소 Cycle Time 조건과 같지는 않았다. 따라서 가동률 하나만으로 성능이 좋거나 나쁘다고 확정하지 말고, 같은 기간의 WIP·대기·완료량·변동을 함께 해석한다.

이는 연구의 시뮬레이션 사례에 대한 요약이며, 연구에 나온 수치를 이 프로젝트의 FAB별 권장 설정이나 효과 크기로 옮기지 않는다.

근거: NISTIR 7236, *A Framework for Standard Modular Simulation: Application to Semiconductor Wafer Fabrication*, Section X.F 및 Table XXI–XXII (PDF 15쪽).
출처: https://nvlpubs.nist.gov/nistpubs/Legacy/IR/nistir7236.pdf
