import { useEffect, useState } from "react";
import { getProbeHistory, type ProbeHistory, type Status, type TimeRange } from "../../api/client";
import { LatencyChart } from "../../components/LatencyChart";
import { Modal } from "../../components/Modal";
import { Spinner } from "../../components/Spinner";
import { StatusDot } from "../../components/StatusBadge";
import { classifyError } from "../../errors";
import { relativeTime, useI18n } from "../../i18n";
import { useToast } from "../../toast";

interface RecentGroup {
  status: string;
  error: string | null | undefined;
  count: number;
  newest: string;        // checked_at of the most recent check in the run
  latency_ms: number | null;
}

/** Collapse consecutive identical checks (same status + error) into one row with
 *  a count, so a long run of the same failure reads as one line, not fifteen.
 *  ``recent`` is newest-first. */
function groupRecent(recent: { status: string; error?: string | null; checked_at: string; latency_ms: number | null }[]): RecentGroup[] {
  const out: RecentGroup[] = [];
  for (const r of recent) {
    const last = out[out.length - 1];
    if (last && last.status === r.status && last.error === r.error) {
      last.count += 1;
    } else {
      out.push({ status: r.status, error: r.error, count: 1, newest: r.checked_at, latency_ms: r.latency_ms });
    }
  }
  return out;
}

const RANGES: TimeRange[] = ["15m", "24h", "7d", "30d", "90d"];

const RANGE_MS: Record<TimeRange, number> = {
  "15m": 15 * 60e3,
  "24h": 24 * 3600e3,
  "7d": 7 * 864e5,
  "30d": 30 * 864e5,
  "90d": 90 * 864e5,
};

const SEV: Record<string, number> = { down: 4, degraded: 3, unknown: 2, paused: 1, up: 0, maintenance: -1 };

interface Cell {
  status: string;
  at: number;
  empty: boolean;
}

/** Bin points into a fixed grid of `n` time slots over [end-range, end].
 *  Each slot keeps the worst status of the checks that fall in it; slots with
 *  no data stay empty (faint). This keeps the bar dense and time-accurate even
 *  when the probe's history covers only part of the range. */
function binCells(points: { checked_at: string; status: string }[], range: TimeRange, n: number): Cell[] {
  const span = RANGE_MS[range];
  // Anchor the grid to the newest sample so the bar always ends "now-ish".
  const end = points.length ? new Date(points[points.length - 1].checked_at).getTime() : Date.now();
  const start = end - span;
  const width = span / n;
  const cells: Cell[] = Array.from({ length: n }, (_, i) => ({
    status: "unknown",
    at: start + width * (i + 0.5),
    empty: true,
  }));
  for (const p of points) {
    const idx = Math.max(0, Math.min(n - 1, Math.floor((new Date(p.checked_at).getTime() - start) / width)));
    const c = cells[idx];
    if (c.empty || (SEV[p.status] ?? 0) > (SEV[c.status] ?? 0)) {
      c.status = p.status;
      c.empty = false;
    }
  }
  return cells;
}

/** Inline coloured "expires in N days / expired" text for the cert card. */
function CertCountdown({ expiresAt }: { expiresAt: string }) {
  const { t } = useI18n();
  const days = Math.floor((new Date(expiresAt).getTime() - Date.now()) / 864e5);
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
  return <b className={`cert-text ${cls}`}>{label}</b>;
}

export function ProbeModal({
  slug,
  probeId,
  probeName,
  certExpiresAt,
  certIssuer,
  onClose,
}: {
  slug: string;
  probeId: number;
  probeName: string;
  certExpiresAt?: string | null;
  certIssuer?: string | null;
  onClose: () => void;
}) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [range, setRange] = useState<TimeRange>("24h");
  const [data, setData] = useState<ProbeHistory | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    getProbeHistory(slug, probeId, range)
      .then((d) => active && setData(d))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [slug, probeId, range]);

  return (
    <Modal
      title={
        <span className="inline">
          {probeName}
          {data && <span className={`pill type-${data.type}`}>{data.type}</span>}
        </span>
      }
      onClose={onClose}
    >
      <div className="toolbar" style={{ marginBottom: 12 }}>
        {data && (
          <span className="muted small">
            {t("modal.uptime")}: <b style={{ color: "var(--text)" }}>{data.uptime_pct}%</b> ·{" "}
            {data.total} {t("modal.checks")} · {data.host}
          </span>
        )}
        <span className="spacer" />
        <button className="ghost btn-sm" title={t("modal.copyLink")} aria-label={t("modal.copyLink")}
          onClick={() => {
            navigator.clipboard?.writeText(`${location.origin}/status/${slug}?probe=${probeId}&range=${range}`);
            toast.success(t("io.copied"));
          }}>🔗</button>
        <div className="range-tabs">
          {RANGES.map((r) => (
            <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>
              {t(`range.${r}`)}
            </button>
          ))}
        </div>
      </div>

      {certExpiresAt && (
        <div className="cert-card">
          <span className="cert-card-title">🔒 {t("cert.title")}</span>
          <div className="cert-grid">
            <span className="muted small">{t("cert.expiresAt")}</span>
            <span>
              {new Date(certExpiresAt).toLocaleString()} ·{" "}
              <CertCountdown expiresAt={certExpiresAt} />
            </span>
            {certIssuer && (
              <>
                <span className="muted small">{t("cert.issuer")}</span>
                <span>{certIssuer}</span>
              </>
            )}
          </div>
        </div>
      )}

      {loading || !data ? (
        <Spinner />
      ) : (
        <>
          <div className="status-bar">
            <div className="timeline" role="img" aria-label="uptime history">
              {binCells(data.points, range, 90).map((c, i) => (
                <div
                  key={i}
                  className={`tl-cell ${c.empty ? "none" : c.status}`}
                  title={
                    c.empty
                      ? `${new Date(c.at).toLocaleString()} — ${t("modal.noData")}`
                      : `${new Date(c.at).toLocaleString()} — ${t(`status.${c.status}`)}`
                  }
                />
              ))}
            </div>
          </div>
          <div className="chart-axis-x" style={{ marginBottom: 14 }}>
            <span>{t(`range.${range}`)}</span>
            <span>{t("dash.now")}</span>
          </div>
          <div className="muted small" style={{ marginBottom: 4, display: "flex", justifyContent: "space-between" }}>
            <span>{t("modal.latencyTitle")}</span>
            {(() => {
              const L = data.latency;
              const fmt = (n: number | null) => (n != null ? n.toFixed(0) : "—");
              if (!L || L.p95 == null) return null;
              return (
                <span style={{ fontVariantNumeric: "tabular-nums" }} title={L.approx ? t("modal.latencyApprox") : undefined}>
                  p50 {fmt(L.p50)} · p95 {fmt(L.p95)} · p99 {fmt(L.p99)} · max {fmt(L.max)} {t("dash.ms")}
                  {L.approx && " ≈"}
                </span>
              );
            })()}
          </div>
          <LatencyChart points={data.points} emptyText={t("modal.noLatency")} />

          <h4 style={{ margin: "18px 0 6px", fontSize: 14 }}>{t("modal.recent")}</h4>
          <div>
            {groupRecent(data.recent).map((g, i) => {
              const key = classifyError(g.error);
              const hint = key ? t(key) : g.error;
              return (
                <div className="log-row" key={i}>
                  <StatusDot status={g.status as Status} />
                  <span className="when">
                    {relativeTime(g.newest, lang)}
                    {g.count > 1 && (
                      <span className="rep-chip" title={t("modal.repeated", { n: g.count })}>×{g.count}</span>
                    )}
                  </span>
                  {g.error && g.status !== "up" && (
                    <span className="log-err" title={g.error !== hint ? g.error : undefined}>
                      {hint}
                    </span>
                  )}
                  <span className="lat">
                    {g.latency_ms != null ? `${g.latency_ms.toFixed(0)} ${t("dash.ms")}` : "—"}
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </Modal>
  );
}
