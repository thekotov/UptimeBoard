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
interface Group { id: number; name: string; rows: Row[]; }
const BAD = new Set(["down", "degraded"]);

export function Heatmap() {
  const { slug } = useParams<{ slug: string }>();
  const { t } = useI18n();
  const [page, setPage] = useState<PageStatus | null>(null);
  const [timeline, setTimeline] = useState<Record<number, PageTimelineEntry>>({});
  const [range, setRange] = useState<TimeRange>("24h");
  const [notFound, setNotFound] = useState(false);
  // раскрытые группы; null — ещё не инициализировано (раскроем те, где были сбои)
  const [open, setOpen] = useState<Set<number> | null>(null);

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

  const groups: Group[] = useMemo(() => {
    if (!page) return [];
    return page.services.map((svc) => ({
      id: svc.id,
      name: svc.name,
      rows: svc.servers.flatMap((srv) =>
        srv.probes.map((p) => ({ id: p.id, name: p.name, service: svc.name, server: srv.name }))
      ),
    }));
  }, [page]);
  const rows = useMemo(() => groups.flatMap((g) => g.rows), [groups]);

  // Сводная строка группы: худший статус среди её проб в каждой клетке.
  const groupCells = useMemo(() => {
    const m: Record<number, string[]> = {};
    for (const g of groups) m[g.id] = binRow(g.rows.flatMap((r) => timeline[r.id]?.points ?? []), range);
    return m;
  }, [groups, timeline, range]);

  // При первой загрузке данных раскрываем группы, где за период были сбои.
  useEffect(() => {
    if (open !== null || !groups.length || !Object.keys(timeline).length) return;
    setOpen(new Set(groups.filter((g) => groupCells[g.id]?.some((c) => BAD.has(c))).map((g) => g.id)));
  }, [groups, timeline, groupCells, open]);

  const toggle = (id: number) =>
    setOpen((o) => {
      const n = new Set(o ?? []);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const allOpen = open !== null && groups.every((g) => open.has(g.id));
  const avg = (ids: number[]) => {
    const v = ids.map((id) => timeline[id]?.uptime_pct).filter((x): x is number => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };

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
          {groups.length > 0 && (
            <button
              className="ghost"
              onClick={() => setOpen(allOpen ? new Set() : new Set(groups.map((g) => g.id)))}
            >
              {allOpen ? t("heatmap.collapseAll") : t("heatmap.expandAll")}
            </button>
          )}
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
          {groups.map((g) => {
            const isOpen = !!open?.has(g.id);
            const gUp = avg(g.rows.map((r) => r.id));
            return (
              <div key={g.id} className={`hm-group ${isOpen ? "open" : ""}`}>
                {/* сводная строка группы — клик раскрывает пробы */}
                <button className="hm-row hm-group-row" aria-expanded={isOpen} onClick={() => toggle(g.id)}>
                  <span className="hm-label">
                    <span className="hm-name">
                      <span className={`chev ${isOpen ? "open" : ""}`}>▶</span> {g.name}
                    </span>
                    <span className="hm-srv">{t("heatmap.probes", { n: g.rows.length })}</span>
                  </span>
                  <span className="hm-track">
                    {(groupCells[g.id] ?? []).map((s, i) => <span key={i} className={`hm-c ${s}`} />)}
                  </span>
                  <span className="hm-uptime">{gUp != null ? `${gUp.toFixed(1)}%` : "—"}</span>
                </button>
                {isOpen &&
                  g.rows.map((row) => {
                    const cells = binRow(timeline[row.id]?.points ?? [], range);
                    const uptime = timeline[row.id]?.uptime_pct;
                    return (
                      <div key={row.id} className="hm-row hm-probe-row">
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
            );
          })}
        </div>
      )}
    </div>
  );
}
