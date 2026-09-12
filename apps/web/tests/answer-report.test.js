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
test('Portable report uses grounded FAB and process instead of unchanged sidebar defaults', () => {
  const report = answerReport({question:'가동률은?',requestPayload:{fab:'fab11',process:'photo'},result:{
    status:'succeeded',answer:'확인했습니다.',plan:{slots:{fab_id:{value:'fab12'},area:{value:'etch'}}},
  }});
  assert.ok(report.includes('fab: fab12'));
  assert.ok(report.includes('process: etch'));
  assert.ok(!report.includes('fab11'));
  assert.ok(!report.includes('photo'));
});
test('SQL scope preserves multiple areas and does not resurrect an omitted sidebar filter', () => {
  const report = answerReport({requestPayload:{fab:'fab11',line:'old-line'},result:{
    status:'succeeded',evidence:[{source_type:'text2sql_plan',metadata:{query_plan:{fab_id:'fab13',slots:{areas:{value:'etch,photo'}}}}}],
  }});
  assert.ok(report.includes('fab: fab13'));
  assert.ok(report.includes('process: etch,photo'));
  assert.ok(!report.includes('old-line'));
});

test('Cross-FAB export preserves both scopes and source provenance separately', () => {
  const report = answerReport({ question: '두 FAB WIP 비교', result: {
    status: 'succeeded', answer: 'FAB11 188 LOT, FAB13 159 LOT.',
    evidence: [{ source_type: 'text2sql_plan', metadata: { query_plan: { fab_id: 'fab11', slots: { fab_ids: { value: 'fab11,fab13' } } } } }],
    data_sources: [{ label: '공정 데이터 출처', description: 'PoC용 생성 데이터' }],
  } });
  assert.ok(report.includes('fab: fab11,fab13'));
  assert.ok(report.includes('## 데이터 출처\n\n- 공정 데이터 출처: PoC용 생성 데이터'));
});
