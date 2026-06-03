import { useRef, useState } from "react";
import type { HistoryPoint } from "../api/client";

const W = 600;
const H = 150;
const PAD = { top: 12, right: 8, bottom: 22, left: 40 };

/** Lightweight dependency-free SVG area+line chart of response time over time,
 *  with hover tooltip, time-axis labels and incident markers (down/degraded). */
export function LatencyChart({ points, emptyText }: { points: HistoryPoint[]; emptyText: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  // x-domain spans ALL points (so incident markers line up even without latency);
  // the line/area uses only points that have a latency value.
  const all = points.map((p) => ({ t: new Date(p.checked_at).getTime(), v: p.latency_ms, s: p.status }));
  const data = all.filter((p): p is { t: number; v: number; s: string } => p.v != null);
  if (data.length < 2) return <div className="chart-empty">{emptyText}</div>;

  const times = all.map((d) => d.t);
  const minX = Math.min(...times);
  const maxX = Math.max(...times);
  const maxV = Math.max(...data.map((d) => d.v), 1);

  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const sx = (t: number) => PAD.left + ((t - minX) / (maxX - minX || 1)) * innerW;
  const sy = (v: number) => PAD.top + innerH - (v / maxV) * innerH;

  const line = data.map((d, i) => `${i === 0 ? "M" : "L"}${sx(d.t).toFixed(1)},${sy(d.v).toFixed(1)}`).join(" ");
  const area =
    `M${sx(data[0].t).toFixed(1)},${(PAD.top + innerH).toFixed(1)} ` +
    data.map((d) => `L${sx(d.t).toFixed(1)},${sy(d.v).toFixed(1)}`).join(" ") +
    ` L${sx(data[data.length - 1].t).toFixed(1)},${(PAD.top + innerH).toFixed(1)} Z`;

  const ticks = [0, maxV / 2, maxV];
  const markers = all.filter((d) => d.s === "down" || d.s === "degraded");
  const fmtTime = (t: number) => new Date(t).toLocaleString();

  const onMove = (e: React.MouseEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const tx = minX + ((e.clientX - rect.left) / rect.width) * (maxX - minX);
    let best = 0;
    for (let i = 1; i < data.length; i++) {
      if (Math.abs(data[i].t - tx) < Math.abs(data[best].t - tx)) best = i;
    }
    setHover(best);
  };

  const hp = hover != null ? data[hover] : null;
  const markerY = PAD.top + innerH;

  return (
    <div className="chart-wrap" ref={wrapRef} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {ticks.map((tv, i) => {
          const y = sy(tv);
          return (
            <g key={i}>
              <line className="axis" x1={PAD.left} y1={y} x2={W - PAD.right} y2={y} opacity={0.5} />
              <text className="chart-label" x={4} y={y + 3}>{Math.round(tv)}</text>
            </g>
          );
        })}
        {/* incident markers along the bottom */}
        {markers.map((m, i) => (
          <rect
            key={i}
            x={sx(m.t) - 1}
            y={markerY + 2}
            width={2}
            height={5}
            fill={m.s === "down" ? "var(--down)" : "var(--degraded)"}
          />
        ))}
        <path className="area" d={area} />
        <path className="line" d={line} />
        {hp && (
          <g>
            <line className="axis" x1={sx(hp.t)} y1={PAD.top} x2={sx(hp.t)} y2={PAD.top + innerH} />
            <circle cx={sx(hp.t)} cy={sy(hp.v)} r={3} fill="var(--accent)" />
          </g>
        )}
      </svg>
      <div className="chart-axis-x">
        <span>{new Date(minX).toLocaleString()}</span>
        <span>{new Date(maxX).toLocaleString()}</span>
      </div>
      {hp && (
        <div className="chart-tip" style={{ left: `${((hp.t - minX) / (maxX - minX || 1)) * 100}%` }}>
          <b>{hp.v.toFixed(0)} ms</b>
          <span>{fmtTime(hp.t)}</span>
        </div>
      )}
    </div>
  );
}
