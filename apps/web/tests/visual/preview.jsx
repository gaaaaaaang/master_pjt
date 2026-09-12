import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Chart } from '../../src/charts';
import { DataTable, AnswerText } from '../../src/ui';
import '../../src/workspace.css';
import originalFixtures from './final-demo-fixtures.json';
import comparisonFixtures from './fab-comparison-fixtures.json';
const fixtures = [...comparisonFixtures, ...originalFixtures];

function Preview() {
  const [selected, setSelected] = useState(0);
  const [narrow, setNarrow] = useState(false);
  const [table, setTable] = useState(false);
  const fixture = fixtures[selected];
  return <main style={{ maxWidth: 1100, margin: '32px auto', padding: '0 20px 40px' }}>
    <h1 style={{ fontSize: 22 }}>FAB 결과 화면 검증</h1>
    <p>저장된 합성 조회 결과를 사용합니다. 실모델 재실행 결과가 아닙니다.</p>
    <nav aria-label="검증 화면" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '24px 0' }}>
      {fixtures.map((item, index) => <button className={selected === index ? 'primary-button' : 'secondary-button'} key={item.id} onClick={() => { setSelected(index); setTable(false); }}>{item.label}</button>)}
      <button className="secondary-button" onClick={() => setNarrow(!narrow)}>{narrow ? '넓게 보기' : '360px로 보기'}</button>
    </nav>
    <section className="chart-card" style={{ width: narrow ? 360 : '100%', maxWidth: '100%', margin: '0 auto' }}>
      <div className="chart-toolbar"><strong>{fixture.label}</strong>{fixture.chart && <button className="secondary-button" onClick={() => setTable(!table)}>{table ? '차트 보기' : '데이터 보기'}</button>}</div>
      {fixture.answer && <div style={{ padding: 20 }}><AnswerText text={fixture.answer}/></div>}
      {fixture.chart && !table ? <Chart spec={fixture.chart}/> : <DataTable key={selected} rows={fixture.rows}/>}
      {fixture.data_sources?.length > 0 && <details style={{ padding: 20 }}><summary>데이터 출처 정보</summary>{fixture.data_sources.map(source => <p key={source.kind}>{source.label}: {source.description}</p>)}</details>}
      <p className="chart-scale-note">{fixture.rows.length}개 조회 행 · 화면 표시만 검증</p>
    </section>
  </main>;
}
createRoot(document.getElementById('root')).render(<Preview/>);
