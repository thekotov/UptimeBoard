import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getPageStatus, getPageTimeline, type PageStatus, type PageTimelineEntry, type TimeRange,
} from "../../api/client";
import { LangSwitch } from "../../components/LangSwitch";
import { useI18n } from "../../i18n";
import { ThemeSwitch } from "../../theme";

const RANGES: TimeRange[] = ["24h", "7d", "30d", "90d"];
const RANGE_MS: Record<TimeRange, number> = {
  "15m": 15 * 60e3, "24h": 24 * 3600e3, "7d": 7 * 864e5, "30d": 30 * 864e5, "90d": 90 * 864e5,
};
const N = 48; // columns in the matrix
const SEV: Record<string, number> = { down: 4, degraded: 3, unknown: 2, paused: 1, up: 0, maintenance: -1 };

type Pt = { checked_at: string; status: string; latency_ms: number | null };

/** Bin one probe's points into N fixed, time-aligned slots; worst status wins. */
function binRow(points: Pt[], range: TimeRange): string[] {
  const span = RANGE_MS[range];
  const end = points.length ? Math.max(...points.map((p) => new Date(p.checked_at).getTime())) : Date.now();
  const start = end - span;
  const width = span / N;
  const slots: (string | null)[] = Array.from({ length: N }, () => null);
  for (const p of points) {
    const i = Math.max(0, Math.min(N - 1, Math.floor((new Date(p.checked_at).getTime() - start) / width)));
    const cur = slots[i];
    if (cur === null || (SEV[p.status] ?? 0) > (SEV[cur] ?? 0)) slots[i] = p.status;
  }
  return slots.map((s) => s ?? "none");
}

interface Row { id: number; name: string; service: string; server: string; }

export function Heatmap() {
  const { slug } = useParams<{ slug: string }>();
  const { t } = useI18n();
  const [page, setPage] = useState<PageStatus | null>(null);
  const [timeline, setTimeline] = useState<Record<number, PageTimelineEntry>>({});
  const [range, setRange] = useState<TimeRange>("24h");
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!slug) return;
    getPageStatus(slug).then(setPage).catch(() => setNotFound(true));
  }, [slug]);

  useEffect(() => {
    if (!slug) return;
    getPageTimeline(slug, range)
      .then((data) => {
        const map: Record<number, PageTimelineEntry> = {};
        for (const e of data) map[e.probe_id] = e;
        setTimeline(map);
      })
      .catch(() => undefined);
  }, [slug, range]);

  const rows: Row[] = useMemo(() => {
    if (!page) return [];
    const out: Row[] = [];
    for (const svc of page.services)
      for (const srv of svc.servers)
        for (const p of srv.probes) out.push({ id: p.id, name: p.name, service: svc.name, server: srv.name });
    return out;
  }, [page]);

  if (notFound) {
    return (
      <div className="container">
        <div className="header"><h1>{t("app.title")}</h1></div>
        <div className="card empty"><span className="e-emoji">🚫</span>{t("dash.notFound")}</div>
      </div>
    );
  }

  return (
    <div className="container fade-in">
      <div className="header">
        <div className="stack">
          <h1>{page?.title ?? slug}</h1>
          <span className="sub">{t("heatmap.title")}</span>
        </div>
        <div className="inline">
          <div className="filter-pills">
            {RANGES.map((r) => (
              <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>{r}</button>
            ))}
          </div>
          <Link className="ghost" to={`/status/${slug}`} title={t("wall.exit")}>✕</Link>
          <ThemeSwitch />
          <LangSwitch />
        </div>
      </div>

      <div className="hm-legend">
        {(["up", "degraded", "down", "unknown"] as const).map((s) => (
          <span key={s} className="hm-legend-item"><span className={`hm-c ${s}`} /> {t(`status.${s}`)}</span>
        ))}
      </div>

      {rows.length === 0 ? (
        <div className="card empty"><span className="e-emoji">📊</span>{t("heatmap.empty")}</div>
      ) : (
        <div className="card hm-board">
          {rows.map((row) => {
            const cells = binRow(timeline[row.id]?.points ?? [], range);
            const uptime = timeline[row.id]?.uptime_pct;
            return (
              <div key={row.id} className="hm-row">
                <div className="hm-label" title={`${row.service} · ${row.server} · ${row.name}`}>
                  <span className="hm-name">{row.name}</span>
                  <span className="hm-srv">{row.server}</span>
                </div>
                <div className="hm-track">
                  {cells.map((s, i) => <span key={i} className={`hm-c ${s}`} />)}
                </div>
                <div className="hm-uptime">{uptime != null ? `${uptime.toFixed(1)}%` : "—"}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
