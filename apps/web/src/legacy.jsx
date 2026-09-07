import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000/api";

function App() {
  const [cases, setCases] = useState([]);
  const [traces, setTraces] = useState([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [customMessage, setCustomMessage] = useState(
    "fab10의 lotrelease 테이블에서 route_product_3 건수를 날짜 기준으로 라인차트로 그려줘."
  );
  const [streamEvents, setStreamEvents] = useState([]);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadSamples() {
      const response = await fetch(`${API_BASE}/agent-trace/samples`);
      const body = await response.json();
      setCases(body.cases);
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
    const outgoing = customMessage.trim();
    setMessages((current) => [...current, { role: "user", content: outgoing }]);
    try {
      const response = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: outgoing, conversation_id: conversationId || null }),
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
            setConversationId(payload.data.conversation_id);
            setMessages((current) => [
              ...current,
              {
                role: "assistant",
                content: payload.data.answer || "",
                conversationId: payload.data.conversation_id,
                feedback: null,
              },
            ]);
            const finalTrace = {
              ...payload.data,
              label: "Live Text2SQL + visualization",
              message: outgoing,
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

  async function submitFeedback(messageIndex, helpful) {
    const message = messages[messageIndex];
    if (!message?.conversationId || message.feedback === "pending") return;
    setError("");
    setMessages((current) =>
      current.map((item, index) =>
        index === messageIndex ? { ...item, feedback: "pending" } : item
      )
    );
    try {
      const response = await fetch(`${API_BASE}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: message.conversationId,
          helpful,
          trace_id: `web-message-${messageIndex}`,
        }),
      });
      if (!response.ok) throw new Error(`Feedback request failed: ${response.status}`);
      setMessages((current) =>
        current.map((item, index) =>
          index === messageIndex ? { ...item, feedback: helpful ? "helpful" : "unhelpful" } : item
        )
      );
    } catch (reason) {
      setMessages((current) =>
        current.map((item, index) =>
          index === messageIndex ? { ...item, feedback: null } : item
        )
      );
      setError(String(reason));
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
              placeholder="이어질 질문 입력"
            />
            <button type="submit" disabled={loading}>
              Trace
            </button>
          </form>

          {messages.length > 0 && (
            <section className="chat-panel" aria-label="conversation">
              <div className="chat-title">
                <h3>Conversation</h3>
                {conversationId && <span>{conversationId.slice(0, 8)}</span>}
              </div>
              <div className="message-list">
                {messages.map((message, index) => (
                  <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
                    <small>{message.role}</small>
                    <p>{message.content}</p>
                    {message.role === "assistant" && (
                      <div className="feedback-actions" aria-label="answer feedback">
                        <button
                          type="button"
                          aria-label="Mark answer helpful"
                          title="Helpful"
                          className={message.feedback === "helpful" ? "selected" : ""}
                          disabled={message.feedback === "pending"}
                          onClick={() => submitFeedback(index, true)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          aria-label="Mark answer unhelpful"
                          title="Not helpful"
                          className={message.feedback === "unhelpful" ? "selected" : ""}
                          disabled={message.feedback === "pending"}
                          onClick={() => submitFeedback(index, false)}
                        >
                          ↓
                        </button>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </section>
          )}

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
                      {event.data?.reasoning?.summary && (
                        <p className="reasoning">{event.data.reasoning.summary}</p>
                      )}
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

              {activeTrace.reasoning_state?.length > 0 && (
                <section className="reasoning-panel">
                  <h3>Reasoning state</h3>
                  <div className="reasoning-list">
                    {activeTrace.reasoning_state.map((item, index) => (
                      <article className="reasoning-row" key={`${item.node}-${index}`}>
                        <strong>{item.node}</strong>
                        <p>{item.summary}</p>
                      </article>
                    ))}
                  </div>
                </section>
              )}

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
                {activeTrace.chart && <Chart spec={activeTrace.chart} />}
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
  const imputedX = new Set((spec.imputed_points || []).map((point) => String(point.x)));
  const [minY, maxY] = numericDomain(spec, values);
  const span = maxY - minY || 1;
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const yPosition = (value) => padding.top + ((maxY - Number(value)) / span) * plotHeight;
  const points = rows.map((row, index) => {
    const x = padding.left + (rows.length === 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth);
    const y = yPosition(row[yField]);
    const label = String(row[xField]);
    return { x, y, label, value: row[yField], imputed: imputedX.has(label) };
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
        <line x1={padding.left} y1={yPosition(0)} x2={width - padding.right} y2={yPosition(0)} />
        <text x={padding.left - 12} y={padding.top + 4} textAnchor="end">{maxY}</text>
        <text x={padding.left - 12} y={height - padding.bottom + 4} textAnchor="end">{minY}</text>
        {points.length > 1 && <polyline points={pointString} />}
        {points.map((point) => (
          <g key={`${point.label}-${point.x}`}>
            <circle className={point.imputed ? "imputed-point" : undefined} cx={point.x} cy={point.y} r="5">
              <title>{point.imputed ? `${point.label}: inferred zero` : `${point.label}: ${point.value}`}</title>
            </circle>
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

function MultiSeriesLineChart({ spec }) {
  const width = 720;
  const height = 300;
  const padding = { top: 28, right: 28, bottom: 64, left: 56 };
  const xField = spec.encoding.x.field;
  const yField = spec.encoding.y.field;
  const colorField = spec.encoding.color.field;
  const rows = spec.rows || [];
  const imputedKeys = new Set(
    (spec.imputed_points || []).map((point) => `${String(point.x)}::${String(point.series ?? "")}`),
  );
  const categories = orderedCategories(spec, rows, xField);
  const series = [...new Set(rows.map((row) => String(row[colorField])))];
  const values = rows.map((row) => Number(row[yField]));
  const [minY, maxY] = numericDomain(spec, values);
  const span = maxY - minY || 1;
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const colors = ["#16805f", "#d97706", "#2563eb", "#a855f7"];
  const xPosition = (index) =>
    padding.left + (categories.length === 1 ? plotWidth / 2 : (index / (categories.length - 1)) * plotWidth);
  const yPosition = (value) => padding.top + ((maxY - Number(value)) / span) * plotHeight;

  return (
    <figure className="chart">
      <figcaption>
        <strong>{spec.title}</strong>
        <span>{series.join(" · ")}</span>
      </figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
        <line x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} />
        <line x1={padding.left} y1={yPosition(0)} x2={width - padding.right} y2={yPosition(0)} />
        <text x={padding.left - 12} y={padding.top + 4} textAnchor="end">{maxY}</text>
        <text x={padding.left - 12} y={height - padding.bottom + 4} textAnchor="end">{minY}</text>
        {series.map((seriesName, seriesIndex) => {
          const segments = contiguousLineSegments({
            categories,
            rows,
            xField,
            yField,
            colorField,
            seriesName,
            imputedKeys,
            xPosition,
            yPosition,
          });
          const points = segments.flat();
          return (
            <g key={seriesName}>
              {segments.filter((segment) => segment.length > 1).map((segment, segmentIndex) => (
                <polyline
                  key={`${seriesName}-segment-${segmentIndex}`}
                  fill="none"
                  stroke={colors[seriesIndex % colors.length]}
                  points={segment.map((point) => `${point.x},${point.y}`).join(" ")}
                />
              ))}
              {points.map((point) => (
                <circle
                  key={`${seriesName}-${point.category}`}
                  cx={point.x}
                  cy={point.y}
                  r="5"
                  fill={point.imputed ? "#fff" : colors[seriesIndex % colors.length]}
                  stroke={colors[seriesIndex % colors.length]}
                  strokeWidth={point.imputed ? 3 : 1}
                >
                  <title>{`${point.category} · ${seriesName}: ${point.value}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
        {categories.map((category, index) => (
          <text key={category} x={xPosition(index)} y={height - padding.bottom + 24} textAnchor="middle">
            {category}
          </text>
        ))}
        <text className="axis-title" x={width / 2} y={height - 10} textAnchor="middle">
          {spec.encoding.x.title}
        </text>
        <text className="axis-title" x="14" y={height / 2} textAnchor="middle" transform={`rotate(-90 14 ${height / 2})`}>
          {spec.encoding.y.title}
        </text>
      </svg>
    </figure>
  );
}

function Chart({ spec }) {
  if (["bar", "grouped_bar"].includes(spec.type) && spec.encoding?.color?.field) {
    return <GroupedBarChart spec={spec} />;
  }
  if (spec.type === "bar") {
    return <BarChart spec={spec} />;
  }
  if (spec.type === "line" && spec.encoding?.color?.field) {
    return <MultiSeriesLineChart spec={spec} />;
  }
  return <LineChart spec={spec} />;
}

function orderedCategories(spec, rows, xField) {
  const available = [...new Set(rows.map((row) => String(row[xField])))];
  const requested = Array.isArray(spec.encoding?.x?.sort)
    ? spec.encoding.x.sort.map(String)
    : [];
  const availableSet = new Set(available);
  const ordered = requested.filter((category) => availableSet.has(category));
  return [...ordered, ...available.filter((category) => !ordered.includes(category))];
}

function numericDomain(spec, values) {
  const requested = spec.encoding?.y?.domain;
  if (
    Array.isArray(requested)
    && requested.length === 2
    && requested.every((value) => Number.isFinite(Number(value)))
    && Number(requested[0]) < Number(requested[1])
  ) {
    return requested.map(Number);
  }
  const finiteValues = values.filter(Number.isFinite);
  const minY = Math.min(...finiteValues, 0);
  const maxY = Math.max(...finiteValues, 0);
  return minY === maxY ? [minY, minY + 1] : [minY, maxY];
}

function contiguousLineSegments({
  categories,
  rows,
  xField,
  yField,
  colorField,
  seriesName,
  imputedKeys,
  xPosition,
  yPosition,
}) {
  const segments = [];
  let current = [];
  categories.forEach((category, categoryIndex) => {
    const row = rows.find(
      (item) => String(item[xField]) === category && String(item[colorField]) === seriesName,
    );
    if (!row) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    current.push({
      x: xPosition(categoryIndex),
      y: yPosition(row[yField]),
      category,
      value: row[yField],
      imputed: imputedKeys.has(`${category}::${seriesName}`),
    });
  });
  if (current.length) segments.push(current);
  return segments;
}

function BarChart({ spec }) {
  const width = 720;
  const height = 300;
  const padding = { top: 28, right: 28, bottom: 64, left: 56 };
  const xField = spec.encoding.x.field;
  const yField = spec.encoding.y.field;
  const rows = spec.rows || [];
  const categories = orderedCategories(spec, rows, xField);
  const values = rows.map((row) => Number(row[yField]));
  const [minY, maxY] = numericDomain(spec, values);
  const span = maxY - minY || 1;
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const groupWidth = plotWidth / Math.max(categories.length, 1);
  const barWidth = Math.min(56, groupWidth * 0.64);
  const yPosition = (value) => padding.top + ((maxY - Number(value)) / span) * plotHeight;
  const zeroY = yPosition(0);

  return (
    <figure className="chart">
      <figcaption>
        <strong>{spec.title}</strong>
        <span>{spec.encoding.y.title}</span>
      </figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
        <line x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} />
        <line x1={padding.left} y1={zeroY} x2={width - padding.right} y2={zeroY} />
        <text x={padding.left - 12} y={padding.top + 4} textAnchor="end">{maxY}</text>
        <text x={padding.left - 12} y={height - padding.bottom + 4} textAnchor="end">{minY}</text>
        {categories.map((category, index) => {
          const row = rows.find((item) => String(item[xField]) === category);
          const value = Number(row?.[yField]) || 0;
          const valueY = yPosition(value);
          const x = padding.left + index * groupWidth + (groupWidth - barWidth) / 2;
          const y = Math.min(valueY, zeroY);
          const barHeight = Math.abs(zeroY - valueY);
          return (
            <g key={category}>
              <rect className="chart-bar" x={x} y={y} width={barWidth} height={barHeight}>
                <title>{`${category}: ${value}`}</title>
              </rect>
              <text className="point-value" x={x + barWidth / 2} y={value >= 0 ? y - 8 : y + barHeight + 16} textAnchor="middle">
                {value}
              </text>
              <text x={x + barWidth / 2} y={height - padding.bottom + 24} textAnchor="middle">
                {category}
              </text>
            </g>
          );
        })}
        <text className="axis-title" x={width / 2} y={height - 10} textAnchor="middle">
          {spec.encoding.x.title}
        </text>
        <text className="axis-title" x="14" y={height / 2} textAnchor="middle" transform={`rotate(-90 14 ${height / 2})`}>
          {spec.encoding.y.title}
        </text>
      </svg>
    </figure>
  );
}

function GroupedBarChart({ spec }) {
  const width = 720;
  const height = 300;
  const padding = { top: 28, right: 28, bottom: 64, left: 56 };
  const xField = spec.encoding.x.field;
  const yField = spec.encoding.y.field;
  const colorField = spec.encoding.color.field;
  const rows = spec.rows || [];
  const categories = orderedCategories(spec, rows, xField);
  const series = [...new Set(rows.map((row) => String(row[colorField])))];
  const values = rows.map((row) => Number(row[yField]));
  const [minY, maxY] = numericDomain(spec, values);
  const span = maxY - minY || 1;
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const groupWidth = plotWidth / Math.max(categories.length, 1);
  const barWidth = Math.min(48, (groupWidth * 0.72) / Math.max(series.length, 1));
  const colors = ["#16805f", "#d97706", "#2563eb", "#a855f7"];
  const yPosition = (value) => padding.top + ((maxY - Number(value)) / span) * plotHeight;
  const zeroY = yPosition(0);

  return (
    <figure className="chart">
      <figcaption>
        <strong>{spec.title}</strong>
        <span>{series.join(" · ")}</span>
      </figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
        <line x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} />
        <line x1={padding.left} y1={zeroY} x2={width - padding.right} y2={zeroY} />
        <text x={padding.left - 12} y={padding.top + 4} textAnchor="end">{maxY}</text>
        <text x={padding.left - 12} y={height - padding.bottom + 4} textAnchor="end">{minY}</text>
        {categories.flatMap((category, categoryIndex) =>
          series.map((seriesName, seriesIndex) => {
            const row = rows.find(
              (item) => String(item[xField]) === category && String(item[colorField]) === seriesName,
            );
            const value = Number(row?.[yField]) || 0;
            const valueY = yPosition(value);
            const barHeight = Math.abs(zeroY - valueY);
            const groupStart = padding.left + categoryIndex * groupWidth + groupWidth * 0.14;
            const x = groupStart + seriesIndex * barWidth;
            const y = Math.min(valueY, zeroY);
            return (
              <g key={`${category}-${seriesName}`}>
                <rect
                  className="chart-bar"
                  x={x}
                  y={y}
                  width={Math.max(barWidth - 4, 4)}
                  height={barHeight}
                  fill={colors[seriesIndex % colors.length]}
                >
                  <title>{`${category} · ${seriesName}: ${value}`}</title>
                </rect>
                <text className="point-value" x={x + barWidth / 2 - 2} y={value >= 0 ? y - 8 : y + barHeight + 16} textAnchor="middle">
                  {value}
                </text>
              </g>
            );
          }),
        )}
        {categories.map((category, index) => (
          <text
            key={category}
            x={padding.left + index * groupWidth + groupWidth / 2}
            y={height - padding.bottom + 24}
            textAnchor="middle"
          >
            {category}
          </text>
        ))}
        <text className="axis-title" x={width / 2} y={height - 10} textAnchor="middle">
          {spec.encoding.x.title}
        </text>
        <text className="axis-title" x="14" y={height / 2} textAnchor="middle" transform={`rotate(-90 14 ${height / 2})`}>
          {spec.encoding.y.title}
        </text>
      </svg>
    </figure>
  );
}

createRoot(document.getElementById("root")).render(<App />);
