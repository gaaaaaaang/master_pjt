import { resultStatus } from './chat-model.js';

function resolvedScope(message, result) {
  const query = [...(result.evidence || [])].reverse().find(item => item.source_type === 'text2sql_plan' && item.metadata?.query_plan)?.metadata.query_plan;
  const plan = query || result.plan;
  if (!plan) return Object.fromEntries(Object.entries(message.requestPayload || {}).filter(([key]) => ['fab', 'line', 'process'].includes(key)));
  const slots = plan.slots || {};
  return Object.fromEntries(Object.entries({
    fab: slots.fab_ids?.value || plan.fab_id || slots.fab_id?.value,
    line: slots.line?.value,
    process: slots.areas?.value || slots.area?.value || slots.process?.value,
  }).filter(([, value]) => typeof value === 'string' && value.trim()));
}

export function answerReport(message) {
  const result = message.result || {};
  const sections = ['# FAB Assistant 분석 기록', `상태: ${resultStatus(result.status).label}`, '## 질문', message.question || '', '## 답변', result.answer || '반환된 답변 없음'];
  const scope = Object.entries(resolvedScope(message, result));
  if (scope.length) sections.push('## 분석 범위', scope.map(([key, value]) => `- ${key}: ${value}`).join('\n'));
  if (result.evidence?.length) sections.push('## 근거', result.evidence.map((item, index) => `${index + 1}. ${item.title || '제목 없음'} (${item.source_type || '출처 미지정'})\n${item.content || ''}`).join('\n\n'));
  else sections.push('## 근거', '별도 첨부된 근거 없음');
  if (result.limitations?.length) sections.push('## 해석할 때 확인할 점', [...new Set(result.limitations)].map(item => `- ${item}`).join('\n'));
  if (result.data_sources?.length) sections.push('## 데이터 출처', result.data_sources.map(source => `- ${source.label}: ${source.description || source.details?.join(' ')}`).join('\n'));
  return sections.join('\n\n') + '\n';
}
