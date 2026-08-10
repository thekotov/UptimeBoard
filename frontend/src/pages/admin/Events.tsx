import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ChangeEventType, getEvents, type Page, type ProbeEventItem } from "../../api/client";
import { RefreshIcon } from "../../components/Icons";
import { SkeletonList } from "../../components/Skeleton";
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

/** Bucket events (newest-first) under day headers: Today / Yesterday / date. */
function byDay(events: ProbeEventItem[], t: (k: string) => string, lang: string) {
  const groups: { label: string; items: ProbeEventItem[] }[] = [];
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const dayMs = 864e5;
  for (const e of events) {
    const d = new Date(e.created_at); d.setHours(0, 0, 0, 0);
    const diff = Math.round((today.getTime() - d.getTime()) / dayMs);
    const label = diff <= 0 ? t("events.today") : diff === 1 ? t("events.yesterday")
      : new Date(e.created_at).toLocaleDateString(lang);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(e);
    else groups.push({ label, items: [e] });
  }
  return groups;
}

export function Events() {
  const { id } = useParams<{ id: string }>();
  const pageId = Number(id);
  const { t, lang } = useI18n();
  const toast = useToast();
  const [page, setPage] = useState<Page | null>(null);
  const [events, setEvents] = useState<ProbeEventItem[]>([]);
  const [filter, setFilter] = useState<ChangeEventType | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get<Page>(`/admin/pages/${pageId}`).then(({ data }) => setPage(data)).catch(() => undefined);
  }, [pageId]);

  const load = () => {
    setLoading(true);
    getEvents({ type: filter ?? undefined, limit: 300, page_id: pageId })
      .then(setEvents)
      .catch(() => toast.error(t("toast.error")))
      .finally(() => setLoading(false));
  };
  useEffect(load, [pageId, filter]);

  return (
    <div className="container fade-in">
      <AdminNav />
      <div className="section-head">
        <div className="stack">
          <h2>{t("events.title")}</h2>
          {page && (
            <span className="muted small">
              <Link to={`/admin/pages/${page.id}`}>← {page.title}</Link>
            </span>
          )}
        </div>
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

      {loading && events.length === 0 ? (
        <SkeletonList rows={7} />
      ) : events.length === 0 ? (
        <div className="card empty">
          <span className="e-emoji">📭</span>
          {t("events.empty")}
        </div>
      ) : (
        <div className="feed">
          {byDay(events, t, lang).map((g) => (
            <div key={g.label} className="feed-day">
              <div className="feed-day-head">{g.label}</div>
              {g.items.map((e) => (
                <Link
                  key={e.id}
                  className={`feed-row ${e.type}`}
                  to={`/status/${e.page_slug}?probe=${e.probe_id}`}
                  title={t("events.openProbe")}
                >
                  <span className="feed-icon" aria-hidden>{EVENT_ICON[e.type]}</span>
                  <div className="feed-body">
                    <div className="feed-title">
                      <span className="feed-type">{t(`alerts.event.${e.type}`)}</span>
                      <span className="feed-probe">{e.probe_name} · {e.server_name}</span>
                    </div>
                    {e.detail && <div className="feed-detail">{e.detail}</div>}
                    <div className="feed-meta">
                      {e.service_name} · {e.probe_type.toUpperCase()}
                    </div>
                  </div>
                  <time className="feed-time" title={new Date(e.created_at).toLocaleString(lang)}>
                    {relativeTime(e.created_at, lang)}
                  </time>
                </Link>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
