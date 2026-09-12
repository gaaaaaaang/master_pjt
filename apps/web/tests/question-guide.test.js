import test from 'node:test';
import assert from 'node:assert/strict';
import { GUIDE_FABS, QUESTION_FLOWS, guideQuestion } from '../src/question-guide.js';

test('guide spans the seven planned question types and retains follow-up order', () => {
  assert.deepEqual(new Set(QUESTION_FLOWS.flatMap(flow => flow.steps.map(step => step.type))),
    new Set(['현황', '진단', '추세·비교', '영향 추정', '대응 절차', '공정 지식', '후속 질문']));
  for (const flow of QUESTION_FLOWS) {
    assert.equal(!!flow.steps[0].followsPrevious, false);
    for (const fab of GUIDE_FABS) {
      for (const step of flow.steps) assert.ok(!guideQuestion(step, fab).includes('{fab}'));
      if (flow.id !== 'knowledge') assert.ok(guideQuestion(flow.steps[0], fab).startsWith(fab));
    }
  }
});

test('guide rejects an unknown FAB instead of silently substituting one', () => {
  const step = QUESTION_FLOWS[0].steps[0];
  assert.equal(guideQuestion(step, ' fab12 '), 'FAB12 전체의 현재 WIP은 몇 개야?');
  assert.throws(() => guideQuestion(step, 'FAB99'));
});
