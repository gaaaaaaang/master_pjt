import { test } from 'node:test';
import assert from 'node:assert/strict';
import { consumeSse, parseSseBlock, buildPayload, toCsv, resultStatus, requestForAttempt, restoreMessages } from '../src/chat-model.js';
function response(chunks) { return new Response(new ReadableStream({ start(controller) { for (const c of chunks) controller.enqueue(typeof c === 'string' ? new TextEncoder().encode(c) : c); controller.close(); } }), { headers: { 'Content-Type': 'text/event-stream' } }); }
test('SSE handles UTF-8 split across bytes and CRLF split across chunks', async () => {
  const text = 'event: trace\r\ndata: {"type":"node_completed","message":"한글"}\r\n\r\nevent: final\r\ndata: {"type":"run_completed","data":{"answer":"완료"}}\r\n\r\n';
  const bytes = new TextEncoder().encode(text);
  const events = [];
  await consumeSse(response([...bytes].map(byte => Uint8Array.of(byte))), e => events.push(e));
  assert.equal(events.length, 2); assert.equal(events[0].message, '한글'); assert.equal(events[1].data.answer, '완료');
});
test('SSE supports multiline data and comments', () => { assert.equal(parseSseBlock(': heartbeat'), null); assert.deepEqual(parseSseBlock('data: {\ndata: "type": "run_completed"\ndata: }'), { type: 'run_completed' }); });
test('SSE flushes final event without trailing separator', async () => { let result; await consumeSse(response(['data: {"type":"run_completed"}']), e => result = e); assert.equal(result.type, 'run_completed'); });
test('SSE rejects truncated streams instead of showing success', async () => { await assert.rejects(consumeSse(response(['data: {"type":"node_completed"}\n\n']), () => {}), /연결이 끊겼어요/); });
test('SSE surfaces server failure and cancellation', async () => { for (const type of ['run_failed', 'run_cancelled']) await assert.rejects(consumeSse(response([`data: {"type":"${type}"}\n\n`]), () => {})); });
test('SSE rejects invalid payloads and HTTP errors', async () => { assert.throws(() => parseSseBlock('data: null')); await assert.rejects(consumeSse(new Response('', { status: 503 }), () => {}), /503/); });
test('Payload trims explicit context and preserves conversation identity', () => { assert.deepEqual(buildPayload('질문', { fab: ' fab10 ', line: '', process: '  ' }, 'session-1'), { message: '질문', fab: 'fab10', conversation_id: 'session-1' }); });
test('CSV escapes formula strings, commas, quotes, preserves numeric negatives', () => { const csv = toCsv([{ name: '=SUM(A1)', count: -5, other: 'a,"b"' }]); assert.ok(csv.startsWith('\uFEFF')); assert.ok(csv.includes('"\'=SUM(A1)"')); assert.ok(csv.includes('"-5"')); assert.ok(csv.includes('"a,""b"""')); });
test('Non-success statuses never claim analysis success', () => { assert.equal(resultStatus('needs_clarification').tone, 'warning'); assert.equal(resultStatus('data_unavailable').tone, 'warning'); assert.equal(resultStatus('human_review').tone, 'warning'); });

test('Retry keeps original scope and conversation despite changed settings', () => {
  const first = requestForAttempt('목록', { fab: 'fab10' }, 'original');
  const retry = requestForAttempt('목록', { fab: 'fab20' }, 'different', { requestPayload: first });
  assert.deepEqual(retry, { message: '목록', fab: 'fab10', conversation_id: 'original' });
  assert.notEqual(first, retry);
});
test('Restored sessions release interrupted stream and feedback controls', () => {
  const messages = restoreMessages([{ status: 'streaming' }, { status: 'done', feedback: 'pending' }, { status: 'done', feedback: 'helpful' }]);
  assert.equal(messages[0].status, 'cancelled');
  assert.equal(messages[1].feedback, null);
  assert.equal(messages[2].feedback, 'helpful');
});
