import { useEffect, useState } from "react";
import { getProbeHistory, type ProbeHistory, type Status, type TimeRange } from "../../api/client";
import { LatencyChart } from "../../components/LatencyChart";
import { Modal } from "../../components/Modal";
import { Spinner } from "../../components/Spinner";
import { StatusDot } from "../../components/StatusBadge";
import { relativeTime, useI18n } from "../../i18n";

const RANGES: TimeRange[] = ["15m", "24h", "7d", "30d", "90d"];

const RANGE_MS: Record<TimeRange, number> = {
  "15m": 15 * 60e3,
  "24h": 24 * 3600e3,
  "7d": 7 * 864e5,
  "30d": 30 * 864e5,
  "90d": 90 * 864e5,
};

const SEV: Record<string, number> = { down: 4, degraded: 3, unknown: 2, paused: 1, up: 0 };

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

export function ProbeModal({
  slug,
  probeId,
  probeName,
  onClose,
}: {
  slug: string;
  probeId: number;
  probeName: string;
  onClose: () => void;
}) {
  const { t, lang } = useI18n();
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
          {data && <span className="pill">{data.type}</span>}
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
        <div className="range-tabs">
          {RANGES.map((r) => (
            <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>
              {t(`range.${r}`)}
            </button>
          ))}
        </div>
      </div>

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
              const v = data.points.map((p) => p.latency_ms).filter((x): x is number => x != null);
              if (!v.length) return null;
              const avg = v.reduce((a, b) => a + b, 0) / v.length;
              return (
                <span style={{ fontVariantNumeric: "tabular-nums" }}>
                  min {Math.min(...v).toFixed(0)} · avg {avg.toFixed(0)} · max {Math.max(...v).toFixed(0)} {t("dash.ms")}
                </span>
              );
            })()}
          </div>
          <LatencyChart points={data.points} emptyText={t("modal.noLatency")} />

          <h4 style={{ margin: "18px 0 6px", fontSize: 14 }}>{t("modal.recent")}</h4>
          <div>
            {data.recent.map((r, i) => (
              <div className="log-row" key={i}>
                <StatusDot status={r.status as Status} />
                <span className="when">{relativeTime(r.checked_at, lang)}</span>
                {r.error && (
                  <span className="muted" style={{ color: "var(--down-text)" }}>
                    {r.error}
                  </span>
                )}
                <span className="lat">
                  {r.latency_ms != null ? `${r.latency_ms.toFixed(0)} ${t("dash.ms")}` : "—"}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </Modal>
  );
}
