// Deliberately synthetic visual-review data. Never used in live conversations.
export const previewCases = {
  trend: {
    title: '제품별 투입 계획 비교',
    question: 'FAB10의 1월 제품별 투입 계획을 주별로 비교해 줘',
    result: {
      status: 'succeeded', query_type: 'trend', conversation_id: 'preview-trend',
      answer: '## 제품 3의 투입 계획이 가장 많아요\n1월 4주 동안 **제품 3은 1,280건**, 제품 1은 960건으로 계획되어 있어요. 제품 3의 계획량이 제품 1보다 **33.3% 많아요**.\n\n주차별로 보면 제품 3은 3주차에 가장 많고, 제품 1은 매주 비슷한 수준을 유지해요.',
      chart: { type: 'line', title: '1월 주별 투입 계획', series: '제품별 비교', encoding: { x: { field: 'week', title: '주차', sort: ['1주', '2주', '3주', '4주'] }, y: { field: 'count', title: '계획 건수', domain: [0, 500] }, color: { field: 'product' } }, rows: [ { week: '1주', product: '제품 1', count: 220 }, { week: '2주', product: '제품 1', count: 250 }, { week: '3주', product: '제품 1', count: 230 }, { week: '4주', product: '제품 1', count: 260 }, { week: '1주', product: '제품 3', count: 280 }, { week: '2주', product: '제품 3', count: 320 }, { week: '3주', product: '제품 3', count: 400 }, { week: '4주', product: '제품 3', count: 280 } ] },
      sql: "SELECT week, product, count\nFROM preview_release_plan\nORDER BY week, product;",
      limitations: ['디자인 검토용 예시 데이터예요. 실제 FAB 운영 수치가 아니에요.', '투입 계획은 생산 실적과 다를 수 있어요.'],
      evidence: [{ source_type: 'preview', title: '제품별 투입 계획 · 예시 데이터', content: '제품 1과 제품 3의 1월 주별 계획 건수를 비교한 디자인 검토용 데이터입니다.', metadata: { basis: 'start_date', source: 'synthetic preview' } }],
      plan: { execution_steps: [{ agent: 'text2sql', action: '제품별 주간 투입 계획 조회' }, { agent: 'visualization', action: '제품별 라인 차트 생성' }] },
    },
  },
  clarification: { title: '투입 계획 날짜 기준 확인', question: 'fab10의 lotrelease를 날짜 기준으로 라인차트로 그려줘', result: { status: 'needs_clarification', answer: '## 어떤 날짜를 기준으로 볼까요?\n투입을 시작하는 날짜와 납기 날짜 중 원하는 기준을 알려주세요. **조회할 기간**도 함께 알려주시면 차트로 정리해 드릴게요.\n\n- 투입 시작일 기준: 언제 생산에 투입할 계획인지 확인해요.\n- 납기일 기준: 언제까지 생산을 마쳐야 하는지 확인해요.', evidence: [], limitations: [] } },
  unavailable: { title: '현재 대기 시간 확인', question: 'FAB10 Dry_Etch 공정의 현재 대기 시간은 얼마야?', result: { status: 'data_unavailable', answer: '## 현재 대기 시간을 확인할 데이터가 없어요\n현재 연결된 데이터에는 실시간 대기 시간이 포함되어 있지 않아요.\n**조회 가능한 데이터부터 확인**하거나, 기준 시점이 있는 대기 시간 데이터를 연결해 주세요.', evidence: [], limitations: ['실시간 현황을 투입 계획이나 설비 설정값으로 대신 추정하지 않았어요.'] } },
  error: { title: '연결 오류', question: 'FAB10의 WIP 현황을 알려줘', result: null, status: 'failed', error: '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.' },
};
previewCases.large = {
  title: '긴 조회 결과 탐색',
  question: '제품별 일간 투입 계획을 표로 확인하고 싶어',
  result: {
    status: 'succeeded',
    answer: '## 조회 결과에서 필요한 값을 찾아보세요\n**65행의 예시 데이터**를 준비했어요. 데이터 탭에서 검색하고 열 제목을 눌러 정렬할 수 있어요.',
    chart: {
      type: 'line', title: '일별 투입 계획 · 디자인 예시',
      encoding: { x: { field: 'date', title: '날짜' }, y: { field: 'count', title: '계획 건수' } },
      rows: Array.from({ length: 65 }, (_, index) => ({
        date: new Date(Date.UTC(2020, 0, index + 1)).toISOString().slice(0, 10),
        product: `제품 ${index % 3 + 1}`,
        count: 100 + (index * 17) % 120,
      })),
    },
    evidence: [], limitations: ['디자인 검토용 예시이며 실제 운영 수치가 아니에요.'],
  },
};
export function makePreview(key) {
  const item = previewCases[key] || previewCases.trend;
  const nodes = key === 'error' ? ['planner'] : key === 'trend' ? ['planner', 'supervisor', 'text2sql', 'visualization', 'reflection', 'composer'] : ['planner', 'text2sql', 'composer'];
  return { id: 'preview', title: item.title, context: { fab: '', line: '', process: '' }, messages: [{ id: 'preview-user', role: 'user', content: item.question }, { id: 'preview-answer', role: 'assistant', question: item.question, result: item.result, status: item.status || 'done', error: item.error, events: nodes.map(node => ({ node, type: 'node_completed', message: { planner: '질문에서 조회 대상과 기간을 확인했어요.', supervisor: '데이터 조회 후 차트를 생성하도록 연결했어요.', text2sql: key === 'trend' ? '제품별 주간 투입 계획 8행을 조회했어요.' : '답변에 필요한 데이터 조건을 확인했어요.', visualization: '제품별 비교 차트를 만들었어요.', reflection: '수치와 데이터 범위를 검토했어요.', composer: '결과와 해석 시 주의할 점을 정리했어요.' }[node], data: { preview: true } })) }] };
}
