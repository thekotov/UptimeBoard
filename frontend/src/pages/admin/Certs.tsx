import { useEffect, useState } from "react";
import { type CertItem, getCerts } from "../../api/client";
import { RefreshIcon } from "../../components/Icons";
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

export function Certs() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [certs, setCerts] = useState<CertItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    getCerts()
      .then(setCerts)
      .catch(() => toast.error(t("toast.error")))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const soon = certs.filter((c) => c.expires_at && daysLeft(c.expires_at) <= 30).length;

  return (
    <div className="container fade-in">
      <AdminNav />
      <div className="section-head">
        <h2>{t("certs.title")}</h2>
        <button className="secondary btn-sm" onClick={load} disabled={loading}>
          <RefreshIcon size={14} /> {t("stats.refresh")}
        </button>
      </div>
      <p className="muted small">
        {t("certs.hint")}
        {certs.length > 0 && ` · ${t("certs.summary", { total: certs.length, soon })}`}
      </p>

      {certs.length === 0 ? (
        <div className="card empty">
          <span className="e-emoji">🔒</span>
          {loading ? t("stats.loading") : t("certs.empty")}
        </div>
      ) : (
        <div className="cx-board">
          {certs.map((c) => {
            const d = c.expires_at ? daysLeft(c.expires_at) : null;
            const u = d != null ? urgency(d, t) : null;
            return (
              <div key={c.probe_id} className={`cx-card ${u?.cls ?? ""}`}>
                <div className="cx-top">
                  <span className="cx-days">{u?.label ?? "—"}</span>
                  {c.expires_at && (
                    <span className="cx-date">{new Date(c.expires_at).toLocaleDateString(lang)}</span>
                  )}
                </div>
                <div className="cx-name">{c.probe_name} · {c.server_name}</div>
                <div className="cx-host">{c.server_host}</div>
                <div className="cx-foot">
                  {c.issuer && <span className="cx-issuer" title={c.subject ?? undefined}>🔒 {c.issuer}</span>}
                  <span className="cx-loc">{c.service_name} · {c.page_title}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
