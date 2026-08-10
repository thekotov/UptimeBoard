/** Shimmer placeholder block. width/height are any CSS size. */
export function Skeleton({ w = "100%", h = 14, r = 6, style }: { w?: number | string; h?: number | string; r?: number; style?: React.CSSProperties }) {
  return <span className="skeleton" style={{ width: w, height: h, borderRadius: r, ...style }} />;
}

/** A card-shaped placeholder approximating a service block on the dashboard. */
export function SkeletonCard() {
  return (
    <div className="card">
      <div className="card-head">
        <Skeleton w={180} h={18} />
        <Skeleton w={90} h={22} r={7} />
      </div>
      <div className="server">
        <div className="server-head">
          <Skeleton w={140} h={14} />
          <Skeleton w={70} h={20} r={7} />
        </div>
        <div className="probe-list">
          {[0, 1, 2].map((i) => (
            <div className="probe-row" key={i}>
              <Skeleton w={`${40 + i * 12}%`} h={12} />
              <span className="spacer" />
              <Skeleton w={60} h={12} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** A card of placeholder list rows — for loading admin feeds/tables. */
export function SkeletonList({ rows = 6 }: { rows?: number }) {
  return (
    <div className="card">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="incident-row">
          <Skeleton w={`${28 + (i % 4) * 14}%`} h={14} />
          <span className="spacer" />
          <Skeleton w={64} h={14} />
        </div>
      ))}
    </div>
  );
}

export function SkeletonDashboard() {
  return (
    <div className="container">
      <div className="header">
        <div className="stack">
          <Skeleton w={220} h={24} />
          <Skeleton w={140} h={12} style={{ marginTop: 6 }} />
        </div>
        <Skeleton w={120} h={32} r={8} />
      </div>
      <div className="overall" style={{ borderLeftColor: "var(--border)" }}>
        <Skeleton w={20} h={20} r={10} />
        <Skeleton w={240} h={18} />
      </div>
      <SkeletonCard />
      <SkeletonCard />
    </div>
  );
}
