// Thin labels only; every original data point remains in the chart/table.
export function showTick(index, length, limit = 6) {
  if (length <= limit) return true;
  if (index === length - 1) return true;
  return index % Math.ceil((length - 1) / (limit - 1)) === 0;
}
export function compactLabel(value, limit = 12) {
  const text = String(value);
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}
