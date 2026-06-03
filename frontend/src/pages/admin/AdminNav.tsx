import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { changePassword, clearToken, getMe, getWorkerHealth, type WorkerHealth } from "../../api/client";
import { LangSwitch } from "../../components/LangSwitch";
import { Modal } from "../../components/Modal";
import { useI18n } from "../../i18n";
import { ThemeSwitch } from "../../theme";
import { useToast } from "../../toast";

export function AdminNav() {
  const nav = useNavigate();
  const { t } = useI18n();
  const [weak, setWeak] = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  const [worker, setWorker] = useState<WorkerHealth | null>(null);

  useEffect(() => {
    getMe().then((m) => setWeak(m.password_is_weak)).catch(() => undefined);
    const poll = () => getWorkerHealth().then(setWorker).catch(() => setWorker(null));
    poll();
    const id = setInterval(poll, 20000);
    return () => clearInterval(id);
  }, []);

  const workerOk = worker?.status === "ok";
  const workerTitle = !worker
    ? t("worker.unknown")
    : workerOk
    ? t("worker.ok")
    : t("worker.stale", { n: worker.last_beat_age_sec == null ? "∞" : Math.round(worker.last_beat_age_sec) });

  const logout = () => {
    clearToken();
    nav("/admin/login");
  };

  return (
    <>
      <div className="header">
        <h1>{t("nav.admin")}</h1>
        <div className="inline">
          <div className="nav-links">
            <NavLink to="/admin" end>
              {t("nav.pages")}
            </NavLink>
            <NavLink to="/admin/alerts">{t("nav.alerts")}</NavLink>
          </div>
          <span className={`worker-pill ${workerOk ? "ok" : "bad"}`} title={workerTitle}>
            <span className={`dot ${workerOk ? "up" : "down"}`} />
            {workerOk ? t("worker.ok") : t("worker.down")}
          </span>
          <ThemeSwitch />
          <LangSwitch />
          <button className="ghost" onClick={() => setShowPwd(true)} title={t("auth.changePassword")}>
            🔑
          </button>
          <button className="ghost" onClick={logout}>
            {t("nav.logout")}
          </button>
        </div>
      </div>

      {weak && (
        <div className="maint-banner" style={{ background: "var(--down-soft)", color: "var(--down-text)", borderColor: "var(--down)" }}>
          ⚠ {t("auth.weakWarning")}
          <span className="spacer" />
          <button className="secondary btn-sm" onClick={() => setShowPwd(true)}>
            {t("auth.changePassword")}
          </button>
        </div>
      )}

      {showPwd && (
        <Modal title={t("auth.changePassword")} onClose={() => setShowPwd(false)}>
          <ChangePasswordForm
            onDone={() => {
              setShowPwd(false);
              setWeak(false);
            }}
            onCancel={() => setShowPwd(false)}
          />
        </Modal>
      )}
    </>
  );
}

function ChangePasswordForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next !== repeat) {
      setError(t("auth.mismatch"));
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      toast.success(t("auth.changed"));
      onDone();
    } catch (err: any) {
      setError(err.response?.data?.detail ?? t("toast.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="form-grid">
      <div className="full">
        <label>{t("auth.current")}</label>
        <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoFocus />
      </div>
      <div className="full">
        <label>{t("auth.new")}</label>
        <input type="password" value={next} onChange={(e) => setNext(e.target.value)} />
      </div>
      <div className="full">
        <label>{t("auth.newRepeat")}</label>
        <input type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
      </div>
      {error && <div className="error full">{error}</div>}
      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
          {t("cancel")}
        </button>
        <button disabled={busy}>{t("save")}</button>
      </div>
    </form>
  );
}
