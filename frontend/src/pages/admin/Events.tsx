import { useEffect, useState } from "react";
import { type ChangeEventType, getEvents, type ProbeEventItem } from "../../api/client";
import { RefreshIcon } from "../../components/Icons";
import { relativeTime, useI18n } from "../../i18n";
import { useToast } from "../../toast";
import { AdminNav } from "./AdminNav";

const EVENT_ICON: Record<ChangeEventType, string> = {
  ip_changed: "🔄",
  cert_changed: "📜",
  content_changed: "📝",
  cert_expiring: "⏳",
};
const TYPES: ChangeEventType[] = ["ip_changed", "cert_changed", "content_changed", "cert_expiring"];

export function Events() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [events, setEvents] = useState<ProbeEventItem[]>([]);
  const [filter, setFilter] = useState<ChangeEventType | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    getEvents({ type: filter ?? undefined, limit: 300 })
      .then(setEvents)
      .catch(() => toast.error(t("toast.error")))
      .finally(() => setLoading(false));
  };
  useEffect(load, [filter]);

  return (
    <div className="container fade-in">
      <AdminNav />
      <div className="section-head">
        <h2>{t("events.title")}</h2>
        <div className="inline">
          <div className="filter-pills">
            <button className={filter === null ? "active" : ""} onClick={() => setFilter(null)}>
              {t("events.all")}
            </button>
            {TYPES.map((tp) => (
              <button key={tp} className={filter === tp ? "active" : ""} onClick={() => setFilter(tp)}>
                {EVENT_ICON[tp]} {t(`alerts.event.${tp}`)}
              </button>
            ))}
          </div>
          <button className="secondary btn-sm" onClick={load} disabled={loading}>
            <RefreshIcon size={14} /> {t("stats.refresh")}
          </button>
        </div>
      </div>

      <p className="muted small">{t("events.hint")}</p>

      {events.length === 0 ? (
        <div className="card empty">
          <span className="e-emoji">📭</span>
          {loading ? t("stats.loading") : t("events.empty")}
        </div>
      ) : (
        <div className="feed">
          {events.map((e) => (
            <div key={e.id} className={`feed-row ${e.type}`}>
              <span className="feed-icon" aria-hidden>{EVENT_ICON[e.type]}</span>
              <div className="feed-body">
                <div className="feed-title">
                  <span className="feed-type">{t(`alerts.event.${e.type}`)}</span>
                  <span className="feed-probe">{e.probe_name} · {e.server_name}</span>
                </div>
                {e.detail && <div className="feed-detail">{e.detail}</div>}
                <div className="feed-meta">
                  {e.service_name} · {e.page_title} · {e.probe_type.toUpperCase()}
                </div>
              </div>
              <time className="feed-time" title={new Date(e.created_at).toLocaleString(lang)}>
                {relativeTime(e.created_at, lang)}
              </time>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
