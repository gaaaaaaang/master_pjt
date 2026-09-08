import { test } from 'node:test';
import assert from 'node:assert/strict';
import { answerBlocks, tableCells } from '../src/answer-blocks.js';
test('Markdown tables retain Korean values, escapes, alignment, and following prose', () => {
  const blocks = answerBlocks('| 공정 | 건수 |\n| :--- | ---: |\n| Dry\\|Etch | **32** |\n\n확인해 주세요');
  assert.deepEqual(blocks[0].rows, [['Dry|Etch', '**32**']]);
  assert.deepEqual(blocks[0].align, ['left', 'right']);
  assert.equal(blocks[1].text, '확인해 주세요');
  assert.deepEqual(tableCells('a | b'), ['a', 'b']);
});
test('Malformed table rows are retained as text', () => {
  const blocks = answerBlocks('| A | B |\n| --- | --- |\n| 1 | 2 | 3 |');
  assert.equal(blocks[1].text, '| 1 | 2 | 3 |');
});
test('Numbered lists preserve starting number and separate bullet lists', () => {
  const blocks = answerBlocks('3. 먼저 확인\n4. 다음 확인\n- 참고\n- 문서');
  assert.equal(blocks[0].start, 3); assert.equal(blocks[0].items.length, 2);
  assert.equal(blocks[1].ordered, false);
});
test('Code fences and HTML remain literal strings', () => {
  const blocks = answerBlocks('```sql\nSELECT a | b;\n```\n<script>alert(1)</script>');
  assert.deepEqual(blocks[0], { type: 'code', text: 'SELECT a | b;' });
  assert.equal(blocks[1].text, '<script>alert(1)</script>');
});
