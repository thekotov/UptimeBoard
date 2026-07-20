import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { type PageStatus, type Status } from "../../api/client";
import { relativeTime, useI18n } from "../../i18n";
import { ThemeSwitch } from "../../theme";
import { useStatusTab } from "../../useStatusTab";

const isProblem = (s: Status) => s === "down" || s === "degraded";
const GLYPH: Record<Status, string> = {
  up: "✓", recovered: "🎉", degraded: "!", down: "✕", unknown: "?", paused: "⏸",
};
// Sort weight so alarms surface worst-first.
const SEV: Record<Status, number> = { down: 5, degraded: 4, unknown: 3, paused: 2, recovered: 1, up: 0 };

// Absolute latency hint (no per-probe history in the live snapshot): a subtle cue
// that one endpoint is much slower than the wall's typical sub-300ms readings.
function latClass(ms: number | null): string {
  if (ms == null) return "";
  if (ms >= 1200) return "bad";
  if (ms >= 500) return "warn";
  return "";
}

interface Tile {
  id: number;
  name: string;
  status: Status;
  latency: number | null;
  service: string;
  server: string;
  host: string;
  type: string;
}

/** Flatten a page snapshot into one tile per probe, carrying its service/server. */
function flatten(page: PageStatus | null): Tile[] {
  if (!page) return [];
  const out: Tile[] = [];
  for (const svc of page.services)
    for (const srv of svc.servers)
      for (const p of srv.probes)
        out.push({
          id: p.id, name: p.name, status: p.status, latency: p.latency_ms,
          service: svc.name, server: srv.name, host: srv.host, type: p.type,
        });
  return out;
}

/** Full-screen NOC / wall-board view of a status page. Calm while healthy, loud
 *  the moment something breaks — designed for an always-on ops-room display.
 *  Query params: ?problems=1 (alarms only), ?columns=N (force grid columns). */
export function WallBoard() {
  const { slug } = useParams<{ slug: string }>();
  const { t, lang } = useI18n();
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const forcedCols = Math.max(0, Number(params.get("columns")) || 0);

  const [page, setPage] = useState<PageStatus | null>(null);
  const [live, setLive] = useState(false);
  const [onlyProblems, setOnlyProblems] = useState(params.get("problems") === "1");
  const [clock, setClock] = useState("");
  const [, setTick] = useState(0);
  const [flash, setFlash] = useState<Set<number>>(new Set());
  const prevStatus = useRef<Map<number, Status>>(new Map());
  const boardRef = useRef<HTMLDivElement>(null);

  useStatusTab(page?.status ?? null, page?.title ?? null);

  // Clock + a 1s tick so "updated N ago" stays fresh.
  useEffect(() => {
    const upd = () => {
      setClock(new Date().toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
      setTick((n) => n + 1);
    };
    upd();
    const id = setInterval(upd, 1000);
    return () => clearInterval(id);
  }, [lang]);

  // Live status via SSE (same stream the public dashboard uses).
  useEffect(() => {
    if (!slug) return;
    const es = new EventSource(`/api/public/pages/${slug}/stream`);
    const onData = (e: MessageEvent) => {
      try {
        setPage(JSON.parse(e.data));
        setLive(true);
      } catch {
        /* ignore */
      }
    };
    es.addEventListener("snapshot", onData);
    es.addEventListener("update", onData);
    es.onerror = () => setLive(false);
    return () => es.close();
  }, [slug]);

  // Best-effort screen wake-lock so kiosk displays don't sleep.
  useEffect(() => {
    let lock: { release: () => void } | null = null;
    const nav = navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<any> } };
    nav.wakeLock?.request("screen").then((l) => (lock = l)).catch(() => undefined);
    return () => lock?.release?.();
  }, []);

  const tiles = useMemo(() => flatten(page), [page]);

  // Flash tiles whose status changed since the last snapshot.
  useEffect(() => {
    if (!page) return;
    const changed = new Set<number>();
    const seen = new Map<number, Status>();
    for (const tl of tiles) {
      seen.set(tl.id, tl.status);
      const prev = prevStatus.current.get(tl.id);
      if (prev !== undefined && prev !== tl.status) changed.add(tl.id);
    }
    prevStatus.current = seen;
    if (changed.size) {
      setFlash(changed);
      const id = setTimeout(() => setFlash(new Set()), 1800);
      return () => clearTimeout(id);
    }
  }, [page]);

  const counts = useMemo(() => {
    const c = { total: tiles.length, up: 0, degraded: 0, down: 0, other: 0 };
    for (const tl of tiles) {
      if (tl.status === "down") c.down++;
      else if (tl.status === "degraded") c.degraded++;
      else if (tl.status === "up" || tl.status === "recovered") c.up++;
      else c.other++;
    }
    return c;
  }, [tiles]);

  const alarms = useMemo(
    () => tiles.filter((tl) => isProblem(tl.status)).sort((a, b) => SEV[b.status] - SEV[a.status]),
    [tiles]
  );

  const shown = onlyProblems ? tiles.filter((tl) => isProblem(tl.status)) : tiles;

  const toggleFullscreen = () => {
    const el = boardRef.current;
    if (!document.fullscreenElement) el?.requestFullscreen?.().catch(() => undefined);
    else document.exitFullscreen?.().catch(() => undefined);
  };

  const status = page?.status ?? "unknown";
  const updated = page ? relativeTime(page.generated_at, lang) : "";

  const gridStyle = forcedCols > 0 ? { gridTemplateColumns: `repeat(${forcedCols}, minmax(0, 1fr))` } : undefined;

  return (
    <div className={`wallboard ${status}`} ref={boardRef}>
      <div className="wb-top">
        <div className="wb-title">
          <span className="wb-glyph" aria-hidden>{GLYPH[status]}</span>
          <div className="wb-stack">
            <h1>{page?.title ?? slug}</h1>
            <span className="wb-headline">
              {status === "up"
                ? t("overall.subAllUp", { total: counts.total })
                : status === "unknown"
                ? "—"
                : t("overall.subProblems", { n: counts.down + counts.degraded, total: counts.total })}
            </span>
          </div>
        </div>

        <div className="wb-metrics">
          <span className="wb-metric up"><b>{counts.up}</b> {t("status.up")}</span>
          <span className={`wb-metric degraded ${counts.degraded ? "active" : "zero"}`}>
            <b>{counts.degraded}</b> {t("status.degraded")}
          </span>
          <span className={`wb-metric down ${counts.down ? "active" : "zero"}`}>
            <b>{counts.down}</b> {t("status.down")}
          </span>
        </div>

        <div className="wb-controls">
          <span className={`wb-live ${live ? "on" : "off"}`}>
            <span className="dot" /> {live ? t("dash.live") : t("dash.reconnecting")}
          </span>
          <span className="wb-clock" aria-hidden>{clock}</span>
          <button className={`wb-toggle ${onlyProblems ? "on" : ""}`} onClick={() => setOnlyProblems((v) => !v)}>
            {onlyProblems ? t("wall.showAll") : t("wall.onlyProblems")}
          </button>
          <button className="wb-icon" onClick={toggleFullscreen} title={t("wall.fullscreen")} aria-label={t("wall.fullscreen")}>⛶</button>
          <ThemeSwitch />
          <Link className="wb-icon" to={`/status/${slug}`} title={t("wall.exit")} aria-label={t("wall.exit")}>✕</Link>
        </div>
      </div>

      {page?.maintenance && (
        <div className="wb-maint">{t("maintenance.banner", { title: page.maintenance.title })}</div>
      )}

      {alarms.length > 0 && (
        <div className="wb-alarms">
          <div className="wb-alarms-head">🔴 {t("dash.activeProblems", { n: alarms.length })}</div>
          <div className="wb-alarms-list">
            {alarms.map((a) => (
              <span key={a.id} className={`wb-alarm ${a.status}`}>
                <b>{a.service}</b> · {a.server} · {a.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {shown.length === 0 ? (
        <div className="wb-empty">{onlyProblems ? "🟢 " + t("overall.up") : "—"}</div>
      ) : (
        <div className="wb-grid" style={gridStyle}>
          {shown.map((tl) => {
            // When the probe name is just its type ("HTTP"), it can't tell duplicate
            // tiles apart — lead with the server instead so each tile is identifiable.
            const generic = tl.name.trim().toLowerCase() === tl.type.toLowerCase();
            const primary = generic ? tl.server : tl.name;
            const secondary = generic ? tl.service : `${tl.service} · ${tl.server}`;
            return (
              <div key={tl.id} className={`wb-tile ${tl.status} ${flash.has(tl.id) ? "flash" : ""}`}>
                <div className="wb-tile-head">
                  <span className={`wb-dot ${tl.status}`} aria-hidden />
                  <span className="wb-tile-name">{primary}</span>
                </div>
                <div className="wb-tile-sub">{secondary}</div>
                <div className="wb-tile-foot">
                  <span className="wb-tile-type">{tl.type.toUpperCase()}</span>
                  {tl.latency != null && (
                    <span className={`wb-tile-lat ${latClass(tl.latency)}`}>{tl.latency.toFixed(0)} {t("dash.ms")}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="wb-footer">
        {page && <span>{t("dash.updated", { time: updated })}</span>}
      </div>
    </div>
  );
}
