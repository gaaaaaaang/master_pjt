export function cellText(value) {
  return value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
}
const collator = new Intl.Collator('ko', { numeric: true, sensitivity: 'base' });
function numeric(value) {
  return (typeof value === 'number' || typeof value === 'string' && value.trim() !== '') && Number.isFinite(Number(value));
}
export function tableView(rows, query = '', sort = null) {
  const needle = query.trim().toLocaleLowerCase();
  const filtered = rows.filter(row => !needle || Object.values(row).some(value => cellText(value).toLocaleLowerCase().includes(needle)));
  if (!sort) return filtered;
  return filtered.sort((a, b) => {
    const av = a[sort.key], bv = b[sort.key];
    // Missing values stay last regardless of direction, and never become zero.
    const aMissing = av == null || av === '', bMissing = bv == null || bv === '';
    if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1;
    const comparison = numeric(av) && numeric(bv) ? Number(av) - Number(bv) : collator.compare(cellText(av), cellText(bv));
    return comparison * (sort.direction === 'descending' ? -1 : 1);
  });
}
