// Small dependency-free SVG charts in the single Exigo purple hue (light -> dark = low -> high).
// Every mark carries a <title> so hovering shows the exact value.

export interface Point {
  label: string;
  value: number;
  display?: string;
}

const W = 520;
const H = 190;
const PAD = { l: 34, r: 10, t: 12, b: 26 };

function niceMax(max: number): number {
  if (max <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(max));
  const n = max / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}

function Axes({ max, ticks = 4 }: { max: number; ticks?: number }) {
  return (
    <g className="chart-axis">
      {Array.from({ length: ticks + 1 }, (_, i) => {
        const v = (max / ticks) * i;
        const y = PAD.t + (H - PAD.t - PAD.b) * (1 - i / ticks);
        return (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y} y2={y} className="chart-grid" />
            <text x={PAD.l - 8} y={y + 4} textAnchor="end">
              {Number.isInteger(v) ? v : v.toFixed(1)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

export function BarChart({ data, unit = "" }: { data: Point[]; unit?: string }) {
  const max = niceMax(Math.max(...data.map((d) => d.value), 0));
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  const slot = innerW / Math.max(data.length, 1);
  const bw = Math.min(34, slot * 0.55);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Bar chart">
      <Axes max={max} />
      {data.map((d, i) => {
        const h = (d.value / max) * innerH;
        const x = PAD.l + slot * i + (slot - bw) / 2;
        return (
          <g key={d.label} className="chart-bar">
            <rect x={x} y={PAD.t + innerH - h} width={bw} height={Math.max(h, 0)} rx={4} />
            <rect x={x - 6} y={PAD.t} width={bw + 12} height={innerH} fill="transparent" />
            <title>{`${d.label}: ${d.display ?? d.value}${unit}`}</title>
            <text x={x + bw / 2} y={H - 8} textAnchor="middle" className="chart-label">
              {d.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function AreaChart({ data, unitLabel }: { data: Point[]; unitLabel?: string }) {
  const max = niceMax(Math.max(...data.map((d) => d.value), 0));
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  const step = data.length > 1 ? innerW / (data.length - 1) : 0;
  const pts = data.map((d, i) => ({
    x: PAD.l + (data.length > 1 ? step * i : innerW / 2),
    y: PAD.t + innerH * (1 - d.value / max),
    d,
  }));
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = pts.length ? `${line} L${pts[pts.length - 1].x},${PAD.t + innerH} L${pts[0].x},${PAD.t + innerH} Z` : "";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label={unitLabel ?? "Trend chart"}>
      <Axes max={max} />
      <path d={area} className="chart-area" />
      <path d={line} className="chart-line" />
      {pts.map((p) => (
        <g key={p.d.label} className="chart-dot">
          <circle cx={p.x} cy={p.y} r={4} />
          <circle cx={p.x} cy={p.y} r={12} fill="transparent" />
          <title>{`${p.d.label}: ${p.d.display ?? p.d.value}`}</title>
        </g>
      ))}
      {pts.length > 0 &&
        pts.map((p, i) =>
          pts.length <= 8 || i % Math.ceil(pts.length / 8) === 0 ? (
            <text key={p.d.label} x={p.x} y={H - 8} textAnchor="middle" className="chart-label">
              {p.d.label}
            </text>
          ) : null,
        )}
    </svg>
  );
}

export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const w = 90;
  const h = 36;
  const d = values
    .map((v, i) => `${i ? "L" : "M"}${((w / (values.length - 1)) * i).toFixed(1)},${(h - 4 - ((v - min) / span) * (h - 8)).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" className="spark">
      <path d={d} />
    </svg>
  );
}

export function Donut({ done, total, centerLabel }: { done: number; total: number; centerLabel: string }) {
  const r = 52;
  const c = 2 * Math.PI * r;
  const pct = total ? done / total : 0;
  return (
    <svg viewBox="0 0 140 140" className="donut" role="img" aria-label={`${done} of ${total} ${centerLabel}`}>
      <circle cx="70" cy="70" r={r} className="donut-track" />
      <circle
        cx="70"
        cy="70"
        r={r}
        className="donut-fill"
        strokeDasharray={`${c * pct} ${c}`}
        transform="rotate(-90 70 70)"
      />
      <text x="70" y="68" textAnchor="middle" className="donut-num">
        {total ? Math.round(pct * 100) : 0}%
      </text>
      <text x="70" y="86" textAnchor="middle" className="donut-sub">
        {centerLabel}
      </text>
    </svg>
  );
}
