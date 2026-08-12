import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, type PageStatus, type ServiceStatus, type Status, type TimeRange } from "../../api/client";
import { LangSwitch } from "../../components/LangSwitch";
import { SkeletonDashboard } from "../../components/Skeleton";
import { StatusBadge, StatusDot } from "../../components/StatusBadge";
import { Timeline } from "../../components/Timeline";
import { type Lang, pickRecoveredHeadline, pickUpHeadline, relativeShort, relativeTime, useI18n } from "../../i18n";
import { ThemeSwitch } from "../../theme";
import { useStatusTab } from "../../useStatusTab";
import { Incidents } from "./Incidents";
import { ProbeModal } from "./ProbeModal";

interface UptimeEntry {
  probe_id: number;
  uptime_pct: number;
  total: number;
  points: { checked_at: string; status: string; latency_ms: number | null }[];
}

const RANGES: TimeRange[] = ["15m", "24h", "7d", "30d", "90d"];

const isProblem = (st: Status) => st === "down" || st === "degraded";
const MOBILE_QUERY = "(max-width: 640px)";
const STATUS_GLYPH: Record<Status, string> = { up: "✓", recovered: "🎉", degraded: "!", down: "✕", unknown: "?", paused: "⏸" };
const SEV: Record<string, number> = { down: 4, degraded: 3, unknown: 2, paused: 1, up: 0, maintenance: -1 };
type Pt = { checked_at: string; status: string; latency_ms: number | null };
const RANGE_MS: Record<TimeRange, number> = {
  "15m": 15 * 60e3,
  "24h": 24 * 3600e3,
  "7d": 7 * 864e5,
  "30d": 30 * 864e5,
  "90d": 90 * 864e5,
};

/** Bin every probe's points into a fixed grid of `n` time slots over the range,
 *  worst status wins per slot. Always returns `n` uniform, time-aligned cells —
 *  slots with no data render faint ("none") — so the bar never stretches a few
 *  buckets into huge segments when history is younger than the range. */
function aggregateBar(arrs: Pt[][], range: TimeRange, n = 40): Pt[] {
  const pts = arrs.flat();
  const span = RANGE_MS[range];
  const end = pts.length ? Math.max(...pts.map((p) => new Date(p.checked_at).getTime())) : Date.now();
  const start = end - span;
  const width = span / n;
  const slots: (Pt | null)[] = Array.from({ length: n }, () => null);
  for (const p of pts) {
    const i = Math.max(0, Math.min(n - 1, Math.floor((new Date(p.checked_at).getTime() - start) / width)));
    const cur = slots[i];
    if (!cur || (SEV[p.status] ?? 0) > (SEV[cur.status] ?? 0)) slots[i] = p;
  }
  return slots.map((p, i) =>
    p
      ? { checked_at: p.checked_at, status: p.status, latency_ms: null }
      : { checked_at: new Date(start + width * (i + 0.5)).toISOString(), status: "none", latency_ms: null }
  );
}

/** Days remaining until a TLS certificate expires (floored; negative = expired). */
function certDaysLeft(expiresAt: string): number {
  return Math.floor((new Date(expiresAt).getTime() - Date.now()) / 864e5);
}

/** Small "🔒 expires in N days" badge for TLS probes, coloured by urgency. */
function CertBadge({ expiresAt }: { expiresAt: string }) {
  const { t } = useI18n();
  const days = certDaysLeft(expiresAt);
  let cls = "cert-ok";
  let label: string;
  if (days < 0) {
    cls = "cert-bad";
    label = t("cert.expired");
  } else if (days === 0) {
    cls = "cert-bad";
    label = t("cert.expiresToday");
  } else if (days === 1) {
    cls = "cert-warn";
    label = t("cert.expiresInDay");
  } else {
    label = t("cert.expiresIn", { days });
    if (days <= 14) cls = "cert-warn";
    else if (days <= 30) cls = "cert-soon";
  }
  return (
    <span
      className={`cert-pill ${cls}`}
      title={t("cert.validUntil", { date: new Date(expiresAt).toLocaleString() })}
    >
      🔒 {label}
    </span>
  );
}

/** Colour the latency value relative to the probe's own recent median:
 *  noticeably slower than usual → amber, much slower → red. */
function latClass(latency: number | null, points: { latency_ms: number | null }[]): string {
  if (latency == null) return "";
  const vals = points.map((p) => p.latency_ms).filter((v): v is number => v != null);
  if (vals.length < 3) return "";
  const sorted = [...vals].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  if (median <= 0) return "";
  const r = latency / median;
  if (r >= 2.5) return "lat-bad";
  if (r >= 1.5) return "lat-warn";
  return "";
}

/** "updated N ago" text that re-renders itself every second, in isolation —
 *  so the tick doesn't force a re-render of the whole services/servers/probes
 *  tree above it just to keep this one label fresh. */
function UpdatedAgo({ generatedAt, lang }: { generatedAt: string; lang: Lang }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return <>{relativeTime(generatedAt, lang)}</>;
}

export function Dashboard() {
  const { slug } = useParams<{ slug: string }>();
  const { t, lang } = useI18n();
  const [page, setPage] = useState<PageStatus | null>(null);
  const [timeline, setTimeline] = useState<Record<number, UptimeEntry>>({});
  const [notFound, setNotFound] = useState(false);
  const [live, setLive] = useState(false);
  const [q, setQ] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [collapsedSrv, setCollapsedSrv] = useState<Set<number>>(new Set());
  // URL is the source of truth for the selected range and open probe, so views
  // are shareable and the browser back button closes the probe modal.
  const [searchParams, setSearchParams] = useSearchParams();
  const paramRange = searchParams.get("range") as TimeRange | null;
  const [range, setRangeState] = useState<TimeRange>(
    paramRange && RANGES.includes(paramRange) ? paramRange : "24h"
  );
  const setRange = (r: TimeRange) => {
    setRangeState(r);
    setSearchParams((prev) => {
      const n = new URLSearchParams(prev);
      if (r === "24h") n.delete("range"); else n.set("range", r);
      return n;
    }, { replace: true });
  };
  const selectedProbeId = searchParams.get("probe") ? Number(searchParams.get("probe")) : null;
  const openProbe = (id: number) =>
    setSearchParams((prev) => { const n = new URLSearchParams(prev); n.set("probe", String(id)); return n; });
  const closeProbe = () =>
    setSearchParams((prev) => { const n = new URLSearchParams(prev); n.delete("probe"); return n; });
  const [updateSeq, setUpdateSeq] = useState(0);
  // Resolve the URL's ?probe=<id> against the live snapshot (scans the full tree,
  // so a deep link opens even if the probe is filtered/collapsed in the view).
  const selectedProbe = useMemo(() => {
    if (selectedProbeId == null || !page) return null;
    for (const svc of page.services)
      for (const srv of svc.servers)
        for (const pr of srv.probes)
          if (pr.id === selectedProbeId)
            return { id: pr.id, name: pr.name, certExpiresAt: pr.cert_expires_at, certIssuer: pr.cert_issuer };
    return null;
  }, [selectedProbeId, page]);
  const [flashIds, setFlashIds] = useState<Set<number>>(new Set());
  const [liveMsg, setLiveMsg] = useState("");
  // phones get a denser, collapsed-by-default layout (matches the 640px CSS breakpoint)
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches
  );
  const prevStatus = useRef<Map<number, Status>>(new Map());
  const didInitCollapse = useRef(false);

  useStatusTab(page?.status ?? null, page?.title ?? null);

  // Flash probe rows whose status changed between snapshots; auto-collapse healthy
  // services on first load when there are many.
  useEffect(() => {
    if (!page) return;
    const changed = new Set<number>();
    const seen = new Map<number, Status>();
    const announce: string[] = [];
    for (const svc of page.services)
      for (const srv of svc.servers)
        for (const p of srv.probes) {
          seen.set(p.id, p.status);
          const prev = prevStatus.current.get(p.id);
          if (prev !== undefined && prev !== p.status) {
            changed.add(p.id);
            announce.push(`${svc.name} · ${p.name}: ${t(`status.${p.status}`)}`);
          }
        }
    prevStatus.current = seen;
    if (changed.size) {
      setFlashIds(changed);
      setLiveMsg(announce.join("; "));
      const id = setTimeout(() => setFlashIds(new Set()), 1600);
      return () => clearTimeout(id);
    }
    if (!didInitCollapse.current) {
      didInitCollapse.current = true;
      // Problem services/servers always stay open so issues are visible immediately.
      // Phones fold every healthy service by default (regardless of page settings) to
      // keep the list scannable; desktop only folds when it's a "collapsed" page with many.
      const mobile = window.matchMedia(MOBILE_QUERY).matches;
      if (mobile || (page.default_collapsed && page.services.length > 4)) {
        setCollapsed(new Set(page.services.filter((s) => !isProblem(s.status)).map((s) => s.id)));
      }
      if (page.default_collapsed) {
        setCollapsedSrv(
          new Set(
            page.services
              .flatMap((s) => s.servers)
              .filter((sv) => !isProblem(sv.status))
              .map((sv) => sv.id)
          )
        );
      }
    }
  }, [page]);

  // Track viewport so collapsibility/affordances react to rotation & resize.
  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setIsMobile(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Live status via SSE.
  useEffect(() => {
    if (!slug) return;
    const es = new EventSource(`/api/public/pages/${slug}/stream`);
    const onData = (e: MessageEvent) => {
      try {
        setPage(JSON.parse(e.data));
        setLive(true);
        setNotFound(false);
        setUpdateSeq((n) => n + 1);
      } catch {
        /* ignore */
      }
    };
    es.addEventListener("snapshot", onData);
    es.addEventListener("update", onData);
    es.onerror = () => setLive(false);
    return () => es.close();
  }, [slug]);

  useEffect(() => {
    if (!slug) return;
    api.get(`/public/pages/${slug}`).catch((err) => {
      if (err.response?.status === 404) setNotFound(true);
    });
  }, [slug]);

  // Keep the latest SSE update sequence readable from the timeline effect below
  // without making it re-run (and reset its poll timer) on every single update.
  const updateSeqRef = useRef(updateSeq);
  useEffect(() => {
    updateSeqRef.current = updateSeq;
  }, [updateSeq]);

  // Timeline / uptime for the selected range. Polls on a fixed cadence as a
  // safety net, but skips the request when no SSE update has arrived since the
  // last fetch — an idle page (nothing changing) stops generating traffic
  // instead of re-fetching the same aggregate every 30s forever.
  useEffect(() => {
    if (!slug) return;
    let active = true;
    let lastFetchedSeq = -1;
    const load = (force: boolean) => {
      if (!force && lastFetchedSeq === updateSeqRef.current) return;
      lastFetchedSeq = updateSeqRef.current;
      api
        .get<UptimeEntry[]>(`/public/pages/${slug}/timeline?range=${range}`)
        .then(({ data }) => {
          if (!active) return;
          const map: Record<number, UptimeEntry> = {};
          for (const e of data) map[e.probe_id] = e;
          setTimeline(map);
        })
        .catch(() => undefined);
    };
    load(true);
    const id = setInterval(() => load(false), 30000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [slug, range]);

  const counts = useMemo(() => {
    const c: Record<Status, number> = { up: 0, recovered: 0, degraded: 0, down: 0, unknown: 0, paused: 0 };
    page?.services.forEach((s) => (c[s.status] += 1));
    return c;
  }, [page]);

  const overallUptime = useMemo(() => {
    const vals = Object.values(timeline).map((e) => e.uptime_pct);
    if (!vals.length) return null;
    return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2);
  }, [timeline]);

  // Average uptime over a set of probe ids (for service/server headers).
  const avgUptime = (probeIds: number[]): string | null => {
    const vals = probeIds.map((id) => timeline[id]?.uptime_pct).filter((v): v is number => v != null);
    if (!vals.length) return null;
    return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1);
  };

  const visibleServices: ServiceStatus[] = useMemo(() => {
    if (!page) return [];
    const needle = q.trim().toLowerCase();
    const matches = (s: string) => !needle || s.toLowerCase().includes(needle);

    return page.services
      .filter((service) => (onlyProblems ? isProblem(service.status) : true))
      .map((service) => {
        const serviceHit = matches(service.name);
        const servers = service.servers
          .map((server) => {
            const serverHit = serviceHit || matches(server.name) || matches(server.host);
            const probes = server.probes.filter(
              (p) => (serverHit || matches(p.name)) && (!onlyProblems || isProblem(p.status))
            );
            return { ...server, probes, _hit: serverHit };
          })
          .filter((server) => server.probes.length > 0 || (!onlyProblems && server._hit))
          .map(({ _hit, ...server }) => server);
        const keep = servers.length > 0 || (!onlyProblems && serviceHit);
        return keep ? { ...service, servers } : null;
      })
      .filter((s): s is ServiceStatus => s !== null);
    // Order is preserved as configured in admin (backend returns admin order).
  }, [page, q, onlyProblems]);

  const activeProblems = useMemo(
    () => (page ? page.services.filter((s) => isProblem(s.status)) : []),
    [page]
  );

  const toggle = (sid: number) =>
    setCollapsed((c) => {
      const next = new Set(c);
      next.has(sid) ? next.delete(sid) : next.add(sid);
      return next;
    });

  const toggleSrv = (id: number) =>
    setCollapsedSrv((c) => {
      const next = new Set(c);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  if (notFound) {
    return (
      <div className="container">
        <div className="header">
          <h1>{t("app.title")}</h1>
          <div className="inline">
            <ThemeSwitch />
            <LangSwitch />
          </div>
        </div>
        <div className="card empty">
          <span className="e-emoji">🚫</span>
          {t("dash.notFound")}
        </div>
      </div>
    );
  }

  if (!page) {
    return <SkeletonDashboard />;
  }

  const manyServices = page.services.length > 4;
  // services are collapsible when there are many of them, and always on phones
  const collapsible = manyServices || isMobile;

  return (
    <div className="container fade-in compact">
      <div className="sr-only" aria-live="polite">{liveMsg}</div>
      <div className="header">
        <div className="stack">
          <h1>{page.title}</h1>
          {page.description && <span className="sub">{page.description}</span>}
        </div>
        <div className="inline">
          {live ? (
            <span className="badge up" title={t("dash.live")}>
              <span className="dot up" /> {t("dash.live")}
            </span>
          ) : (
            <span className="badge degraded" title={t("dash.reconnecting")}>
              <span className="dot degraded" /> {t("dash.reconnecting")}
            </span>
          )}
          <details className="views-menu">
            <summary className="ghost" aria-label={t("views.title")}>{t("views.title")} ▾</summary>
            <div className="views-pop">
              <a href={`/status/${slug}/heatmap`}>📊 {t("heatmap.open")}</a>
              <a href={`/status/${slug}/wall`}>🖥 {t("wall.open")}</a>
            </div>
          </details>
          <ThemeSwitch />
          <LangSwitch />
        </div>
      </div>

      {page.announcements?.map((a, i) => (
        <div key={i} className={`announce-banner ${a.level}`}>
          <span className="ann-icon">{a.level === "warning" ? "⚠" : "📢"}</span>
          <div>
            <strong>{a.title}</strong>
            {a.body && <div className="small" style={{ marginTop: 2 }}>{a.body}</div>}
          </div>
        </div>
      ))}

      {page.maintenance && (
        <div className="maint-banner">
          {t("maintenance.banner", { title: page.maintenance.title })}
        </div>
      )}

      <div className={`overall ${page.status}`}>
        <span className={`status-icon ${page.status}`} aria-hidden>
          {STATUS_GLYPH[page.status]}
        </span>
        <div className="stack">
          <span className="title">
            {page.status === "up"
              ? pickUpHeadline(lang)
              : page.status === "recovered"
              ? pickRecoveredHeadline(lang)
              : t(`overall.${page.status}`)}
          </span>
          <span className="sub">
            {page.status === "up"
              ? t("overall.subAllUp", { total: page.services.length })
              : page.status === "recovered"
              ? t("overall.subRecovered", { n: counts.recovered, total: page.services.length })
              : page.status === "unknown"
              ? t("overall.subUnknown")
              : t("overall.subProblems", {
                  n: counts.down + counts.degraded,
                  total: page.services.length,
                })}
            {overallUptime != null && <> · {t("dash.overallUptime", { range: t(`range.${range}`) })}</>}
            {" · "}
            {(() => {
              const [prefix, suffix] = t("dash.updated").split("{time}");
              return <>{prefix}<UpdatedAgo generatedAt={page.generated_at} lang={lang} />{suffix}</>;
            })()}
          </span>
        </div>
        {activeProblems.length > 0 && (
          <div className="overall-chips" aria-label={t("dash.activeProblems", { n: activeProblems.length })}>
            {activeProblems.map((s) => (
              <button
                key={s.id}
                className={`ap-chip ${s.status}`}
                onClick={() =>
                  document.getElementById(`svc-${s.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" })
                }
              >
                <StatusDot status={s.status} /> {s.name}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="toolbar sticky-toolbar">
        {manyServices && (
          <div className="search">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("search")} />
          </div>
        )}
        <button
          className={`toggle ${onlyProblems ? "active" : ""}`}
          onClick={() => setOnlyProblems((v) => !v)}
        >
          <StatusDot status="down" /> {t("onlyProblems")}
        </button>
        <span className="spacer" />
        <div className="range-tabs">
          {RANGES.map((r) => (
            <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>
              {t(`range.${r}`)}
            </button>
          ))}
        </div>
      </div>

      {page.services.length === 0 && (
        <div className="card empty">
          <span className="e-emoji">📭</span>
          {t("dash.empty")}
        </div>
      )}
      {page.services.length > 0 && visibleServices.length === 0 && (
        <div className="card empty">
          <span className="e-emoji">{onlyProblems ? "🎉" : "🔍"}</span>
          {onlyProblems ? t("overall.up") : t("nothingFound")}
        </div>
      )}

      {visibleServices.map((service) => {
        const isCollapsed = collapsed.has(service.id);
        const probeIds = service.servers.flatMap((sv) => sv.probes.map((p) => p.id));
        const svcUptime = avgUptime(probeIds);
        const svcBar = aggregateBar(
          probeIds.map((id) => timeline[id]?.points).filter(Boolean) as Pt[][],
          range
        );
        return (
          <div className="card" key={service.id} id={`svc-${service.id}`}>
            <div
              className={`card-head ${collapsible ? "collapse-head" : ""}`}
              onClick={collapsible ? () => toggle(service.id) : undefined}
              title={collapsible ? t(isCollapsed ? "dash.showService" : "dash.hideService") : undefined}
            >
              <h3>
                {collapsible && <span className={`chev ${isCollapsed ? "" : "open"}`}>▶</span>}
                <span className="svc-name">{service.name}</span>
                {collapsible && isCollapsed && (
                  <span className="count-pill svc-count">{service.servers.length}</span>
                )}
              </h3>
              <div className="inline">
                {svcBar.some((p) => p.status !== "none") && (
                  <span className="svc-bar">
                    <Timeline points={svcBar} count={svcBar.length} />
                  </span>
                )}
                {svcUptime != null && !(isMobile && isCollapsed) && (
                  <span className="muted small">{svcUptime}%</span>
                )}
                <StatusBadge status={service.status} short={isMobile} />
              </div>
            </div>
            {!isCollapsed &&
              service.servers.map((server) => {
                const srvUptime = avgUptime(server.probes.map((p) => p.id));
                const srvCollapsed = collapsedSrv.has(server.id);
                const srvBar = aggregateBar(
                  server.probes.map((p) => timeline[p.id]?.points).filter(Boolean) as Pt[][],
                  range
                );
                return (
                <div className={`server srv-${server.status}`} key={server.id}>
                  <div
                    className="server-head collapse-head"
                    onClick={() => toggleSrv(server.id)}
                    title={srvCollapsed ? t("dash.showProbes") : t("dash.hideProbes")}
                  >
                    <div className="inline">
                      <span className={`chev ${srvCollapsed ? "" : "open"}`}>▶</span>
                      <StatusDot status={server.status} />
                      <strong>{server.name}</strong>
                      {server.note ? (
                        <span className="muted small srv-note" title={server.note}>{server.note}</span>
                      ) : (
                        <span className="muted small">{server.host}</span>
                      )}
                      <span className="count-pill">{server.probes.length}</span>
                    </div>
                    <div className="inline">
                      {srvCollapsed && srvBar.some((p) => p.status !== "none") && (
                        <span className="svc-bar">
                          <Timeline points={srvBar} count={srvBar.length} />
                        </span>
                      )}
                      {srvUptime != null && !(isMobile && srvCollapsed) && (
                        <span className="muted small">{srvUptime}% · {t(`range.${range}`)}</span>
                      )}
                    </div>
                  </div>
                  {!srvCollapsed && (
                  <div className="probe-list">
                  {server.probes.map((probe) => {
                    const tl = timeline[probe.id];
                    return (
                      <div
                        className={`probe-row clickable ${flashIds.has(probe.id) ? "flash" : ""}`}
                        key={probe.id}
                        onClick={() => openProbe(probe.id)}
                        title={t("modal.history")}
                      >
                        <div className="probe-main">
                          <StatusDot status={probe.status} />
                          <span className="name">{probe.name}</span>
                          <span className={`pill type-${probe.type}`}>{probe.type}</span>
                          {probe.cert_expires_at && (
                            <CertBadge expiresAt={probe.cert_expires_at} />
                          )}
                          {probe.status === "paused" && <span className="pill">{t("status.paused")}</span>}
                          {probe.stale && <span className="pill stale-pill">{t("dash.stale")}</span>}
                          {probe.error && probe.status !== "up" && (
                            <span className="probe-err" title={probe.error}>
                              {probe.error}
                            </span>
                          )}
                        </div>
                        {tl && tl.points.length > 0 && (
                          <div className="probe-spark">
                            <Timeline points={tl.points} count={tl.points.length} />
                          </div>
                        )}
                        <div className="probe-stats">
                          {probe.latency_ms != null ? (
                            <span className={`lat ${latClass(probe.latency_ms, tl?.points ?? [])}`}>
                              {probe.latency_ms.toFixed(0)} {t("dash.ms")}
                            </span>
                          ) : (
                            <span className="faint">{t("dash.noData")}</span>
                          )}
                          {tl && <span title={t("dash.uptime24")}>{tl.uptime_pct}%</span>}
                          {probe.checked_at && (
                            <span className="faint" title={relativeTime(probe.checked_at, lang)}>
                              {relativeShort(probe.checked_at, lang)}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  </div>
                  )}
                </div>
                );
              })}
          </div>
        );
      })}

      <Incidents slug={slug!} refreshKey={updateSeq} />

      {selectedProbe && (
        <ProbeModal
          slug={slug!}
          probeId={selectedProbe.id}
          probeName={selectedProbe.name}
          certExpiresAt={selectedProbe.certExpiresAt}
          certIssuer={selectedProbe.certIssuer}
          onClose={closeProbe}
        />
      )}
    </div>
  );
}
