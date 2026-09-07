import React, { useEffect, useMemo, useRef, useState } from 'react';
import { cellText, tableView } from './table-model';
import { answerBlocks } from './answer-blocks';
import { agentLabel, resultStatus, rowsFromResult, toCsv } from './chat-model';

const paths = {
  plus: 'M12 5v14M5 12h14', search: 'm21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  chat: 'M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z',
  arrow: 'M12 19V5m-6 6 6-6 6 6', chevron: 'm9 5 7 7-7 7', down: 'm6 9 6 6 6-6', close: 'm6 6 12 12M6 18 18 6',
  settings: 'M4 7h16M4 17h16M8 4v6m8 4v6', chart: 'M4 3v17h17M8 15l4-5 4 2 5-7',
  layers: 'm12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',
  book: 'M12 6v15M3 3h5a4 4 0 0 1 4 3 4 4 0 0 1 4-3h5v15h-5a4 4 0 0 0-4 3 4 4 0 0 0-4-3H3V3Z',
  check: 'm5 12 4 4L19 6', clock: 'M12 8v4l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  copy: 'M9 9h12v12H9zM5 15H3V3h12v2', download: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
  menu: 'M4 6h16M4 12h16M4 18h16', spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
  stop: 'M6 6h12v12H6z', info: 'M12 11v6m0-10v.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  like: 'M7 10v11H3V10h4Zm0 0 5-8c2 0 3 2 2 5l-1 3h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 2H7',
  refresh: 'M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M4 12a8 8 0 0 0 14 6',
};
export function Icon({ name, size = 20, ...props }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name] || paths.spark}/></svg>;
}
export function Brand({ small = false }) { return <span className={`brand-mark ${small ? 'small' : ''}`} aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M9 24V8h15v5H14v3h8v5h-8v3H9Z" fill="currentColor"/></svg></span>; }
export function Chip({ status }) { const { label, tone } = resultStatus(status); return <span className={`status-chip ${tone}`}><span/>{label}</span>; }
export function CopyButton({ text, label = '복사', onNotice }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => { if (copied) { const timer = setTimeout(() => setCopied(false), 1800); return () => clearTimeout(timer); } }, [copied]);
  return <button className="text-button" type="button" onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied(true); } catch { onNotice?.('복사하지 못했어요. 텍스트를 선택해 복사해 주세요.'); } }}><Icon name={copied ? 'check' : 'copy'} size={16}/>{copied ? '복사했어요' : label}</button>;
}
function inline(text) { return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => part.startsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part.startsWith('`') ? <code key={i}>{part.slice(1, -1)}</code> : part); }
export function AnswerText({ text = '' }) {
  return <div className="answer-text">{answerBlocks(text).map((block, i) => {
    if (block.type === 'code') return <pre key={i}>{block.text}</pre>;
    if (block.type === 'heading') return <h3 key={i}>{inline(block.text)}</h3>;
    if (block.type === 'table') return <div className="data-table answer-table" role="region" tabIndex={0} aria-label="답변 표" key={i}><table><thead><tr>{block.headings.map((cell, j) => <th scope="col" key={j} style={{ textAlign: block.align[j] }}>{inline(cell)}</th>)}</tr></thead><tbody>{block.rows.map((row, j) => <tr key={j}>{row.map((cell, k) => <td key={k} style={{ textAlign: block.align[k] }}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>;
    if (block.type === 'list') { const Tag = block.ordered ? 'ol' : 'ul'; return <Tag key={i} start={block.start}>{block.items.map((item, j) => <li key={j}>{inline(item)}</li>)}</Tag>; }
    return <p key={i}>{inline(block.text)}</p>;
  })}</div>;
}
export function DataTable({ rows }) {
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState(null);
  const fields = useMemo(() => [...new Set(rows.flatMap(row => Object.keys(row)))], [rows]);
  const filtered = useMemo(() => tableView(rows, query, sort), [rows, query, sort]);
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  const currentPage = Math.min(page, pages - 1);
  function changeSort(key) {
    setSort(current => current?.key === key
      ? current.direction === 'ascending' ? { key, direction: 'descending' } : null
      : { key, direction: 'ascending' });
    setPage(0);
  }
  return <section className="table-explorer" aria-label="데이터 탐색">
    {rows.length > 20 && <div className="table-controls"><label><Icon name="search" size={16}/><input aria-label="조회 데이터 검색" placeholder="조회 결과에서 검색" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }}/></label><span role="status">{filtered.length.toLocaleString()} / {rows.length.toLocaleString()}행</span></div>}
    <div className="data-table" tabIndex={0} role="region" aria-label="조회 결과 표"><table><thead><tr>{fields.map(key => <th scope="col" key={key} aria-sort={sort?.key === key ? sort.direction : 'none'}><button type="button" onClick={() => changeSort(key)} aria-label={`${key} 정렬 변경`}>{key}<span aria-hidden="true">{sort?.key === key ? sort.direction === 'ascending' ? '↑' : '↓' : '↕'}</span></button></th>)}</tr></thead><tbody>{filtered.slice(currentPage * 20, currentPage * 20 + 20).map((row, i) => <tr key={i}>{fields.map(key => <td key={key} title={cellText(row[key])}>{row[key] == null ? '—' : cellText(row[key])}</td>)}</tr>)}</tbody></table>{!filtered.length && <p className="table-empty">일치하는 결과가 없어요. 다른 검색어를 입력해 주세요.</p>}</div>
    {(rows.length > 20 || sort) && <div className="pagination"><span className="table-order">{sort ? `${sort.key} ${sort.direction === 'ascending' ? '오름차순' : '내림차순'}` : '원본 순서'}</span><button type="button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>이전</button><span>{currentPage + 1} / {pages}</span><button type="button" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>다음</button></div>}
  </section>;
}
export function downloadRows(rows) {
  const url = URL.createObjectURL(new Blob([toCsv(rows)], { type: 'text/csv;charset=utf-8;' }));
  const link = document.createElement('a');
  link.href = url; link.download = 'fab-analysis.csv'; link.hidden = true;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
export function Drawer({ title, subtitle, children, onClose, wide = false }) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    dialog.showModal();
    return () => { dialog.close(); if (previous?.isConnected) previous.focus(); };
  }, []);
  return <dialog ref={ref} className={`drawer ${wide ? 'wide' : ''}`} onCancel={onClose} onClick={e => { if (e.target === e.currentTarget && (e.clientX < e.currentTarget.getBoundingClientRect().left)) onClose(); }} aria-labelledby="drawer-title"><header><div><p className="overline">FAB ASSISTANT</p><h2 id="drawer-title">{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button" onClick={onClose} aria-label="패널 닫기" autoFocus><Icon name="close"/></button></header>{children}</dialog>;
}
export function Inspector({ message, initialTab = 'trace', onClose, onNotice }) {
  const [tab, setTab] = useState(initialTab);
  const result = message.result || {};
  const events = (message.events || []).filter(event => !['run_started', 'run_completed'].includes(event.type));
  const rows = rowsFromResult(result, events);
  const agents = [...new Set(events.map(event => event.node))];
  return <Drawer title="분석 상세" subtitle="답변에 사용한 근거와 실행 과정을 확인해요." onClose={onClose} wide>
    <div className="inspector-question">{message.question}</div>
    <div className="tab-list" role="tablist" aria-label="분석 상세 구분">{[['trace', '실행 과정'], ['evidence', '근거'], ['data', '데이터 · SQL']].map(([key, label], i) => <button id={`tab-${key}`} role="tab" aria-controls="inspector-panel" tabIndex={tab === key ? 0 : -1} aria-selected={tab === key} key={key} onClick={() => setTab(key)} onKeyDown={e => { const keys = ['trace', 'evidence', 'data']; let next; if (e.key === 'ArrowRight') next = keys[(i + 1) % 3]; if (e.key === 'ArrowLeft') next = keys[(i + 2) % 3]; if (e.key === 'Home') next = keys[0]; if (e.key === 'End') next = keys[2]; if (next) { e.preventDefault(); setTab(next); document.getElementById(`tab-${next}`).focus(); } }}>{label}</button>)}</div>
    <div className="inspector-body" role="tabpanel" id="inspector-panel" aria-labelledby={`tab-${tab}`}>
      {tab === 'trace' && <><div className="section-heading"><h3>{agents.length}개 에이전트의 실행 기록</h3><Chip status={result.status || message.status}/></div><p className="muted small-text">각 단계를 열면 실제 이벤트와 반환 데이터를 볼 수 있어요.</p>{!events.length && <p className="empty-panel">아직 수신한 실행 기록이 없어요.</p>}
        <div className="timeline">{agents.map((agent, i) => { const items = events.filter(event => event.node === agent); return <details className="agent-step" key={agent}><summary><span className="step-number">{String(i + 1).padStart(2, '0')}</span><span><strong>{agentLabel(agent)}</strong><small>{agent} · 이벤트 {items.length}개</small><span className="agent-summary" title={items.at(-1)?.message}>{items.at(-1)?.message || "반환된 요약이 없어요."}</span></span><Icon name="down" size={16}/></summary><div className="agent-events">{items.map((event, j) => <article key={j}><span className="event-type">{event.type}</span><p>{event.message}</p>{event.data?.reasoning?.summary && <p className="muted">{event.data.reasoning.summary}</p>}<details className="raw-details"><summary>반환 데이터</summary><pre>{JSON.stringify(event.data || {}, null, 2)}</pre></details></article>)}</div></details>; })}</div>
        {result.plan && <details className="raw-details"><summary>전체 실행 계획</summary><pre>{JSON.stringify(result.plan, null, 2)}</pre></details>}
        {Object.keys(result).length > 0 && <details className="raw-details"><summary>최종 응답 원본</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
      </>}
      {tab === 'evidence' && <><h3>답변의 근거</h3>{!(result.evidence?.length) && <p className="empty-panel">이 답변에 첨부된 근거가 없어요.</p>}{(result.evidence || []).map((item, i) => <article className="evidence-card" key={i}><span className="source-kind">{item.source_type}</span><h4>{item.title}</h4><AnswerText text={item.content}/>{Object.keys(item.metadata || {}).length > 0 && <details className="raw-details"><summary>출처 상세</summary><pre>{JSON.stringify(item.metadata, null, 2)}</pre></details>}</article>)}{result.limitations?.length > 0 && <div className="limitations"><h4>해석할 때 확인해 주세요</h4><ul>{result.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></div>}</>}
      {tab === 'data' && <><div className="section-heading"><h3>조회 데이터 <span className="count">{rows.length}행</span></h3>{rows.length > 0 && <button className="text-button" onClick={() => downloadRows(rows)}><Icon name="download" size={16}/>전체 CSV 저장</button>}</div>{rows.length ? <><p className="muted small-text">서버가 반환한 차트 데이터 또는 조회 샘플이에요.</p><div className="csv-copy"><CopyButton text={toCsv(rows).replace(/^\uFEFF/, "")} label="전체 CSV 복사" onNotice={onNotice}/><span>파일 저장이 어려울 때 복사해서 사용할 수 있어요.</span></div><DataTable rows={rows}/></> : <p className="empty-panel">반환된 데이터가 없어요.</p>}{result.sql && <section className="sql-section"><div className="section-heading"><h3>실행 SQL</h3><CopyButton text={result.sql} onNotice={onNotice}/></div><pre>{result.sql}</pre></section>}</>}
    </div>
  </Drawer>;
}
