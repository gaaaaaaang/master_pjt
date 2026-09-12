import React, { useState } from 'react';
import { Drawer, Icon } from './ui';
import { GUIDE_FABS, QUESTION_FLOWS, guideQuestion } from './question-guide';

export function QuestionGuide({ initialFab, initialFlowId, onSelect, onClose }) {
  const suggestedFab = String(initialFab || '').trim().toUpperCase();
  const [fab, setFab] = useState(GUIDE_FABS.includes(suggestedFab) ? suggestedFab : 'FAB11');
  const [flowId, setFlowId] = useState(QUESTION_FLOWS.some(item => item.id === initialFlowId) ? initialFlowId : 'status');
  const flow = QUESTION_FLOWS.find(item => item.id === flowId);
  return <Drawer title="질문 가이드" subtitle="질문을 골라 입력하고, 답변을 받은 뒤 다음 단계로 이어가세요." onClose={onClose}>
    <div className="question-guide">
      <label className="guide-fab">질문에 사용할 FAB<select value={fab} onChange={event => setFab(event.target.value)}>{GUIDE_FABS.map(value => <option key={value}>{value}</option>)}</select></label>
      <p className="guide-note">답변의 관측 시각과 집계 기준을 함께 확인해 주세요. FAB을 바꾸면 첫 질문부터 시작하세요.</p>
      <div className="guide-choices" aria-label="질문 흐름">{QUESTION_FLOWS.map(item => <button type="button" aria-pressed={item.id === flowId} key={item.id} onClick={() => setFlowId(item.id)}>{item.title}</button>)}</div>
      <h3>{flow.title}</h3><p className="guide-description">{flow.description}</p>
      <ol className="guide-steps">{flow.steps.map((step, index) => <li key={step.prompt}>
        <div className="guide-step-heading"><span>{index + 1}</span><strong>{step.type}</strong></div>
        <p>{guideQuestion(step, fab)}</p>
        {step.followsPrevious && <small>같은 대화에서 바로 위 질문의 답변을 받은 뒤 사용해요.</small>}
        <button className="secondary-button" type="button" onClick={() => onSelect(guideQuestion(step, fab), { fab, flowId })}>입력창에 넣기<Icon name="chevron" size={15}/></button>
      </li>)}</ol>
    </div>
  </Drawer>;
}
