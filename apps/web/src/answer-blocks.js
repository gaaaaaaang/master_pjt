// A deliberately small, plain-text Markdown subset. No raw HTML execution.
export function tableCells(line) {
  let text = line.trim().replace(/^\|/, '').replace(/(?<!\\)\|$/, '');
  return text.split(/(?<!\\)\|/).map(cell => cell.trim().replaceAll('\\|', '|'));
}
function tableRule(line = '') {
  const cells = tableCells(line);
  return cells.length > 1 && cells.every(cell => /^:?-{3,}:?$/.test(cell));
}
export function answerBlocks(text = '') {
  const lines = String(text).split(/\r?\n/);
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const code = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      blocks.push({ type: 'code', text: code.join('\n') });
    } else if (line.includes('|') && tableRule(lines[i + 1])) {
      const headings = tableCells(line);
      const align = tableCells(lines[++i]).map(cell => cell.endsWith(':') ? cell.startsWith(':') ? 'center' : 'right' : 'left');
      const rows = [];
      while (i + 1 < lines.length && lines[i + 1].trim() && lines[i + 1].includes('|')) {
        const cells = tableCells(lines[i + 1]);
        // Preserve malformed rows as ordinary text instead of silently losing cells.
        if (cells.length !== headings.length) break;
        rows.push(cells); i++;
      }
      blocks.push({ type: 'table', headings, align, rows });
    } else if (/^#{1,6}\s/.test(line)) {
      blocks.push({ type: 'heading', text: line.replace(/^#+\s/, '') });
    } else if (/^\s*(?:[-*]\s|\d+[.)]\s)/.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const pattern = ordered ? /^\s*\d+[.)]\s/ : /^\s*[-*]\s/;
      const items = [line.replace(pattern, '')];
      while (i + 1 < lines.length && pattern.test(lines[i + 1])) items.push(lines[++i].replace(pattern, ''));
      blocks.push({ type: 'list', ordered, start: ordered ? Number(line.match(/\d+/)[0]) : undefined, items });
    } else if (line.trim()) blocks.push({ type: 'paragraph', text: line });
  }
  return blocks;
}
