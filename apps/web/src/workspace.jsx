import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Chart } from './charts';
import { Icon, Brand, Chip, CopyButton, AnswerText, DataTable, Drawer, Inspector, downloadRows } from './ui';
import { consumeSse, buildPayload, progressLabel, rowsFromResult } from './chat-model';
import { makePreview } from './preview';
import './workspace.css';

const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '');
const STORE_KEY = 'fab-workspace-v1';
const previewKey = new URLSearchParams(window.location.search).get('preview');
const isPreview = previewKey !== null;
const examples = [
  { icon: 'layers', title: '생산 현황', text: '지금 공정 상황이 궁금할 때', prompt: 'FAB10의 공정별 WIP 현황을 알려줘' },
  { icon: 'search', title: '원인 찾기', text: '지표가 달라진 이유를 찾을 때', prompt: 'FAB10 Dry_Etch 공정의 대기 시간이 늘어난 원인을 분석해 줘' },
  { icon: 'chart', title: '추이 비교', text: '변화를 한눈에 보고 싶을 때', prompt: 'FAB10의 lotrelease 투입 계획을 start_date 기준으로 2020년 1월 주별로 비교해 줘' },
  { icon: 'book', title: '공정 지식', text: '관련 문서와 사례가 필요할 때', prompt: 'Dry_Etch 공정에서 대기 시간이 길어질 때 확인할 항목을 알려줘' },
];
function newConversation() { return { id: crypto.randomUUID(), title: '새 대화', context: { fab: '', line: '', process: '' }, messages: [] }; }
function initialState() {
  if (isPreview) return [makePreview(previewKey)];
  try { const saved = JSON.parse(sessionStorage.getItem(STORE_KEY)); if (Array.isArray(saved) && saved.length && saved.every(c => typeof c.id === 'string' && typeof c.title === 'string' && Array.isArray(c.messages))) return saved.map(c => ({ ...c, context: { fab: '', line: '', process: '', ...c.context }, messages: c.messages.map(m => m.status === 'streaming' ? { ...m, status: 'cancelled' } : m) })); } catch { /* A new session remains usable if browser storage is unavailable. */ }
  return [newConversation()];
}
function Workspace() {
  const [conversations, setConversations] = useState(initialState);
  const [activeId, setActiveId] = useState(conversations[0].id);
  const [draft, setDraft] = useState('');
  const [search, setSearch] = useState('');
  const [drawer, setDrawer] = useState(null);
  const [sidebar, setSidebar] = useState(false);
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width:760px)').matches);
  const sidebarRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [connection, setConnection] = useState('checking');
  const abortRef = useRef(null);
  const textarea = useRef(null);
  const scrollRef = useRef(null);
  const shouldFollow = useRef(false);
  const wasStreaming = useRef(false);
  const active = conversations.find(c => c.id === activeId) || conversations[0];
  useEffect(() => { const media = window.matchMedia('(max-width:760px)'); const change = () => setMobile(media.matches); media.addEventListener('change', change); return () => media.removeEventListener('change', change); }, []);
  useEffect(() => {
    if (!sidebar || !mobile) return;
    const previous = document.activeElement;
    const panel = sidebarRef.current;
    const nodes = () => [...panel.querySelectorAll('button:not(:disabled), a, input')];
    nodes()[0]?.focus();
    const key = e => {
      if (e.key === 'Escape') { e.preventDefault(); setSidebar(false); }
      if (e.key === 'Tab') { const items = nodes(); const first = items[0]; const last = items.at(-1); if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); } }
    };
    panel.addEventListener('keydown', key);
    return () => { panel.removeEventListener('keydown', key); if (previous?.isConnected) previous.focus(); };
  }, [sidebar, mobile]);
  const selectedMessage = drawer?.messageId && active.messages.find(m => m.id === drawer.messageId);
  const artifacts = active.messages.filter(m => m.result?.chart || m.result?.sql || rowsFromResult(m.result, m.events).length);
  useEffect(() => { if (!isPreview) { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(conversations)); } catch { setNotice('브라우저 저장 공간이 부족해요. 이 대화는 새로고침하면 사라질 수 있어요.'); } } }, [conversations]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 5000); return () => clearTimeout(timer); }, [notice]);
  useEffect(() => {
    if (isPreview) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    const health = API_BASE === '/api' ? '/health' : API_BASE.replace(/\/api$/, '') + '/health';
    fetch(health, { signal: controller.signal }).then(r => setConnection(r.ok ? 'online' : 'offline')).catch(() => setConnection('offline')).finally(() => clearTimeout(timeout));
    return () => { clearTimeout(timeout); controller.abort(); };
  }, []);
  useEffect(() => {
    const scroller = scrollRef.current;
    const last = active.messages.at(-1);
    if (shouldFollow.current && scroller) {
      if (wasStreaming.current && last?.status !== 'streaming') {
        const answer = scroller.querySelector('.assistant-message:last-child');
        if (answer) scroller.scrollTop += answer.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 22;
      } else scroller.scrollTop = scroller.scrollHeight;
    }
    wasStreaming.current = last?.status === 'streaming';
  }, [active.messages]);
  useEffect(() => { const handler = e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); textarea.current?.focus(); } }; window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler); }, []);
  useEffect(() => () => abortRef.current?.abort(), []);
  function patchConversation(id, update) { setConversations(current => current.map(c => c.id === id ? update(c) : c)); }
  function patchMessage(conversation, id, patch) { patchConversation(conversation, c => ({ ...c, messages: c.messages.map(m => m.id === id ? { ...m, ...patch } : m) })); }
  function startNew() { if (busy) return; if (isPreview) { window.location.href = '/'; return; } const next = newConversation(); setConversations(current => [next, ...current]); setActiveId(next.id); setDraft(''); setDrawer(null); setSidebar(false); shouldFollow.current = true; textarea.current?.focus(); }
  function choose(id) { if (busy) return; setActiveId(id); setDraft(''); setDrawer(null); setSidebar(false); shouldFollow.current = true; }
  async function send(text = draft, retryId = null) {
    if (busy || abortRef.current || !text.trim()) return;
    if (isPreview) { setNotice('여기는 예시 화면이에요. 실제 질문은 새 대화에서 시작해 주세요.'); return; }
    const outgoing = text.trim(); const conversation = active.id; const messageId = retryId || crypto.randomUUID();
    const controller = new AbortController(); abortRef.current = controller;
    setBusy(true); setDraft(''); setDrawer(null); shouldFollow.current = true;
    let events = []; let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 180000);
    patchConversation(conversation, c => ({ ...c, title: c.messages.length ? c.title : outgoing.slice(0, 48), messages: retryId ? c.messages.map(m => m.id === retryId ? { ...m, status: 'streaming', error: null, result: null, events: [] } : m) : [...c.messages, { id: crypto.randomUUID(), role: 'user', content: outgoing }, { id: messageId, role: 'assistant', question: outgoing, status: 'streaming', events: [] }] }));
    try {
      const response = await fetch(`${API_BASE}/chat/stream`, { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(buildPayload(outgoing, active.context, active.backendId)) });
      await consumeSse(response, event => {
        if (event.type === 'run_completed') {
          const result = event.data || {};
          patchConversation(conversation, c => ({ ...c, backendId: result.conversation_id || c.backendId, messages: c.messages.map(m => m.id === messageId ? { ...m, status: 'done', result, events } : m) }));
          setConnection('online');
        } else { events = [...events, event]; patchMessage(conversation, messageId, { events }); }
      });
    } catch (error) {
      const cancelled = controller.signal.aborted && !timedOut;
      patchMessage(conversation, messageId, { status: cancelled ? 'cancelled' : 'failed', events, error: cancelled ? '' : timedOut ? '응답 대기 시간이 길어졌어요. 질문 범위를 좁혀 다시 시도해 주세요.' : error instanceof TypeError ? '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.' : error.message });
    } finally { clearTimeout(timer); abortRef.current = null; setBusy(false); textarea.current?.focus(); }
  }
  async function feedback(message, helpful) {
    if (isPreview) { setNotice('예시 화면에서는 피드백을 전송하지 않아요.'); return; }
    if (!message.result?.conversation_id || message.feedback) return;
    const conversation = active.id;
    patchMessage(conversation, message.id, { feedback: 'pending' });
    try { const response = await fetch(`${API_BASE}/feedback`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conversation_id: message.result.conversation_id, helpful, trace_id: message.id }) }); if (!response.ok) throw new Error(); patchMessage(conversation, message.id, { feedback: helpful ? 'helpful' : 'unhelpful' }); setNotice('피드백을 남겼어요. 고마워요.'); }
    catch { patchMessage(conversation, message.id, { feedback: null }); setNotice('피드백을 저장하지 못했어요. 다시 시도해 주세요.'); }
  }
  function suggestion(prompt) { setDraft(prompt); textarea.current?.focus(); }
  return <div className="app-shell">
    <a className="skip-link" href="#question">질문 입력으로 바로 가기</a>
    {sidebar && <button className="sidebar-backdrop" aria-label="대화 목록 닫기" onClick={() => setSidebar(false)}/>}
    <aside ref={sidebarRef} inert={mobile && !sidebar} className={`sidebar ${sidebar ? 'is-open' : ''}`} aria-label="대화 탐색">
      <a className="brand" href="/"><Brand/><span>FAB <b>Assistant</b></span></a>
      <button className="new-chat" onClick={startNew} disabled={busy}><Icon name="plus" size={19}/>새 대화<span>＋</span></button>
      <label className="history-search"><Icon name="search" size={17}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="대화 검색" aria-label="대화 검색"/></label>
      <div className="history-title">최근 대화<span>{conversations.filter(c => c.messages.length).length}</span></div>
      <nav className="history-list" aria-label="최근 대화">
        {conversations.filter(c => c.title.toLowerCase().includes(search.toLowerCase())).map(c => <button type="button" disabled={busy} className={`history-item ${c.id === active.id ? 'selected' : ''}`} key={c.id} onClick={() => choose(c.id)} aria-current={c.id === active.id ? 'page' : undefined}><Icon name="chat" size={17}/><span>{c.title}</span></button>)}
        {!conversations.some(c => c.title.toLowerCase().includes(search.toLowerCase())) && <p className="search-empty">검색한 대화가 없어요.</p>}
      </nav>
      <div className="sidebar-footer"><div className="workspace-label"><span className="workspace-avatar">F</span><div><strong>FAB Workspace</strong><small>생산 운영 어시스턴트</small></div></div><p><span className={`connection-dot ${connection}`}/>{isPreview ? '디자인 미리보기' : connection === 'online' ? '서버에 연결되어 있어요' : connection === 'checking' ? '연결 확인 중' : '서버 연결 확인 필요'}</p></div>
    </aside>
    <main className="main-workspace" inert={mobile && sidebar}>
      <header className="topbar"><div className="topbar-title"><button className="icon-button mobile-menu" aria-label="대화 목록 열기" aria-expanded={sidebar} onClick={() => setSidebar(!sidebar)}><Icon name="menu"/></button><span className="topbar-product">어시스턴트</span><span className="topbar-divider">/</span><span className="conversation-title">{active.title}</span></div><button aria-label="분석 결과 모아보기" className="topbar-action" onClick={() => setDrawer({ type: 'artifacts' })}><Icon name="layers" size={18}/><span>분석 결과</span>{artifacts.length > 0 && <span className="count">{artifacts.length}</span>}</button></header>
      {isPreview && <div className="preview-banner"><span>디자인 미리보기 · 실제 운영 데이터가 아니에요</span><div><select aria-label="미리보기 시나리오" value={previewKey || 'trend'} onChange={e => { window.location.search = `?preview=${e.target.value}`; }}><option value="trend">분석 완료</option><option value="clarification">조건 확인</option><option value="unavailable">데이터 없음</option><option value="error">연결 오류</option></select><a href="/">실제 대화로 이동<Icon name="chevron" size={14}/></a></div></div>}
      <div className="conversation-scroll" ref={scrollRef} onScroll={e => { const el = e.currentTarget; shouldFollow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; }}>
        {!active.messages.length ? <section className="welcome"><div className="welcome-symbol"><Brand/></div><p className="welcome-eyebrow">복잡한 공정 데이터, 명확한 답으로</p><h1>어떤 운영의 답을<br/>찾고 계신가요?</h1><p className="welcome-description">현황부터 원인, 다음 판단에 필요한 근거까지.<br/>궁금한 점을 편하게 물어보세요.</p><div className="suggestion-grid">{examples.map(item => <button key={item.title} onClick={() => suggestion(item.prompt)} className="suggestion-card"><span className={`suggestion-icon ${item.icon}`}><Icon name={item.icon} size={22}/></span><strong>{item.title}<Icon name="chevron" size={15}/></strong><small>{item.text}</small></button>)}</div><div className="starter-line"><Icon name="spark" size={16}/><span>처음이라면</span><button onClick={() => suggestion('FAB10에서 조회할 수 있는 데이터 목록을 알려줘')}>어떤 데이터를 조회할 수 있어?<Icon name="chevron" size={14}/></button></div></section> : <section className="message-list" aria-label="대화 내용">{active.messages.map((message, i) => message.role === 'user' ? <article key={message.id} className="user-message" aria-label="내 질문"><p>{message.content}</p></article> : <AssistantMessage key={message.id} message={message} busy={busy} canRetry={i === active.messages.length - 1} onRetry={() => send(message.question, message.id)} onInspect={tab => setDrawer({ type: 'inspect', messageId: message.id, tab })} onFeedback={helpful => feedback(message, helpful)} onNotice={setNotice}/>)}</section>}
      </div>
      <div className="composer-zone"><div className="composer-wrap"><button className="context-trigger" onClick={() => setDrawer({ type: 'context' })} disabled={busy}><Icon name="settings" size={16}/><span>분석 범위</span><strong>{Object.values(active.context).filter(Boolean).join(' · ') || '질문에서 자동으로 확인'}</strong><Icon name="down" size={14}/></button><form className={`composer ${busy ? 'is-busy' : ''}`} onSubmit={e => { e.preventDefault(); send(); }}><textarea ref={textarea} id="question" rows={2} maxLength={8000} aria-label="FAB에 질문하기" placeholder="FAB에 대해 궁금한 점을 물어보세요" value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (!busy) send(); } }}/><div className="composer-bottom"><span><Brand small/>FAB Assistant <span className="composer-dot">·</span> {busy ? '분석 중' : '데이터와 지식을 연결해요'}</span>{busy ? <button className="send-button stop-button" type="button" aria-label="응답 수신 중단" onClick={() => abortRef.current?.abort()}><Icon name="stop" size={17}/></button> : <button className="send-button" type="submit" aria-label="질문 보내기" disabled={!draft.trim()}><Icon name="arrow" size={21}/></button>}</div></form><p className="composer-caption">답변의 근거와 데이터 기준을 함께 확인해 주세요.<span>Enter 전송 · Shift + Enter 줄바꿈</span></p></div></div>
    </main>
    {notice && <div className="toast" role="status"><Icon name="info" size={18}/>{notice}</div>}
    {drawer?.type === 'context' && <ContextDrawer context={active.context} onClose={() => setDrawer(null)} onSave={context => { patchConversation(active.id, c => ({ ...c, context })); setDrawer(null); setNotice('다음 질문부터 분석 범위를 적용해요.'); }}/ >}
    {drawer?.type === 'inspect' && selectedMessage && <Inspector key={selectedMessage.id} message={selectedMessage} initialTab={drawer.tab} onClose={() => setDrawer(null)} onNotice={setNotice}/>}
    {drawer?.type === 'artifacts' && <Drawer title="이 대화의 분석 결과" subtitle="질문별로 생성된 차트와 데이터를 모았어요." onClose={() => setDrawer(null)}><div className="inspector-body">{!artifacts.length ? <div className="empty-artifacts"><Icon name="layers" size={38}/><h3>분석 결과가 여기에 쌓여요</h3><p>대화에서 만든 차트와 조회 데이터를<br/>다시 찾아볼 수 있어요.</p></div> : artifacts.map(message => <button className="artifact-card" key={message.id} onClick={() => setDrawer({ type: 'inspect', messageId: message.id, tab: 'data' })}><span className="suggestion-icon chart"><Icon name={message.result?.chart ? 'chart' : 'layers'}/></span><span><strong>{message.result?.chart?.title || '조회 데이터'}</strong><small>{message.question}</small></span><Icon name="chevron" size={18}/></button>)}</div></Drawer>}
  </div>;
}
function AssistantMessage({ message, busy, canRetry, onRetry, onInspect, onFeedback, onNotice }) {
  const [view, setView] = useState('chart'); const result = message.result; const rows = rowsFromResult(result, message.events);
  const pending = message.status === 'streaming'; const failed = ['failed', 'cancelled'].includes(message.status);
  const agentCount = new Set((message.events || []).filter(e => e.node !== 'input').map(e => e.node)).size;
  return <article className="assistant-message" aria-label="FAB Assistant 답변"><div className="assistant-heading"><Brand small/><strong>FAB Assistant</strong>{result && <Chip status={result.status}/>}</div>
    {pending ? <div className="working-state" role="status"><span className="thinking-dots"><i/><i/><i/></span><div><strong>{progressLabel(message.events || [])}</strong><small>답변과 근거를 함께 준비하고 있어요.</small></div></div> : failed ? <div className={`error-state ${message.status === 'cancelled' ? 'cancelled' : ''}`}><Icon name="info"/><div><h3>{message.status === 'cancelled' ? '응답 수신을 중단했어요' : '답변을 가져오지 못했어요'}</h3><p>{message.error || '서버의 분석은 잠시 더 진행될 수 있어요. 필요하면 다시 요청해 주세요.'}</p>{canRetry && <button className="secondary-button" onClick={onRetry} disabled={busy}><Icon name="refresh" size={16}/>다시 시도</button>}</div></div> : result && <>
      <AnswerText text={result.answer || '답변 내용이 반환되지 않았어요. 분석 상세에서 결과를 확인해 주세요.'}/>
      {result.chart && <section className="chart-card"><div className="chart-toolbar"><div className="segmented-control" aria-label="결과 표시 방식"><button aria-pressed={view === 'chart'} className={view === 'chart' ? 'active' : ''} onClick={() => setView('chart')}><Icon name="chart" size={16}/>차트</button><button aria-pressed={view === 'table'} className={view === 'table' ? 'active' : ''} onClick={() => setView('table')}>데이터</button></div>{rows.length > 0 && <button className="icon-button" aria-label="차트 데이터 CSV 저장" onClick={() => downloadRows(rows)}><Icon name="download" size={18}/></button>}</div>{view === 'chart' ? <Chart spec={result.chart}/> : <DataTable rows={rows}/>}<div className="chart-footnote"><span>{rows.length}행 · 서버 반환 데이터</span><button onClick={() => onInspect('data')}>데이터 · SQL 보기<Icon name="chevron" size={14}/></button></div></section>}
      {result.limitations?.length > 0 && <div className="inline-limitations"><Icon name="info" size={17}/><div><strong>해석할 때 확인해 주세요</strong><ul>{result.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></div></div>}
      {result.status === 'needs_clarification' && <p className="next-action"><Icon name="chat" size={17}/>아래 입력창에 기준과 기간을 알려주시면 이어서 분석할게요.</p>}
    </>}
    {(message.events?.length > 0 || result) && <div className="answer-details"><button className="trace-trigger" onClick={() => onInspect('trace')}><span className={`trace-icon ${pending ? 'pending' : ''}`}><Icon name={pending ? 'clock' : 'layers'} size={15}/></span><span>{pending ? '실행 과정 확인' : '어떻게 분석했나요?'}<small>{agentCount > 0 ? `${agentCount}개 에이전트` : '분석 상세'}</small></span><Icon name="chevron" size={15}/></button>{result?.evidence?.length > 0 && <button className="evidence-trigger" onClick={() => onInspect('evidence')}><Icon name="book" size={16}/>근거 {result.evidence?.length || 0}<Icon name="chevron" size={14}/></button>}</div>}
    {result && <div className="answer-actions"><CopyButton text={result.answer || ''} label="답변 복사" onNotice={onNotice}/><div className="feedback-actions"><button className="icon-button" aria-label="도움이 됐어요" aria-pressed={message.feedback === 'helpful'} disabled={!!message.feedback} onClick={() => onFeedback(true)}><Icon name="like" size={16}/></button><button className="icon-button" aria-label="아쉬워요" aria-pressed={message.feedback === 'unhelpful'} disabled={!!message.feedback} onClick={() => onFeedback(false)}><Icon name="like" size={16} style={{ transform: 'rotate(180deg)' }}/></button></div></div>}
  </article>;
}
function ContextDrawer({ context, onSave, onClose }) {
  const [form, setForm] = useState({ ...context });
  return <Drawer title="분석 범위" subtitle="자주 묻는 대상을 지정하면 더 간편해져요." onClose={onClose}><form className="context-form" onSubmit={e => { e.preventDefault(); onSave(Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim()]))); }}><div className="context-help"><Icon name="info" size={19}/><p>비워두면 질문에서 대상을 확인해요. 여기에 입력한 값은 질문보다 우선 적용되니, 대상을 바꿀 때 함께 수정해 주세요.</p></div>{[['fab', 'FAB', '예: fab10'], ['line', '라인', '예: M2'], ['process', '공정', '예: Dry_Etch']].map(([key, label, placeholder]) => <label key={key}>{label}<input value={form[key]} maxLength={100} placeholder={placeholder} onChange={e => setForm({ ...form, [key]: e.target.value })}/></label>)}<div className="context-form-actions"><button type="button" className="secondary-button" onClick={() => setForm({ fab: '', line: '', process: '' })}>입력값 비우기</button><button type="submit" className="primary-button">범위 적용</button></div><p className="muted small-text">이 대화의 다음 질문부터 적용돼요. 이전 대화에서 기억한 대상은 질문에 새 대상을 명시하거나 새 대화에서 변경할 수 있어요.</p></form></Drawer>;
}
createRoot(document.getElementById('root')).render(<Workspace/>);
