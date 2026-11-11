import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { type PageStatus, type ProbeStatus, type Status } from "../../api/client";
import { formatDuration, relativeTime, useI18n } from "../../i18n";
import { ThemeSwitch } from "../../theme";
import { useStatusTab } from "../../useStatusTab";

const isProblem = (s: Status) => s === "down" || s === "degraded";
// Sort weight so alarms surface worst-first.
const SEV: Record<Status, number> = { down: 5, degraded: 4, unknown: 3, paused: 2, recovered: 1, up: 0 };

/** Сервер на стене: плитка с точками его проб. */
interface SrvTile {
  id: number;
  name: string;
  note: string | null;
  status: Status;
  group: string;
  probes: ProbeStatus[];
}
interface Group {
  id: number;
  name: string;
  servers: SrvTile[];
}

function groupsOf(page: PageStatus | null): Group[] {
  if (!page) return [];
  return page.services.map((svc) => ({
    id: svc.id,
    name: svc.name,
    servers: svc.servers.map((srv) => ({
      id: srv.id, name: srv.name, note: srv.note ?? null, status: srv.status, group: svc.name, probes: srv.probes,
    })),
  }));
}

/** «db-01 (основная БД)» → ["db-01", "основная БД"]: пояснение в скобках — второй строкой. */
function splitName(name: string): [string, string | null] {
  const m = name.match(/^(.*\S)\s*\(([^()]+)\)$/);
  return m ? [m[1], m[2]] : [name, null];
}

/** Перенос длинных имён только по точкам/дефисам/пробелам, а не посреди слова. */
function breakable(text: string) {
  const parts = text.split(/(?<=[.\-_/])/);
  return parts.map((p, i) => (i < parts.length - 1 ? [p, <wbr key={i} />] : p));
}

/** Самый ранний down_since среди проблемных проб сервера — «лежит N». */
function downSince(srv: SrvTile): string | null {
  const ts = srv.probes.filter((p) => isProblem(p.status) && p.down_since).map((p) => p.down_since as string);
  return ts.length ? ts.sort()[0] : null;
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
  error: string | null;
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
          service: svc.name, server: srv.name, host: srv.host, type: p.type, error: p.error,
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
  const groups = useMemo(() => groupsOf(page), [page]);
  const alarms = useMemo(
    () => groups.flatMap((g) => g.servers).filter((sv) => isProblem(sv.status)).sort((a, b) => SEV[b.status] - SEV[a.status]),
    [groups]
  );

  // Автомасштаб: подбираем размер шрифта (всё на стене в em) так, чтобы экран
  // помещался без прокрутки. Пересчёт при смене данных и размера окна.
  const bodyRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [vp, setVp] = useState(0);
  useEffect(() => {
    const on = () => setVp((n) => n + 1);
    window.addEventListener("resize", on);
    document.addEventListener("fullscreenchange", on);
    return () => { window.removeEventListener("resize", on); document.removeEventListener("fullscreenchange", on); };
  }, []);
  const layoutKey = `${groups.map((g) => g.servers.length).join(",")}|${alarms.map((a) => a.id).join(",")}|${onlyProblems}`;
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    let sc = 1.7;
    el.style.fontSize = `${sc}em`;
    while (sc > 0.55 && el.scrollHeight > el.clientHeight + 1) {
      sc = Math.round((sc - 0.05) * 100) / 100;
      el.style.fontSize = `${sc}em`;
    }
    setScale(sc);
  }, [layoutKey, vp]);

  // Flash tiles whose status changed since the last snapshot.
  useEffect(() => {
    if (!page) return;
    const changed = new Set<number>();
    const seen = new Map<number, Status>();
    // мигаем плиткой сервера, если сменился его статус
    for (const sv of groups.flatMap((g) => g.servers)) {
      seen.set(sv.id, sv.status);
      const prev = prevStatus.current.get(sv.id);
      if (prev !== undefined && prev !== sv.status) changed.add(sv.id);
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

  const toggleFullscreen = () => {
    const el = boardRef.current;
    if (!document.fullscreenElement) el?.requestFullscreen?.().catch(() => undefined);
    else document.exitFullscreen?.().catch(() => undefined);
  };

  const status = page?.status ?? "unknown";
  const updated = page ? relativeTime(page.generated_at, lang) : "";

  // ?columns=N — фиксированное число плиток в ряду внутри группы
  const gridStyle = forcedCols > 0 ? { gridTemplateColumns: `repeat(${forcedCols}, minmax(0, 1fr))` } : undefined;

  return (
    <div className={`wallboard ${status}`} ref={boardRef}>
      <div className="wb-top">
        <div className="wb-counter">
          <span className="wb-counter-label">{t(`status.${status}`)}</span>
          {/* плитки — серверы, поэтому и крупный счётчик по серверам; пробы — мелкой подписью */}
          <span className="wb-counter-num">{alarms.length} / {groups.reduce((n, g) => n + g.servers.length, 0)}</span>
          <span className="wb-counter-sub">
            {t("wall.problemServers")}
            {counts.down + counts.degraded > 0 && <> · {t("wall.failingProbes", { n: counts.down + counts.degraded, total: counts.total })}</>}
            {" · "}{page?.title ?? slug}
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

      <div className="wb-body" ref={bodyRef} style={{ fontSize: `${scale}em` }}>
        {alarms.length > 0 && (
          <div className="wb-alarms">
            {alarms.map((sv) => {
              const since = downSince(sv);
              const bad = sv.probes.filter((p) => isProblem(p.status));
              return (
                <div key={sv.id} className={`wb-alarm ${sv.status} ${flash.has(sv.id) ? "flash" : ""}`}>
                  <span className="wb-alarm-eyebrow">
                    {t(`status.${sv.status}`)}
                    {since && <> · {formatDuration(Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 1000)), lang)}</>}
                  </span>
                  <span className="wb-alarm-name">{sv.name}</span>
                  <span className="wb-alarm-group">{sv.group}</span>
                  <ul className="wb-alarm-probes">
                    {bad.slice(0, 3).map((p) => (
                      <li key={p.id}>
                        <b>{p.name}</b>
                        {p.error && <span className="wb-alarm-err"> — {p.error}</span>}
                      </li>
                    ))}
                    {bad.length > 3 && <li className="muted">+{bad.length - 3}</li>}
                  </ul>
                </div>
              );
            })}
          </div>
        )}

        {onlyProblems ? (
          alarms.length === 0 && <div className="wb-empty">{t("overall.up")}</div>
        ) : (
          <div className="wb-groups">
            {groups.map((g) => (
              <section
                key={g.id}
                className="wb-group"
                style={{ flex: `${g.servers.length} 1 ${Math.min(g.servers.length, 6) * 10}em` }}
              >
                <h3 className="wb-group-name">{g.name}</h3>
                <div className="wb-srv-grid" style={gridStyle}>
                  {g.servers.map((sv) => {
                    const [main, sub] = splitName(sv.name);
                    const bad = sv.probes.filter((p) => isProblem(p.status)).length;
                    return (
                      <div
                        key={sv.id}
                        className={`wb-srv ${sv.status} ${flash.has(sv.id) ? "flash" : ""}`}
                        title={[sv.name, sv.note].filter(Boolean).join(" — ")}
                      >
                        <span className="wb-srv-head">
                          <span className={`wb-dot ${sv.status}`} aria-label={t(`status.${sv.status}`)} />
                          <span className="wb-srv-name">{breakable(main)}</span>
                        </span>
                        {sub && <span className="wb-srv-sub">{sub}</span>}
                        <span className="wb-srv-checks">
                          {bad > 0
                            ? t("wall.checksBad", { bad, n: sv.probes.length })
                            : t("wall.checks", { n: sv.probes.length })}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>

      <div className="wb-footer">
        {page && <span>{t("dash.updated", { time: updated })}</span>}
      </div>
    </div>
  );
}
