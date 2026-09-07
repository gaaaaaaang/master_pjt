import { test } from 'node:test';
import assert from 'node:assert/strict';
import { answerReport } from '../src/answer-report.js';
test('Portable answer retains status, question, evidence, limitations and explicit scope', () => {
  const report = answerReport({question:'현재 WIP?',requestPayload:{fab:'fab10',conversation_id:'internal'},result:{status:'data_unavailable',answer:'현재 데이터 없음',evidence:[{title:'모델 데이터',source_type:'simulation',content:'계획값'}],limitations:['실시간 아님','실시간 아님'],sql:'SELECT internal'}});
  for (const expected of ['데이터 확인 필요','현재 WIP?','현재 데이터 없음','fab10','모델 데이터','simulation','계획값','실시간 아님']) assert.ok(report.includes(expected));
  assert.equal(report.match(/실시간 아님/g).length,1); assert.ok(!report.includes('SELECT internal')); assert.ok(!report.includes('conversation_id'));
});
test('Report makes absence of evidence explicit', () => {
  assert.ok(answerReport({result:{status:'needs_clarification',answer:'날짜 기준을 알려주세요'}}).includes('별도 첨부된 근거 없음'));
});
