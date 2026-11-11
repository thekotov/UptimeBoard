import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getIncidents, type Incident } from "../../api/client";
import { type Lang, formatDuration, relativeTime, useI18n } from "../../i18n";

const DAY = 864e5;
const FEED_LIMIT = 15;

/** Загрузка инцидентов страницы (до 100 — хватает на сводку за 7 дней). Дашборд
 *  вызывает хук один раз и сам решает, где показать блок: наверху при идущем сбое. */
export function useIncidents(slug: string | undefined, refreshKey: number): Incident[] | null {
  const [items, setItems] = useState<Incident[] | null>(null);
  useEffect(() => {
    if (!slug) return;
    let active = true;
    getIncidents(slug, 100)
      .then((d) => active && setItems(d))
      .catch(() => active && setItems([]));
    return () => {
      active = false;
    };
  }, [slug, refreshKey]);
  return items;
}

/** Метка времени для ленты: «14:02», «вчера 14:02» или «08.11 14:02». */
function feedTime(iso: string, lang: Lang, yesterday: string): string {
  const d = new Date(iso);
  const hm = d.toLocaleTimeString(lang === "ru" ? "ru-RU" : "en-GB", { hour: "2-digit", minute: "2-digit" });
  const startToday = new Date();
  startToday.setHours(0, 0, 0, 0);
  if (d >= startToday) return hm;
  if (d.getTime() >= startToday.getTime() - DAY) return `${yesterday} ${hm}`;
  const dm = d.toLocaleDateString(lang === "ru" ? "ru-RU" : "en-GB", { day: "2-digit", month: "2-digit" });
  return `${dm} ${hm}`;
}

/** Сбои по дням за последние 7 дней (сегодня — последний столбик). */
function perDay(items: Incident[]): { day: Date; n: number }[] {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const days = Array.from({ length: 7 }, (_, i) => ({ day: new Date(start.getTime() - (6 - i) * DAY), n: 0 }));
  for (const inc of items) {
    const idx = 6 - Math.floor((start.getTime() - new Date(inc.started_at).setHours(0, 0, 0, 0)) / DAY);
    if (idx >= 0 && idx < 7) days[idx].n += 1;
  }
  return days;
}

function Feed({ items, slug }: { items: Incident[]; slug: string }) {
  const { t, lang } = useI18n();
  return (
    <ol className="inc-feed">
      {items.map((inc) => (
        <li key={inc.id} className={`inc-item ${inc.ongoing ? `ongoing ${inc.last_status}` : "resolved"}`}>
          <time className="inc-time" dateTime={inc.started_at} title={new Date(inc.started_at).toLocaleString(lang)}>
            {feedTime(inc.started_at, lang, t("inc.yesterday"))}
          </time>
          <span className="inc-dot" aria-hidden />
          <div className="inc-what">
            <Link to={`/status/${slug}?probe=${inc.probe_id}`} className="inc-who">
              {inc.server_name}
            </Link>
            <span className="muted"> · {inc.probe_name}</span>
            <div className="small faint">{inc.service_name}</div>
          </div>
          <div className="inc-state">
            {inc.ongoing ? (
              <span className="inc-live">
                {t("inc.ongoingShort")}
                {inc.acknowledged_at && <> · {t("incidents.acked")}</>}
              </span>
            ) : (
              <span className="muted">{t("inc.restored")}</span>
            )}
            <span className="inc-dur">{formatDuration(inc.duration_sec, lang)}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Блок «Последние сбои». Пока есть незакрытый сбой — `active`: стоит под шапкой,
 *  раскрыт, с красной полосой. Иначе — тихая строка-сводка за 7 дней, по клику лента. */
export function Incidents({ slug, items, active }: { slug: string; items: Incident[]; active: boolean }) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);

  const week = useMemo(() => items.filter((i) => Date.now() - new Date(i.started_at).getTime() < 7 * DAY), [items]);
  const days = useMemo(() => perDay(week), [week]);
  const max = Math.max(1, ...days.map((d) => d.n));
  const last = items[0];
  const ongoing = items.filter((i) => i.ongoing);

  const summary = (
    <div className="inc-summary">
      <span className="inc-spark" role="img" aria-label={t("inc.summary", { n: week.length })}>
        {days.map((d) => (
          <span
            key={d.day.toISOString()}
            className={`inc-bar ${d.n ? "has" : ""}`}
            style={{ height: d.n ? `${30 + (70 * d.n) / max}%` : undefined }}
            title={`${d.day.toLocaleDateString(lang)} — ${d.n}`}
          />
        ))}
      </span>
      <span className="inc-sum-text">
        {week.length === 0 ? (
          t("inc.summaryNone")
        ) : (
          <>
            {t("inc.summary", { n: week.length === 100 ? "100+" : week.length })}
            {last && !active && (
              <span className="muted">
                {" · "}
                {t("inc.last", {
                  time: relativeTime(last.started_at, lang),
                  who: last.server_name,
                  dur: formatDuration(last.duration_sec, lang),
                })}
              </span>
            )}
          </>
        )}
      </span>
      <span className="spacer" />
      {items.length > 0 && (
        <button className="inc-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? t("inc.hide") : t("inc.show")}
        </button>
      )}
      {items.length > 0 && (
        <Link to={`/status/${slug}/history`} className="small">
          {t("incidents.showAll")}
        </Link>
      )}
    </div>
  );

  if (active) {
    const rest = items.filter((i) => !i.ongoing).slice(0, FEED_LIMIT);
    return (
      <section className="inc-block active" aria-live="polite">
        <h3 className="inc-title">{t("inc.ongoingTitle", { n: ongoing.length })}</h3>
        <Feed items={ongoing} slug={slug} />
        {summary}
        {open && rest.length > 0 && <Feed items={rest} slug={slug} />}
      </section>
    );
  }

  return (
    <section className="inc-block">
      <h3 className="inc-title">{t("incidents.title")}</h3>
      {summary}
      {open && <Feed items={items.slice(0, FEED_LIMIT)} slug={slug} />}
    </section>
  );
}
