import { useEffect, useState } from "react";
import { getProbeHistory, type ProbeHistory, type Status, type TimeRange } from "../../api/client";
import { LatencyChart } from "../../components/LatencyChart";
import { Modal } from "../../components/Modal";
import { Spinner } from "../../components/Spinner";
import { StatusDot } from "../../components/StatusBadge";
import { Timeline } from "../../components/Timeline";
import { relativeTime, useI18n } from "../../i18n";

const RANGES: TimeRange[] = ["24h", "7d", "30d", "90d"];

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
            <Timeline points={data.points} count={data.points.length || 1} />
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
