// Thin labels only; every original data point remains in the chart/table.
export function showTick(index, length, limit = 6) {
  if (length <= limit) return true;
  if (index === length - 1) return true;
  return index % Math.ceil((length - 1) / (limit - 1)) === 0;
}
export function compactLabel(value, limit = 12) {
  const text = String(value);
  const date = text.match(/^\d{4}-(\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/);
  if (date) return date[2] && date[2] !== '00:00' ? `${date[1]} ${date[2]}` : text.slice(0, 10);
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}

// Presentation only: CSV exports, source tables and hover values retain precision.
export function formatChartNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 }).format(number);
}

const metricLabels = {
  observed_at: '관측일 · KST', interval_end: '관측 시각 · KST', area: '공정 영역',
  comparison_period: '비교 기간 · KST', wip_lots: 'WIP (LOT)', queue_lots: '대기 LOT',
  avg_queue_minutes: '평균 대기 시간 (분)', avg_cycle_hours: '평균 Cycle Time (시간)',
  yield_percent: '수율 (%)', utilization_percent: '가동률 (%)', lot_completions: '완료 LOT',
  lot_starts: '투입 LOT', down_minutes: '비가동 시간 (분)', pm_minutes: '정비 시간 (분)',
  bottleneck_score: '병목 점수', temperature_c: '온도 (°C)', humidity_percent: '습도 (%)', defect_ppm: '불량 (ppm)',
  interval_start: '구간 시작 · KST', first_observed_at: '첫 관측 · KST', last_observed_at: '마지막 관측 · KST',
  observation_count: '관측 구간 수', observation_minutes_min: '최소 관측 길이 (분)', observation_minutes_max: '최대 관측 길이 (분)',
  period_label: '조회 기간', number_of_tools: '설비 수 (대)', sum_number_of_tools: '설비 수 합계 (대)',
  total_tools: '설비 수 합계 (대)', total_number_of_tools: '설비 수 합계 (대)',
};
export function metricLabel(value) {
  if (!value) return '';
  return String(value).split(' / ').map(part => metricLabels[part] || part).join(' / ');
}

export const seriesColors = ['#2563eb', '#8b5cf6', '#059669', '#d97706', '#db2777', '#0891b2', '#64748b', '#9333ea'];

// WIP counts and queue-time minutes do not share a meaningful vertical scale.
export function metricChartFacets(spec) {
  if (spec.type !== 'line' || spec.encoding?.y?.field !== 'value') return [];
  const metrics = [...new Set((spec.rows || []).map(row => row.metric))];
  if (metrics.length < 2 || metrics.some(metric => typeof metric !== 'string')) return [];
  return metrics.map(metric => {
    const rows = spec.rows.filter(row => row.metric === metric);
    const values = rows.map(row => Number(row.value));
    let min = Math.min(...values); let max = Math.max(...values);
    if (min === max) { const padding = Math.max(Math.abs(min) * 0.01, 1); min -= padding; max += padding; }
    let color = spec.encoding.color?.field === 'metric' ? undefined : spec.encoding.color;
    if (color?.field === 'series_key' && rows.every(row => typeof row.area === 'string')) {
      color = { field: 'area', title: '공정 영역' };
    }
    const seriesNames = new Map(rows.map(row => [row.series_key, row.area]));
    const imputedPoints = (spec.imputed_points || []).filter(point =>
      color?.field !== 'area' || seriesNames.has(String(point.series))).map(point =>
      color?.field === 'area' ? { ...point, series: seriesNames.get(String(point.series)) } : point);
    return { ...spec, title: metricLabel(metric), rows,
      series: color ? spec.series : null,
      imputed_points: imputedPoints,
      encoding: { ...spec.encoding, color, y: { ...spec.encoding.y, title: metric, domain: [min, max] } } };
  });
}
