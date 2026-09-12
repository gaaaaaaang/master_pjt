import test from 'node:test';
import assert from 'node:assert/strict';
import { compactConversations, mergeConversations, readLegacy, saveConversations, loadConversations } from '../src/workspace-storage.js';

test('durable fallback migrates old tab storage including server conversation IDs', async () => {
  const local = new Map(); const session = new Map();
  const storage = map => ({ getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, value) });
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage(local) });
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: storage(session) });
  const conversations = [{ id: 'local-id', backendId: 'server-id', title: 'FAB13', messages: [{ role: 'assistant', result: { answer: '159 LOT', chart: { rows: [159] }, conversation_history: ['duplicate'] } }] }];
  session.set('fab-workspace-v1', JSON.stringify(conversations));
  assert.deepEqual(await loadConversations(), conversations);
  await saveConversations(conversations);
  session.clear();
  assert.equal((await loadConversations())[0].backendId, 'server-id');
  assert.equal((await loadConversations())[0].messages[0].result.answer, '159 LOT');
  assert.equal((await loadConversations())[0].messages[0].result.conversation_history, undefined);
  assert.deepEqual(compactConversations(conversations)[0].messages[0].result.chart, { rows: [159] });
});

test('one tab cannot erase other conversations or overwrite their newer version', () => {
  const saved = [{ id: 'a', updatedAt: 20, messages: ['new'] }, { id: 'b', updatedAt: 10 }];
  const incoming = [{ id: 'a', updatedAt: 5, messages: ['stale'] }, { id: 'c', updatedAt: 30 }];
  const merged = mergeConversations(saved, incoming);
  assert.deepEqual(merged.map(item => item.id), ['c', 'a', 'b']);
  assert.deepEqual(merged.find(item => item.id === 'a').messages, ['new']);
});

test('blocked browser storage getter does not break startup', () => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('blocked'); } });
  assert.equal(readLegacy('missing'), null);
});
