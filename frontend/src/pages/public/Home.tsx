import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { listPublicPages, type PageListItem } from "../../api/client";
import { LangSwitch } from "../../components/LangSwitch";
import { useI18n } from "../../i18n";
import { ThemeSwitch } from "../../theme";

export function Home() {
  const { t } = useI18n();
  const [pages, setPages] = useState<PageListItem[]>([]);
  const [q, setQ] = useState("");

  useEffect(() => {
    listPublicPages().then(setPages).catch(() => setPages([]));
  }, []);

  // Filter by query, then group by folder (preserving server ordering).
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const filtered = pages.filter(
      (p) =>
        !needle ||
        p.title.toLowerCase().includes(needle) ||
        p.slug.toLowerCase().includes(needle) ||
        (p.group_name ?? "").toLowerCase().includes(needle)
    );
    const map = new Map<string, PageListItem[]>();
    for (const p of filtered) {
      const key = p.group_name || t("ungrouped");
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(p);
    }
    return [...map.entries()];
  }, [pages, q, t]);

  return (
    <div className="container fade-in">
      <div className="header">
        <div className="stack">
          <h1>{t("app.title")}</h1>
          <span className="sub">{t("home.subtitle")}</span>
        </div>
        <div className="inline">
          <ThemeSwitch />
          <LangSwitch />
          <Link to="/admin" className="badge unknown">
            {t("home.toAdmin")}
          </Link>
        </div>
      </div>

      <div className="toolbar">
        <div className="search">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("search")} />
        </div>
      </div>

      {pages.length === 0 && (
        <div className="card empty">
          <span className="e-emoji">📭</span>
          {t("home.empty")}
        </div>
      )}
      {pages.length > 0 && groups.length === 0 && (
        <div className="card empty">
          <span className="e-emoji">🔍</span>
          {t("nothingFound")}
        </div>
      )}

      {groups.map(([group, items]) => (
        <div key={group}>
          <div className="group-head">
            📁 {group} <span className="count">{items.length}</span>
          </div>
          {items.map((p) => (
            <Link key={p.slug} to={`/status/${p.slug}`} className="dir-item">
              <span className="name">{p.title}</span>
              <span className="muted small">/status/{p.slug}</span>
            </Link>
          ))}
        </div>
      ))}
    </div>
  );
}
