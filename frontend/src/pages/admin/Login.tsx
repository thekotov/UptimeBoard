import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login, setToken } from "../../api/client";
import { LangSwitch } from "../../components/LangSwitch";
import { useI18n } from "../../i18n";

export function Login() {
  const nav = useNavigate();
  const { t } = useI18n();
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const token = await login(email, password);
      setToken(token);
      nav("/admin");
    } catch {
      setError(t("login.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="container">
      <form className="login-box card fade-in" onSubmit={submit}>
        <div className="card-head">
          <h3>{t("login.title")}</h3>
          <LangSwitch />
        </div>
        <label>{t("login.email")}</label>
        <input value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        <label style={{ marginTop: 12 }}>{t("login.password")}</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <div className="error">{error}</div>}
        <div style={{ marginTop: 18 }}>
          <button disabled={busy} style={{ width: "100%" }}>
            {busy ? t("login.submitting") : t("login.submit")}
          </button>
        </div>
      </form>
    </div>
  );
}
