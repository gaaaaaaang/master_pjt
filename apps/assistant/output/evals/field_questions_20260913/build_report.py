"""Build a reviewable report from preserved live SSE results, not invented answers."""
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
originals = [BASE / 'acceptance' / f'q{n}.json' for n in range(1, 7)] + [BASE / 'rechecks/q7.json']
variants = [BASE / 'rechecks/q1.json', BASE / 'variants/q2.json', BASE / 'empty_verified/q3.json',
            BASE / 'variants/q4.json', BASE / 'variants/q5.json', BASE / 'mapping_verified/q8.json']
outcomes = [
    '제한 시작 이력 조회. 실제 설정자 식별정보는 데이터에 없음.',
    '관련 Hold 이벤트 조회. 업무 제한 사유는 확인할 수 없음을 명시.',
    '장비 조건 안에서 최신 이벤트 조회. 과거 다운 이벤트를 현재 비가동으로 대체하지 않음.',
    '해당 LOT의 최신 hold_start/started 이벤트 조회. 실시간 공식 상태와 구분.',
    '가용성 정의 부재를 미지원으로 구분. 빈 자원 0개라고 만들지 않음.',
    '작업 코드 마스터 부재를 미지원으로 구분. 설비군·이벤트 종류로 대체하지 않음.',
    '제품 경로 테이블을 UNION ALL로 조회. 모델 계측 매핑 200행, 전체 건수·공식 현장 매핑과 구분.',
]

def read(path):
    record = json.loads(path.read_text())
    assert record.get('final'), f'No final answer: {path}'
    return record

records = [read(path) for path in originals]
variant_records = [read(path) for path in variants]
lines = ['# 현장 질문 조회 개선 및 실제 실행 결과', '', '검증일: 2026-09-13', '',
    'LOT·장비 식별자를 보존하고, 실제 데이터가 있는 출처를 선택하며, 요청 항목별 근거와 결측을 구분하도록 공통 조회 경로를 개선했다. '
    '모델용 계측 경로를 합치는 조회도 추가했다. 업무 데이터가 없는 항목을 모두 답할 수 있게 된 것은 아니다.', '',
    '현재 DB의 시뮬레이션 ID로 치환해 기존 `/api/chat/stream`을 실제 호출했다. '
    '신규 현장 데이터는 적재하지 않았다. 아래 결과는 수정·재검증을 거친 각 질문의 최종 확인본이며, '
    '중간 실패 기록은 로컬 평가 디렉터리에 보존했다. API 성공 상태를 정답률로 계산하지 않는다.', '',
    '## 변경 내용', '',
    '- LOT·개별 장비·자원 그룹 식별자를 분리하고 SQL의 정확한 대상 조건을 검증한다.',
    '- 제한·사유·설정자 조회가 FAB 전체 집계 템플릿으로 대체되지 않도록 한다.',
    '- 출처별 존재 확인과 실제 코드 도메인으로 스키마 선택을 보강한다.',
    '- 설정자·사유·가용성·작업 코드의 실제 업무 필드와 이벤트 설명·역할을 구분한다.',
    '- 최신 대상 조회 전에 다운·idle 같은 결과 조건을 임의로 넣지 못하게 한다.',
    '- 공통 구조의 경로 테이블 UNION ALL, 항목별 answer_coverage, 부분·빈 결과 상태와 반환 한도를 지원한다.',
    '- 최종 답변에서 사유·설정자 의미 왜곡과 빈 조회 결과의 과장을 검증한다.', '',
    '## 원문 7개 최종 확인', '', '| 번호 | 실행 상태 | 반환 행 | 확인 결과 |', '| --- | --- | ---: | --- |']
for i, record in enumerate(records):
    final = record['final']
    lines.append(f"| {i+1} | {final['status']} | {final['query_result']['row_count']} | {outcomes[i]} |")
lines += ['', '기존 6번 답변도 실제 작업 코드가 아니라 설비군을 반환한 것이어서 정답으로 볼 수 없다. '
    '현재는 데이터/정의 부족을 분명하게 알린다.', '',
    '## 검증 범위와 남은 한계', '',
    '- 자동 테스트: `pytest -q` 999개 통과. 새 업무 기록 회귀 테스트 43개 포함. 기존 라이브러리 deprecation 경고 2개.',
    '- 원문 7개와 변형·회귀 질문 6개를 실제 API에서 실행하고 답변·SQL·행을 확인했다. '
    'GUI 상호작용 및 신규 현장 데이터 인수 테스트는 이번 검증에 포함하지 않았다.',
    '- 중간 검증에서 여러 테이블 계획 누락, 역할·사유 표현 혼동, 빈 결과 과장과 외부 모델 호출 오류가 있었다. '
    '수정 뒤 영향받은 질문을 재검증했다. 모델 출력의 모든 변형에 대한 보장은 아니다.',
    '- 개인 설정자, 업무 제한 사유, 공식 장비/LOT 현재 상태, 자원 점유·예약·자격, 등록 작업 코드, 공식 계측 매핑은 현장 데이터와 업무 정의가 필요하다.',
    '- 계측 매핑 200행은 반환 상한이다. 공장 전체 등록 건수로 해석하지 않는다. 최신 이벤트도 공식 현재 상태를 대신하지 않는다.',
    '- 자동 승인 검토가 재검증 호출을 한 차례 거절했으나, 사용자 승인과 기존 서비스 경로를 재확인한 뒤 승인되어 검증을 진행했다.', '',
    '[내일 데이터 연결 기준](business_record_data_contract.md)', '',
    '## 변형·회귀 질문', '', '| 질문 | 상태 | 반환 행 |', '| --- | --- | ---: |']
for record in variant_records:
    final = record['final']
    lines.append(f"| {record['request']['message']} | {final['status']} / {final['query_result']['answer_status']} | {final['query_result']['row_count']} |")
for heading, paths, group in [('원문', originals, records), ('변형', variants, variant_records)]:
    for index, (path, record) in enumerate(zip(paths, group), 1):
        final = record['final']
        lines += ['', f'## {heading} {index}: {record["request"]["message"]}', '',
                  f'실제 실행 소요: {record["seconds"]}초. [전체 답변·행·이벤트 JSON]({os.path.relpath(path, ROOT / "docs")})', '',
                  final['answer'], '', '### 실행 SQL', '', '```sql', final.get('sql') or '-- 실행 SQL 없음: 해당 업무 데이터/정의 미지원', '```']
report = ROOT / 'docs/business_record_improvement_20260913.md'
report.write_text('\n'.join(lines) + '\n')
print(report)
