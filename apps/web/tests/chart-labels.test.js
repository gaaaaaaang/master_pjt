import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compactLabel, showTick } from '../src/chart-labels.js';
test('Dense axes preserve both endpoints without too many labels', () => {
  for (const length of [7, 8, 12, 31, 366]) {
    const ticks = Array.from({ length }, (_, i) => i).filter(i => showTick(i, length));
    assert.equal(ticks[0], 0); assert.equal(ticks.at(-1), length - 1); assert.ok(ticks.length <= 6);
  }
});
test('Short axes and labels remain intact', () => {
  assert.equal(showTick(2, 4), true); assert.equal(compactLabel('2020-01-01'), '2020-01-01');
  assert.equal(compactLabel('Dry_Etch_long_toolgroup_name').length, 12);
});
