export const AGENTS = {
  input: ['요청 확인', '질문의 범위를 확인해요'],
  planner: ['분석 계획', '필요한 분석 단계를 정리해요'],
  supervisor: ['진행 관리', '분석 순서와 결과를 확인해요'],
  dispatcher: ['에이전트 연결', '분석에 필요한 에이전트를 연결해요'],
  text2sql: ['데이터 조회', '조건에 맞는 생산 데이터를 조회해요'],
  rag: ['문서 검색', '관련 공정 문서에서 근거를 찾아요'],
  case_search: ['사례 검색', '관련된 운영 사례를 찾아요'],
  impact: ['영향 분석', '가정과 데이터를 바탕으로 영향을 계산해요'],
  visualization: ['차트 생성', '조회 결과를 차트로 정리해요'],
  reflection: ['근거 검토', '답변의 근거와 한계를 확인해요'],
  composer: ['답변 작성', '분석 결과를 읽기 쉽게 정리해요'],
  answer_supervisor: ['답변 검토', '질문에 맞는 답변인지 확인해요'],
};
export function agentLabel(node) { return AGENTS[node]?.[0] || node || '분석'; }
export function progressLabel(events) {
  const last = events.at(-1);
  return AGENTS[last?.node]?.[1] || '요청을 전달하고 있어요';
}
export function resultStatus(status) {
  if (status === 'streaming') return { label: '분석 중', tone: 'progress' };
  if (status === 'succeeded') return { label: '분석 완료', tone: 'success' };
  if (status === 'needs_clarification') return { label: '확인이 필요해요', tone: 'warning' };
  if (status === 'data_unavailable') return { label: '데이터 확인 필요', tone: 'warning' };
  if (status === 'cancelled') return { label: '응답 수신 중단', tone: 'muted' };
  if (status === 'failed') return { label: '분석을 마치지 못했어요', tone: 'danger' };
  return { label: '결과 검토 필요', tone: 'warning' };
}
export function parseSseBlock(block) {
  const lines = block.split(/\r?\n/);
  const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).replace(/^ /, '')).join('\n');
  if (!data.trim()) return null;
  const event = JSON.parse(data);
  if (!event || typeof event !== 'object' || Array.isArray(event)) throw new Error('올바르지 않은 응답 형식이에요.');
  return event;
}
export async function consumeSse(response, onEvent) {
  if (!response.ok) throw new Error(`서버가 요청을 처리하지 못했어요. (HTTP ${response.status})`);
  if (!response.body) throw new Error('응답 연결을 열지 못했어요.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let completed = false;
  function dispatch(block) {
    const event = parseSseBlock(block);
    if (!event) return;
    if (event.type === 'run_failed') throw new Error(event.message || '분석 중 오류가 발생했어요.');
    if (event.type === 'run_cancelled') throw new Error('서버에서 분석을 중단했어요. 다시 질문해 주세요.');
    onEvent(event);
    if (event.type === 'run_completed') completed = true;
  }
  try {
    while (!completed) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || '';
      for (const block of blocks) { dispatch(block); if (completed) break; }
      if (done) { if (!completed && buffer.trim()) dispatch(buffer); break; }
    }
    if (!completed) throw new Error('답변이 완성되기 전에 연결이 끊겼어요. 다시 시도해 주세요.');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
export function buildPayload(message, context, conversationId) {
  return { message, ...(conversationId ? { conversation_id: conversationId } : {}),
    ...Object.fromEntries(Object.entries(context).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.trim()])) };
}
export function rowsFromResult(result, events = []) {
  if (result?.query_result?.rows?.length) return result.query_result.rows;
  if (result?.chart?.source_rows?.length) return result.chart.source_rows;
  if (result?.chart?.rows?.length) return result.chart.rows;
  const evidence = [...(result?.evidence || [])].reverse().find(item => item.source_type === 'text2sql_plan' && item.metadata?.sample_rows?.length);
  if (evidence) return evidence.metadata.sample_rows;
  const event = [...events].reverse().find(item => item.data?.sample_rows?.length);
  return event?.data.sample_rows || [];
}
export function toCsv(rows) {
  if (!rows.length) return '';
  const fields = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const cell = value => {
    let text = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    if (typeof value === 'string' && /^[=+@\-\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  return '\uFEFF' + [fields.map(cell).join(','), ...rows.map(row => fields.map(key => cell(row[key])).join(','))].join('\r\n');
}

// Retrying repeats the original request, even if the sidebar scope changed later.
export function requestForAttempt(message, context, conversationId, previousAttempt) {
  return previousAttempt?.requestPayload
    ? { ...previousAttempt.requestPayload }
    : buildPayload(message, context, conversationId);
}
export function restoreMessages(messages) {
  return messages.map(message => ({
    ...message,
    ...(message.status === 'streaming' ? { status: 'cancelled' } : {}),
    ...(message.feedback === 'pending' ? { feedback: null } : {}),
  }));
}
