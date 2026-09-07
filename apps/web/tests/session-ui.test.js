import { test } from 'node:test';
import assert from 'node:assert/strict';
import { restoreWorkspaceUi } from '../src/session-ui.js';
test('Restores active conversation and its draft while discarding orphan drafts', () => {
  const restored = restoreWorkspaceUi(JSON.stringify({ activeId:'old',drafts:{ new:'다음 질문',old:'기존 질문',deleted:'삭제된 대화' } }), ['new','old']);
  assert.deepEqual(restored,{activeId:'old',drafts:{new:'다음 질문',old:'기존 질문'}});
});
test('Corrupt storage and missing active ids recover to available conversation', () => {
  for (const raw of ['{','null','[]','12']) assert.deepEqual(restoreWorkspaceUi(raw,['a']),{activeId:'a',drafts:{}});
  assert.deepEqual(restoreWorkspaceUi('{"activeId":"deleted","drafts":{"a":42}}',['a']),{activeId:'a',drafts:{}});
});
test('Restored drafts respect input length limit', () => {
  const restored=restoreWorkspaceUi(JSON.stringify({drafts:{a:'가'.repeat(9000)}}),['a']);
  assert.equal(restored.drafts.a.length,8000);
});
