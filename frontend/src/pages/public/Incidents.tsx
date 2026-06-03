import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getIncidents, type Incident } from "../../api/client";
import { IncidentBadge } from "../../components/StatusBadge";
import { formatDuration, relativeTime, useI18n } from "../../i18n";

export function Incidents({ slug, refreshKey }: { slug: string; refreshKey: number }) {
  const { t, lang } = useI18n();
  const [items, setItems] = useState<Incident[] | null>(null);
  const [open, setOpen] = useState(false); // collapsed by default

  useEffect(() => {
    let active = true;
    getIncidents(slug, 15)
      .then((d) => active && setItems(d))
      .catch(() => active && setItems([]));
    return () => {
      active = false;
    };
  }, [slug, refreshKey]);

  if (!items) return null;

  return (
    <div className="card">
      <div className="card-head collapse-head" onClick={() => setOpen((v) => !v)}>
        <h3>
          <span className={`chev ${open ? "open" : ""}`}>▶</span> 🛠 {t("incidents.title")}
          <span className="count-pill">{items.length}</span>
        </h3>
        {items.length > 0 && (
          <Link
            to={`/status/${slug}/history`}
            className="small"
            onClick={(e) => e.stopPropagation()}
          >
            {t("incidents.showAll")} →
          </Link>
        )}
      </div>
      {!open ? null : items.length === 0 ? (
        <div className="empty">{t("incidents.empty")}</div>
      ) : (
        items.map((inc) => (
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
        ))
      )}
    </div>
  );
}
