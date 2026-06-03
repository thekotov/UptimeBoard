import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getIncidents, type Incident } from "../../api/client";
import { LangSwitch } from "../../components/LangSwitch";
import { Spinner } from "../../components/Spinner";
import { IncidentBadge } from "../../components/StatusBadge";
import { formatDuration, relativeTime, useI18n } from "../../i18n";
import { ThemeSwitch } from "../../theme";

export function IncidentsHistory() {
  const { slug } = useParams<{ slug: string }>();
  const { t, lang } = useI18n();
  const [items, setItems] = useState<Incident[] | null>(null);

  useEffect(() => {
    if (!slug) return;
    getIncidents(slug, 100).then(setItems).catch(() => setItems([]));
  }, [slug]);

  return (
    <div className="container fade-in">
      <div className="header">
        <div className="stack">
          <h1>{t("incidents.title")}</h1>
          <Link to={`/status/${slug}`} className="sub">← /status/{slug}</Link>
        </div>
        <div className="inline">
          <ThemeSwitch />
          <LangSwitch />
        </div>
      </div>

      {!items ? (
        <Spinner />
      ) : items.length === 0 ? (
        <div className="card empty">
          <span className="e-emoji">🎉</span>
          {t("incidents.empty")}
        </div>
      ) : (
        <div className="card">
          {items.map((inc) => (
            <div className="incident-row" key={inc.id}>
              <IncidentBadge ongoing={inc.ongoing} status={inc.last_status} />
              <div className="incident-where">
                <div className="what">
                  {inc.service_name} · {inc.probe_name}{" "}
                  <span className="muted small">({inc.server_name})</span>
                  {inc.ongoing && inc.acknowledged_at && (
                    <span className="badge ack-badge">🔧 {t("incidents.acked")}</span>
                  )}
                </div>
                <div className="muted small">
                  {t("incidents.started", { time: relativeTime(inc.started_at, lang) })}
                </div>
              </div>
              <div className="incident-when">
                {inc.ongoing ? (
                  <span style={{ color: "var(--down-text)" }}>● {t("incidents.ongoing")}</span>
                ) : (
                  t("incidents.resolvedAt", { time: relativeTime(inc.resolved_at, lang) })
                )}
                <div className="dur">{formatDuration(inc.duration_sec, lang)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
