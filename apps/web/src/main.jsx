import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_BASE = "http://localhost:8000/api";

function App() {
  const [cases, setCases] = useState([]);
  const [traces, setTraces] = useState([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [customMessage, setCustomMessage] = useState(
    "fab10의 lotrelease 테이블에서 route_product_3 건수를 날짜 기준으로 라인차트로 그려줘."
  );
  const [streamEvents, setStreamEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadSamples() {
      const response = await fetch(`${API_BASE}/agent-trace/samples`);
      const body = await response.json();
      setCases(body.cases);
      runBatch(body.cases);
    }
    loadSamples().catch((reason) => setError(String(reason)));
  }, []);

  async function runBatch(nextCases = cases) {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/agent-trace/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cases: nextCases }),
      });
      const body = await response.json();
      setTraces(body.traces);
      setActiveIndex(0);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function runCustom(event) {
    event.preventDefault();
    if (!customMessage.trim()) return;
    setLoading(true);
    setError("");
    setStreamEvents([]);
    try {
      const response = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: customMessage }),
      });
      if (!response.ok || !response.body) {
        throw new Error(`Stream request failed: ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          const payload = parseSseBlock(block);
          if (!payload) continue;
          setStreamEvents((current) => [...current, payload]);
          if (payload.type === "run_completed") {
            const finalTrace = {
              ...payload.data,
              label: "Live Text2SQL + visualization",
              message: customMessage,
              passed: payload.data.status === "succeeded",
            };
            setTraces((current) => [finalTrace, ...current]);
            setActiveIndex(0);
          }
          if (payload.type === "run_failed") {
            throw new Error(payload.data?.error || payload.message);
          }
        }
        if (done) break;
      }
    } catch (reason) {
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }

  const activeTrace = traces[activeIndex];
  const score = useMemo(() => {
    if (!traces.length) return { passed: 0, total: 0 };
    return {
      passed: traces.filter((trace) => trace.passed).length,
      total: traces.length,
    };
  }, [traces]);

  return (
    <main className="dashboard">
      <section className="summary">
        <div>
          <p className="eyebrow">Planner / Supervisor Evaluation</p>
          <h1>Agent orchestration trace</h1>
        </div>
        <div className="scoreboard" aria-label="evaluation score">
          <span>{score.passed}</span>
          <small>passed / {score.total}</small>
        </div>
      </section>

      <section className="workbench">
        <aside className="case-list" aria-label="trace cases">
          <div className="toolbar">
            <button type="button" onClick={() => runBatch()} disabled={loading}>
              {loading ? "Running" : "Run suite"}
            </button>
          </div>
          {error && <p className="error">{error}</p>}
          {traces.map((trace, index) => (
            <button
              className={`case-row ${index === activeIndex ? "active" : ""}`}
              key={`${trace.conversation_id}-${index}`}
              onClick={() => setActiveIndex(index)}
              type="button"
            >
              <span className={trace.passed ? "dot pass" : "dot fail"} />
              <span>
                <strong>{trace.label}</strong>
                <small>
                  {trace.query_type} · {trace.status}
                </small>
              </span>
            </button>
          ))}
        </aside>

        <section className="trace-view">
          <form className="custom-runner" onSubmit={runCustom}>
            <input
              value={customMessage}
              onChange={(event) => setCustomMessage(event.target.value)}
              placeholder="질문 입력"
            />
            <button type="submit" disabled={loading}>
              Trace
            </button>
          </form>

          {streamEvents.length > 0 && (
            <section className="stream-panel" aria-live="polite">
              <div className="stream-title">
                <h3>Live execution</h3>
                {loading && <span>Running</span>}
              </div>
              <div className="stream-list">
                {streamEvents.map((event, index) => (
                  <article className="stream-row" key={`${event.node}-${event.type}-${index}`}>
                    <span className={`stream-dot ${event.type}`} />
                    <div>
                      <strong>{event.node}</strong>
                      <p>{event.message}</p>
                      {event.node === "text2sql" && event.data?.sql && (
                        <pre>{event.data.sql}</pre>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}

          {activeTrace && (
            <>
              <header className="trace-header">
                <div>
                  <p className="eyebrow">{activeTrace.status}</p>
                  <h2>{activeTrace.message}</h2>
                </div>
                <StatusPill passed={activeTrace.passed} />
              </header>

              <div className="metric-grid">
                <Metric label="Query type" value={activeTrace.query_type} />
                <Metric label="Expected" value={activeTrace.expected_query_type || "n/a"} />
                <Metric label="Confidence" value={formatConfidence(activeTrace.confidence)} />
                <Metric label="Evidence" value={String(activeTrace.evidence.length)} />
              </div>

              <section className="flow">
                {(activeTrace.plan?.execution_steps || []).map((step, index) => (
                  <article className="step" key={`${step.agent}-${index}`}>
                    <span>{index + 1}</span>
                    <div>
                      <strong>{step.agent}</strong>
                      <p>{step.action}</p>
                      <small>{step.reason}</small>
                    </div>
                  </article>
                ))}
              </section>

              <div className="split">
                <section>
                  <h3>Supervisor runs</h3>
                  {activeTrace.agent_runs.map((run) => (
                    <article className="run-row" key={`${run.agent}-${run.status}`}>
                      <strong>{run.agent}</strong>
                      <span>{run.status}</span>
                      <p>{run.summary}</p>
                    </article>
                  ))}
                </section>
                <section>
                  <h3>Evidence and limits</h3>
                  {activeTrace.evidence.map((item) => (
                    <article className="evidence" key={`${item.source_type}-${item.title}`}>
                      <strong>{item.title}</strong>
                      <small>{item.source_type}</small>
                    </article>
                  ))}
                  {activeTrace.limitations.map((item) => (
                    <p className="limit" key={item}>
                      {item}
                    </p>
                  ))}
                </section>
              </div>

              <section className="answer-box">
                <h3>Answer</h3>
                <p>{activeTrace.answer}</p>
                {activeTrace.sql && <pre>{activeTrace.sql}</pre>}
                {activeTrace.chart && <LineChart spec={activeTrace.chart} />}
              </section>
            </>
          )}
        </section>
      </section>
    </main>
  );
}

function Metric({ label, value }) {
  return (
    <article className="metric">
      <small>{label}</small>
      <strong>{value}</strong>
    </article>
  );
}

function StatusPill({ passed }) {
  return <span className={passed ? "pill pass" : "pill fail"}>{passed ? "Pass" : "Review"}</span>;
}

function formatConfidence(value) {
  if (value === null || value === undefined) return "n/a";
  return `${Math.round(value * 100)}%`;
}

function parseSseBlock(block) {
  const dataLine = block.split("\n").find((line) => line.startsWith("data:"));
  if (!dataLine) return null;
  return JSON.parse(dataLine.slice(5).trim());
}

function LineChart({ spec }) {
  const width = 720;
  const height = 280;
  const padding = { top: 24, right: 28, bottom: 52, left: 56 };
  const xField = spec.encoding?.x?.field;
  const yField = spec.encoding?.y?.field;
  const rows = spec.rows || [];
  const values = rows.map((row) => Number(row[yField]));
  const maxY = Math.max(...values, 1);
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const points = rows.map((row, index) => {
    const x = padding.left + (rows.length === 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth);
    const y = padding.top + plotHeight - (Number(row[yField]) / maxY) * plotHeight;
    return { x, y, label: String(row[xField]), value: row[yField] };
  });
  const pointString = points.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <figure className="chart">
      <figcaption>
        <strong>{spec.title}</strong>
        <span>{spec.series}</span>
      </figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
        <line x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} />
        <line x1={padding.left} y1={height - padding.bottom} x2={width - padding.right} y2={height - padding.bottom} />
        <text x={padding.left - 12} y={padding.top + 4} textAnchor="end">{maxY}</text>
        <text x={padding.left - 12} y={height - padding.bottom + 4} textAnchor="end">0</text>
        {points.length > 1 && <polyline points={pointString} />}
        {points.map((point) => (
          <g key={`${point.label}-${point.x}`}>
            <circle cx={point.x} cy={point.y} r="5" />
            <text className="point-value" x={point.x} y={point.y - 12} textAnchor="middle">{point.value}</text>
            <text x={point.x} y={height - padding.bottom + 22} textAnchor="middle">{point.label}</text>
          </g>
        ))}
        <text className="axis-title" x={width / 2} y={height - 8} textAnchor="middle">
          {spec.encoding?.x?.title}
        </text>
        <text className="axis-title" x="14" y={height / 2} textAnchor="middle" transform={`rotate(-90 14 ${height / 2})`}>
          {spec.encoding?.y?.title}
        </text>
      </svg>
    </figure>
  );
}

createRoot(document.getElementById("root")).render(<App />);
