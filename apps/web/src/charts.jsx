import React from "react";
import { showTick, compactLabel } from "./chart-labels";
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
        <line x1={padding.left} y1={yPosition(Math.max(minY, Math.min(maxY, 0)))} x2={width - padding.right} y2={yPosition(Math.max(minY, Math.min(maxY, 0)))} />
        <text x={padding.left - 12} y={padding.top + 4} textAnchor="end">{maxY}</text>
        <text x={padding.left - 12} y={height - padding.bottom + 4} textAnchor="end">{minY}</text>
        {points.length > 1 && <polyline points={pointString} />}
        {points.map((point, pointIndex) => (
          <g key={`${point.label}-${point.x}`}>
            <circle className={point.imputed ? "imputed-point" : undefined} cx={point.x} cy={point.y} r="5">
              <title>{point.imputed ? `${point.label}: inferred zero` : `${point.label}: ${point.value}`}</title>
            </circle>
            {points.length <= 8 && <text className="point-value" x={point.x} y={point.y - 12} textAnchor="middle">{point.value}</text>}
            {showTick(pointIndex, points.length) && <text x={point.x} y={height - padding.bottom + 22} textAnchor="middle"><title>{point.label}</title>{compactLabel(point.label)}</text>}
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
  const colors = ["#3182f6", "#8f7ae5", "#19a988", "#e5aa56"];
  const xPosition = (index) =>
    padding.left + (categories.length === 1 ? plotWidth / 2 : (index / (categories.length - 1)) * plotWidth);
  const yPosition = (value) => padding.top + ((maxY - Number(value)) / span) * plotHeight;

  return (
    <figure className="chart">
      <figcaption>
        <strong>{spec.title}</strong>
        <span className="chart-legend">{series.map((name, index) => <span key={name}><i style={{ background: colors[index % colors.length] }}/>{name}</span>)}</span>
      </figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
        <line x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} />
        <line x1={padding.left} y1={yPosition(Math.max(minY, Math.min(maxY, 0)))} x2={width - padding.right} y2={yPosition(Math.max(minY, Math.min(maxY, 0)))} />
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
        {categories.map((category, index) => showTick(index, categories.length) && (
          <text key={category} x={xPosition(index)} y={height - padding.bottom + 24} textAnchor="middle">
            <title>{category}</title>{compactLabel(category)}
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

export function Chart({ spec }) {
  if (!spec || !["line", "bar", "grouped_bar"].includes(spec.type) || !spec.encoding?.x?.field || !spec.encoding?.y?.field) return <p className="notice">이 차트 형식은 아직 지원하지 않아요. 원본 데이터에서 확인해 주세요.</p>;
  if (!Array.isArray(spec.rows) || !spec.rows.length) return <p className="notice">표시할 데이터가 없어요.</p>;
  if (spec.rows.some(row => !row || typeof row !== "object" || row[spec.encoding.x.field] == null || row[spec.encoding.y.field] == null || row[spec.encoding.y.field] === '' || !Number.isFinite(Number(row[spec.encoding.y.field])))) return <p className="notice">차트에 필요한 값이 누락되었어요. 원본 데이터에서 확인해 주세요.</p>;
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
  const zeroY = yPosition(Math.max(minY, Math.min(maxY, 0)));

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
          if (!row) return null;
          const value = Number(row[yField]);
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
              {showTick(index, categories.length) && <text x={x + barWidth / 2} y={height - padding.bottom + 24} textAnchor="middle">
                <title>{category}</title>{compactLabel(category)}
              </text>}
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
  const colors = ["#3182f6", "#8f7ae5", "#19a988", "#e5aa56"];
  const yPosition = (value) => padding.top + ((maxY - Number(value)) / span) * plotHeight;
  const zeroY = yPosition(Math.max(minY, Math.min(maxY, 0)));

  return (
    <figure className="chart">
      <figcaption>
        <strong>{spec.title}</strong>
        <span className="chart-legend">{series.map((name, index) => <span key={name}><i style={{ background: colors[index % colors.length] }}/>{name}</span>)}</span>
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
            if (!row) return null;
            const value = Number(row[yField]);
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
        {categories.map((category, index) => showTick(index, categories.length) && (
          <text
            key={category}
            x={padding.left + index * groupWidth + groupWidth / 2}
            y={height - padding.bottom + 24}
            textAnchor="middle"
          >
            <title>{category}</title>{compactLabel(category)}
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

