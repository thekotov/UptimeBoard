import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type AdminStats, getAdminStats } from "../../api/client";
import { RefreshIcon } from "../../components/Icons";
import { SkeletonList } from "../../components/Skeleton";
import { relativeTime, useI18n } from "../../i18n";
import { useToast } from "../../toast";
import { AdminNav } from "./AdminNav";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function Tile({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="card" style={{ flex: "1 1 150px", margin: 0 }}>
      <div className="muted small">{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, letterSpacing: "-0.01em" }}>{value}</div>
      {sub != null && <div className="faint small" style={{ marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

export function Stats() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    getAdminStats()
      .then(setStats)
      .catch(() => toast.error(t("toast.error")))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const num = (n: number) => n.toLocaleString(lang);
  const maxTable = stats ? Math.max(1, ...stats.database.tables.map((x) => x.total_bytes)) : 1;
  const workerStatus = stats?.worker.status ?? "unknown";

  return (
    <div className="container fade-in">
      <AdminNav />

      <div className="toolbar">
        <button className="secondary btn-sm" onClick={load} disabled={loading}>
          <RefreshIcon size={14} /> {t("stats.refresh")}
        </button>
        <span className="spacer" />
        <span className={`badge ${workerStatus === "ok" ? "up" : workerStatus === "stale" ? "down" : "unknown"}`}>
          <span className={`dot ${workerStatus === "ok" ? "up" : workerStatus === "stale" ? "down" : "unknown"}`} />
          {t("stats.worker")}:{" "}
          {workerStatus === "ok"
            ? t("worker.ok")
            : workerStatus === "stale"
            ? t("worker.down")
            : t("worker.unknown")}
        </span>
      </div>

      {!stats ? (
        <SkeletonList rows={8} />
      ) : (
        <>
          {/* headline KPIs */}
          <div className="summary" style={{ gap: 12, marginBottom: 16, alignItems: "stretch" }}>
            <Tile label={t("stats.totalDbSize")} value={formatBytes(stats.database.total_bytes)} />
            <Tile
              label={t("stats.checks24h")}
              value={num(stats.metrics.results_last_24h)}
              sub={`${num(stats.metrics.results_last_1h)} ${t("stats.perHour")}`}
            />
            <Tile
              label={t("stats.retention")}
              value={stats.metrics.results_oldest ? relativeTime(stats.metrics.results_oldest, lang) : "—"}
            />
            <Tile
              label={t("stats.openIncidents")}
              value={num(stats.entities.incidents_open)}
              sub={`${num(stats.entities.incidents)} ${t("stats.incidents").toLowerCase()}`}
            />
            <Tile
              label={t("stats.probes")}
              value={`${num(stats.entities.probes_enabled)} / ${num(stats.entities.probes)}`}
            />
          </div>

          {/* monitoring health: is the monitor itself doing its job? */}
          {(() => {
            const mon = stats.monitoring;
            const healthy = mon.overdue_count === 0 && mon.failing_channels.length === 0;
            return (
              <div className="card">
                <div className="card-head">
                  <h3>{t("stats.monitoring")}</h3>
                  <span className={`badge ${healthy ? "up" : "down"}`}>
                    <span className={`dot ${healthy ? "up" : "down"}`} />
                    {healthy ? t("stats.monHealthy") : t("stats.monIssues")}
                  </span>
                </div>
                {healthy ? (
                  <p className="muted small" style={{ margin: 0 }}>{t("stats.monAllGood")}</p>
                ) : (
                  <div style={{ display: "grid", gap: 16 }}>
                    {mon.overdue.length > 0 && (
                      <div>
                        <div className="muted small" style={{ marginBottom: 6 }}>
                          {t("stats.overdue")}: <b>{num(mon.overdue_count)}</b>
                          {mon.never_checked > 0 && ` · ${t("stats.neverChecked")}: ${num(mon.never_checked)}`}
                        </div>
                        <div className="table-scroll">
                          <table className="table">
                            <thead>
                              <tr>
                                <th>{t("stats.probe")}</th>
                                <th>{t("stats.location")}</th>
                                <th style={{ textAlign: "right" }}>{t("stats.lastCheck")}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {mon.overdue.map((p) => (
                                <tr key={p.probe_id}>
                                  <td>
                                    <Link to={`/status/${p.page_slug}?probe=${p.probe_id}`}>{p.probe_name}</Link>
                                    <span className="faint small"> · {p.probe_type.toUpperCase()}</span>
                                  </td>
                                  <td className="muted small">{p.server_name} · {p.page_title}</td>
                                  <td style={{ textAlign: "right" }} className={p.last_checked_at ? "" : "danger-text"}>
                                    {p.last_checked_at ? relativeTime(p.last_checked_at, lang) : t("stats.never")}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                    {mon.failing_channels.length > 0 && (
                      <div>
                        <div className="muted small" style={{ marginBottom: 6 }}>
                          {t("stats.failingChannels")}: <b>{mon.failing_channels.length}</b>
                        </div>
                        <div className="feed">
                          {mon.failing_channels.map((c) => (
                            <div key={c.id} className="feed-row cert_expiring">
                              <span className="feed-icon" aria-hidden>⚠</span>
                              <div className="feed-body">
                                <div className="feed-title"><span className="feed-probe">{c.name} · {c.type}</span></div>
                                {c.last_error && <div className="feed-detail">{c.last_error}</div>}
                                {c.last_sent_at && <div className="feed-meta">{relativeTime(c.last_sent_at, lang)}</div>}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

          {/* database tables */}
          <div className="card">
            <div className="card-head">
              <h3>{t("stats.database")}</h3>
              <span className="muted small">{formatBytes(stats.database.total_bytes)}</span>
            </div>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("stats.table")}</th>
                    <th style={{ textAlign: "right" }}>{t("stats.rows")}</th>
                    <th style={{ textAlign: "right" }}>{t("stats.size")}</th>
                    <th style={{ width: "30%" }}></th>
                  </tr>
                </thead>
                <tbody>
                  {stats.database.tables.map((tbl) => (
                    <tr key={tbl.name}>
                      <td><code>{tbl.name}</code></td>
                      <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{num(tbl.rows)}</td>
                      <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                        {formatBytes(tbl.total_bytes)}
                        {tbl.index_bytes > 0 && (
                          <span className="faint small"> · {t("stats.idx")} {formatBytes(tbl.index_bytes)}</span>
                        )}
                      </td>
                      <td>
                        <div style={{ background: "var(--panel-3)", borderRadius: 4, height: 6, overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${(tbl.total_bytes / maxTable) * 100}%`,
                              height: "100%",
                              background: "var(--accent)",
                            }}
                          />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* metrics detail */}
          <div className="card">
            <div className="card-head"><h3>{t("stats.metrics")}</h3></div>
            <div className="summary" style={{ gap: 12, margin: 0, alignItems: "stretch" }}>
              <Tile label={t("stats.checks1h")} value={num(stats.metrics.results_last_1h)} />
              <Tile label={t("stats.checks24h")} value={num(stats.metrics.results_last_24h)} />
              <Tile
                label={t("stats.rollups")}
                value={`${num(stats.metrics.rollups_hour)} / ${num(stats.metrics.rollups_day)}`}
              />
              <Tile
                label={t("stats.oldest")}
                value={stats.metrics.results_oldest ? relativeTime(stats.metrics.results_oldest, lang) : "—"}
                sub={stats.metrics.results_oldest ? new Date(stats.metrics.results_oldest).toLocaleString(lang) : undefined}
              />
              <Tile
                label={t("stats.newest")}
                value={stats.metrics.results_newest ? relativeTime(stats.metrics.results_newest, lang) : "—"}
                sub={stats.metrics.results_newest ? new Date(stats.metrics.results_newest).toLocaleString(lang) : undefined}
              />
            </div>
          </div>

          {/* entity counts */}
          <div className="card">
            <div className="card-head"><h3>{t("stats.entities")}</h3></div>
            <div className="summary" style={{ gap: 12, margin: 0, alignItems: "stretch" }}>
              <Tile label={t("stats.pages")} value={num(stats.entities.pages)} />
              <Tile label={t("stats.services")} value={num(stats.entities.services)} />
              <Tile label={t("stats.servers")} value={num(stats.entities.servers)} />
              <Tile
                label={t("stats.probes")}
                value={num(stats.entities.probes)}
                sub={`${num(stats.entities.probes_enabled)} ${t("alerts.on")}`}
              />
              <Tile label={t("stats.incidents")} value={num(stats.entities.incidents)} />
              <Tile
                label={t("stats.channels")}
                value={num(stats.entities.alert_channels)}
                sub={`${num(stats.entities.alert_channels_enabled)} ${t("alerts.on")}`}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
