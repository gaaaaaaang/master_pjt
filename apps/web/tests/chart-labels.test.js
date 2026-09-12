import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compactLabel, showTick, metricChartFacets } from '../src/chart-labels.js';
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

test('Different metric units use independent panels without changing plotted values', () => {
  const rows = [
    { day: '2026-09-10', metric: 'wip_lots', value: 200 },
    { day: '2026-09-10', metric: 'avg_queue_minutes', value: 12.3 },
    { day: '2026-09-11', metric: 'wip_lots', value: 210 },
    { day: '2026-09-11', metric: 'avg_queue_minutes', value: 15.2 },
  ];
  const spec = { type: 'line', rows, encoding: { x: { field: 'day' }, y: { field: 'value', domain: [0, 210] }, color: { field: 'metric' } } };
  const facets = metricChartFacets(spec);
  assert.equal(facets.length, 2);
  assert.deepEqual(facets[0].encoding.y.domain, [200, 210]);
  assert.deepEqual(facets[1].rows.map(row => row.value), [12.3, 15.2]);
  assert.equal(facets[1].encoding.color, undefined);
  assert.deepEqual(metricChartFacets(facets[0]), []);
  assert.deepEqual(spec.encoding.y.domain, [0, 210]);
});

test('Area legends remain meaningful within each metric panel', () => {
  const rows = ['etch', 'photo'].flatMap(area => ['wip_lots', 'avg_queue_minutes'].map(metric => ({
    observed_at: '2026-09-11', area, metric, value: 10, series_key: `${area} / ${metric}`,
  })));
  const spec = { type: 'line', rows, encoding: { x: { field: 'observed_at' }, y: { field: 'value' }, color: { field: 'series_key' } },
    imputed_points: [{ x: '2026-09-11', series: 'etch / wip_lots' }] };
  const facets = metricChartFacets(spec);
  assert.deepEqual(facets[0].encoding.color, { field: 'area', title: '공정 영역' });
  assert.deepEqual(facets[0].imputed_points, [{ x: '2026-09-11', series: 'etch' }]);
  assert.deepEqual(facets[1].imputed_points, []);
});
