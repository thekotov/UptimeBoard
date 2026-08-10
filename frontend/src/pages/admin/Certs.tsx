import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CertItem, type Page, type Status, getCerts } from "../../api/client";
import { RefreshIcon } from "../../components/Icons";
import { SkeletonList } from "../../components/Skeleton";
import { useI18n } from "../../i18n";
import { useToast } from "../../toast";
import { AdminNav } from "./AdminNav";

function daysLeft(expiresAt: string): number {
  return Math.floor((new Date(expiresAt).getTime() - Date.now()) / 864e5);
}

/** Urgency class (reuses the dashboard's cert-pill palette) + a short label. */
function urgency(days: number, t: (k: string, p?: any) => string): { cls: string; label: string } {
  if (days < 0) return { cls: "cert-bad", label: t("cert.expired") };
  if (days === 0) return { cls: "cert-bad", label: t("cert.expiresToday") };
  if (days === 1) return { cls: "cert-warn", label: t("cert.expiresInDay") };
  const label = t("cert.expiresIn", { days });
  if (days <= 7) return { cls: "cert-bad", label };
  if (days <= 14) return { cls: "cert-warn", label };
  if (days <= 30) return { cls: "cert-soon", label };
  return { cls: "cert-ok", label };
}

interface CertGroup {
  key: string;
  expires_at: string | null;
  issuer: string | null;
  subject: string | null;
  items: CertItem[];
}

/** Probes tracking the same subject + issuer + expiry are almost certainly the
 *  same physical certificate (there's no fingerprint column to key on) — group
 *  them so one certificate shows as one row with all its probes underneath. */
function groupCerts(certs: CertItem[]): CertGroup[] {
  const map = new Map<string, CertGroup>();
  for (const c of certs) {
    const key = `${c.subject ?? ""}|${c.issuer ?? ""}|${c.expires_at ?? ""}`;
    let g = map.get(key);
    if (!g) {
      g = { key, expires_at: c.expires_at, issuer: c.issuer, subject: c.subject, items: [] };
      map.set(key, g);
    }
    g.items.push(c);
  }
  return [...map.values()];
}

export function Certs() {
  const { id } = useParams<{ id: string }>();
  const pageId = Number(id);
  const { t, lang } = useI18n();
  const toast = useToast();
  const [page, setPage] = useState<Page | null>(null);
  const [certs, setCerts] = useState<CertItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [onlyExpiring, setOnlyExpiring] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    api.get<Page>(`/admin/pages/${pageId}`).then(({ data }) => setPage(data)).catch(() => undefined);
  }, [pageId]);

  const load = () => {
    setLoading(true);
    getCerts({ page_id: pageId })
      .then(setCerts)
      .catch(() => toast.error(t("toast.error")))
      .finally(() => setLoading(false));
  };
  useEffect(load, [pageId]);

  const soon = certs.filter((c) => c.expires_at && daysLeft(c.expires_at) <= 30).length;
  const shown = onlyExpiring
    ? certs.filter((c) => c.expires_at && daysLeft(c.expires_at) <= 30)
    : certs;
  const groups = useMemo(() => groupCerts(shown), [shown]);
  const allGroupsCount = useMemo(() => groupCerts(certs).length, [certs]);

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="container fade-in">
      <AdminNav />
      <div className="section-head">
        <div className="stack">
          <h2>{t("certs.title")}</h2>
          {page && (
            <span className="muted small">
              <Link to={`/admin/pages/${page.id}`}>← {page.title}</Link>
            </span>
          )}
        </div>
        <div className="inline">
          <div className="filter-pills">
            <button className={onlyExpiring ? "" : "active"} onClick={() => setOnlyExpiring(false)}>
              {t("events.all")}
            </button>
            <button className={onlyExpiring ? "active" : ""} onClick={() => setOnlyExpiring(true)}>
              ⏳ ≤30 {t("certs.days")}
            </button>
          </div>
          <button className="secondary btn-sm" onClick={load} disabled={loading}>
            <RefreshIcon size={14} /> {t("stats.refresh")}
          </button>
        </div>
      </div>
      <p className="muted small">
        {t("certs.hint")}
        {certs.length > 0 &&
          ` · ${t("certs.summary", { total: certs.length, soon })} · ${t("certs.uniqueCount", { n: allGroupsCount })}`}
      </p>

      {loading && certs.length === 0 ? (
        <SkeletonList rows={6} />
      ) : shown.length === 0 ? (
        <div className="card empty">
          <span className="e-emoji">🔒</span>
          {t("certs.empty")}
        </div>
      ) : (
        <div className="table-scroll">
          <table className="table cx-table">
            <thead>
              <tr>
                <th />
                <th>{t("certs.col.status")}</th>
                <th>{t("certs.col.expires")}</th>
                <th>{t("certs.col.cert")}</th>
                <th>{t("certs.col.host")}</th>
                <th>{t("certs.col.usedBy")}</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const d = g.expires_at ? daysLeft(g.expires_at) : null;
                const u = d != null ? urgency(d, t) : null;
                const isOpen = expanded.has(g.key);
                const hosts = [...new Set(g.items.map((i) => i.server_host))];
                return (
                  <Fragment key={g.key}>
                    <tr className="cx-row" onClick={() => toggle(g.key)}>
                      <td className={`cx-chev ${isOpen ? "open" : ""}`}>▸</td>
                      <td><span className={`badge ${u?.cls ?? ""}`}>{u?.label ?? "—"}</span></td>
                      <td className="cx-date">
                        {g.expires_at ? new Date(g.expires_at).toLocaleDateString(lang) : "—"}
                      </td>
                      <td>
                        <div className="cx-subject">{g.subject ?? "—"}</div>
                        {g.issuer && <div className="muted small">{g.issuer}</div>}
                      </td>
                      <td className="muted small">
                        {hosts.slice(0, 2).join(", ")}
                        {hosts.length > 2 ? ` +${hosts.length - 2}` : ""}
                      </td>
                      <td>{t("certs.probesCount", { n: g.items.length })}</td>
                    </tr>
                    {isOpen && (
                      <tr className="cx-sub-row">
                        <td colSpan={6}>
                          <table className="table cx-sub-table">
                            <thead>
                              <tr>
                                <th>{t("stats.probe")}</th>
                                <th>{t("stats.location")}</th>
                                <th>{t("certs.col.status")}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {g.items.map((item) => (
                                <tr key={item.probe_id}>
                                  <td>
                                    <Link
                                      to={`/status/${item.page_slug}?probe=${item.probe_id}`}
                                      title={t("events.openProbe")}
                                      onClick={(e) => e.stopPropagation()}
                                    >
                                      {item.probe_name}
                                    </Link>{" "}
                                    <span className="faint small">· {item.probe_type.toUpperCase()}</span>
                                  </td>
                                  <td className="muted small">
                                    {item.server_name} · {item.server_host} · {item.service_name} · {item.page_title}
                                  </td>
                                  <td>
                                    <span className={`badge ${item.status}`}>
                                      {t(`status.${item.status as Status}`)}
                                    </span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
