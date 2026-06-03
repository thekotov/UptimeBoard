interface Point {
  checked_at: string;
  status: string;
  latency_ms: number | null;
}

/** Compact uptime sparkline: one cell per recent check (most recent on the right).
 *  Status is conveyed by colour AND texture (stripes for degraded, hatch for down)
 *  so it stays readable for colour-blind users; each cell also has a title. */
export function Timeline({ points, count = 40 }: { points: Point[]; count?: number }) {
  const recent = points.slice(-count);
  return (
    <div className="timeline" role="img" aria-label="uptime history">
      {recent.map((p, i) => (
        <div
          key={i}
          className={`tl-cell ${p.status}`}
          title={`${new Date(p.checked_at).toLocaleString()} — ${p.status}`}
        />
      ))}
    </div>
  );
}
