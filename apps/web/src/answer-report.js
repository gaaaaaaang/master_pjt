import { resultStatus } from './chat-model.js';

export function answerReport(message) {
  const result = message.result || {};
  const sections = ['# FAB Assistant 분석 기록', `상태: ${resultStatus(result.status).label}`, '## 질문', message.question || '', '## 답변', result.answer || '반환된 답변 없음'];
  const scope = Object.entries(message.requestPayload || {}).filter(([key]) => ['fab', 'line', 'process'].includes(key));
  if (scope.length) sections.push('## 명시한 분석 범위', scope.map(([key, value]) => `- ${key}: ${value}`).join('\n'));
  if (result.evidence?.length) sections.push('## 근거', result.evidence.map((item, index) => `${index + 1}. ${item.title || '제목 없음'} (${item.source_type || '출처 미지정'})\n${item.content || ''}`).join('\n\n'));
  else sections.push('## 근거', '별도 첨부된 근거 없음');
  if (result.limitations?.length) sections.push('## 해석할 때 확인할 점', [...new Set(result.limitations)].map(item => `- ${item}`).join('\n'));
  return sections.join('\n\n') + '\n';
}
