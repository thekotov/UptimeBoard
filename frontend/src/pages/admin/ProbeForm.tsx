import { useState } from "react";
import { api, type CheckResult, type Probe, type ProbeType, testProbe } from "../../api/client";
import { RefreshIcon } from "../../components/Icons";
import { classifyError } from "../../errors";
import { useI18n } from "../../i18n";
import { useToast } from "../../toast";

const num = (v: unknown, fallback: number): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};

const headersToText = (h: unknown): string =>
  h && typeof h === "object"
    ? Object.entries(h as Record<string, string>).map(([k, v]) => `${k}: ${v}`).join("\n")
    : "";

const textToHeaders = (s: string): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const line of s.split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
};

export function ProbeForm({
  serverId,
  probe,
  pageDefaultTolerance,
  onSaved,
  onCancel,
}: {
  serverId?: number;
  probe?: Probe;
  pageDefaultTolerance?: number;
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const editing = probe != null;
  const cfg = (probe?.config ?? {}) as Record<string, any>;

  const [type, setType] = useState<ProbeType>(probe?.type ?? "http");
  const [name, setName] = useState(probe?.name ?? "");
  const [interval, setInterval] = useState(probe?.interval_sec ?? 60);
  const [timeout, setTimeoutSec] = useState(probe?.timeout_sec ?? 10);
  const [enabled, setEnabled] = useState(probe?.enabled ?? true);
  const [failureThreshold, setFailureThreshold] = useState(probe?.failure_threshold ?? 2);
  const [latencyDegraded, setLatencyDegraded] = useState<number | "">(probe?.latency_degraded_ms ?? "");
  const [degradedThreshold, setDegradedThreshold] = useState(probe?.degraded_threshold ?? 1);
  const [downThreshold, setDownThreshold] = useState(probe?.down_threshold ?? 2);
  const [toleranceChecks, setToleranceChecks] = useState(probe?.tolerance_checks ?? pageDefaultTolerance ?? 1);
  const [recoveryThreshold, setRecoveryThreshold] = useState(probe?.recovery_threshold ?? 2);
  const [retries, setRetries] = useState(probe?.retries ?? 2);
  // http
  const [url, setUrl] = useState(cfg.url ?? "");
  const [method, setMethod] = useState<string>(cfg.method ?? "GET");
  const [reqBody, setReqBody] = useState(cfg.body ?? "");
  const [contentType, setContentType] = useState(cfg.content_type ?? "");
  const [expectedStatus, setExpectedStatus] = useState(num(cfg.expected_status, 200));
  const [bodySubstr, setBodySubstr] = useState(cfg.expected_body_substr ?? "");
  const [followRedirects, setFollowRedirects] = useState<boolean>(cfg.follow_redirects ?? true);
  const [headers, setHeaders] = useState(headersToText(cfg.headers));
  const [basicUser, setBasicUser] = useState(cfg.basic_user ?? "");
  const [basicPass, setBasicPass] = useState(cfg.basic_pass ?? "");
  const [bearer, setBearer] = useState(cfg.bearer_token ?? "");
  const [bodyRegex, setBodyRegex] = useState(cfg.expected_body_regex ?? "");
  const [jsonPath, setJsonPath] = useState(cfg.json_path ?? "");
  const [jsonExpected, setJsonExpected] = useState(cfg.json_expected ?? "");
  const [checkCert, setCheckCert] = useState<boolean>(cfg.check_cert ?? false);
  const [trackIp, setTrackIp] = useState<boolean>(cfg.track_ip ?? false);
  const [trackContent, setTrackContent] = useState<boolean>(cfg.track_content ?? false);
  const [trackCertChange, setTrackCertChange] = useState<boolean>(cfg.track_cert_change ?? false);
  const [certExpiryReminders, setCertExpiryReminders] = useState<boolean>(cfg.cert_expiry_reminders ?? false);
  // tcp / tls / icmp / heartbeat
  const [port, setPort] = useState(num(cfg.port, 443));
  const [count, setCount] = useState(num(cfg.count, 3));
  const [packetSize, setPacketSize] = useState(num(cfg.packet_size, 56));
  const [warnDays, setWarnDays] = useState(num(cfg.warn_days, 14));
  const [grace, setGrace] = useState(num(cfg.grace_sec, 60));
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testRes, setTestRes] = useState<CheckResult | null>(null);
  // open the detection-tuning section by default only when editing a probe
  // whose tuning differs from the defaults, so it isn't lost behind a collapsed panel
  const [tuningOpen, setTuningOpen] = useState(
    editing && (
      (probe?.failure_threshold ?? 2) !== 2 ||
      (probe?.degraded_threshold ?? 1) !== 1 ||
      (probe?.down_threshold ?? 2) !== 2 ||
      (probe?.tolerance_checks ?? (pageDefaultTolerance ?? 1)) !== (pageDefaultTolerance ?? 1) ||
      (probe?.recovery_threshold ?? 2) !== 2 ||
      (probe?.retries ?? 2) !== 2 ||
      probe?.latency_degraded_ms != null
    )
  );

  const buildConfig = (): Record<string, unknown> => {
    if (type === "http") {
      const c: Record<string, unknown> = {
        url, method, expected_status: Number(expectedStatus), follow_redirects: followRedirects,
      };
      // Request body for write methods; empty string clears a previously-set body.
      if (method === "POST" || method === "PUT" || method === "PATCH") {
        c.body = reqBody;
        if (contentType.trim()) c.content_type = contentType.trim();
      }
      if (bodySubstr.trim()) c.expected_body_substr = bodySubstr.trim();
      if (bodyRegex.trim()) c.expected_body_regex = bodyRegex.trim();
      const h = textToHeaders(headers);
      if (Object.keys(h).length) c.headers = h;
      if (basicUser.trim()) { c.basic_user = basicUser.trim(); c.basic_pass = basicPass; }
      if (bearer.trim()) c.bearer_token = bearer.trim();
      if (jsonPath.trim()) { c.json_path = jsonPath.trim(); if (jsonExpected !== "") c.json_expected = jsonExpected; }
      // SSL certificate tracking — only meaningful for https endpoints. Always
      // emit the flag so toggling it off persists when editing (config is merged).
      const isHttps = /^https:\/\//i.test(url.trim());
      c.check_cert = isHttps && checkCert;
      if (c.check_cert) c.warn_days = Number(warnDays);
      // Cert-related tracking only makes sense when the certificate is fetched.
      c.track_cert_change = Boolean(c.check_cert) && trackCertChange;
      c.cert_expiry_reminders = Boolean(c.check_cert) && certExpiryReminders;
      // Alert when the URL host's resolved IP set / page content changes. Always
      // emit the flags so toggling them off persists on edit (config is merged).
      c.track_ip = trackIp;
      c.track_content = trackContent;
      return c;
    }
    if (type === "tcp") return { port: Number(port) };
    if (type === "tls") return { port: Number(port), warn_days: Number(warnDays), track_cert_change: trackCertChange, cert_expiry_reminders: certExpiryReminders };
    if (type === "heartbeat") return { ...cfg, grace_sec: Number(grace) }; // keep token
    return { count: Number(count), packet_size: Number(packetSize) };
  };

  const commonPayload = () => ({
    name: name || type.toUpperCase(),
    interval_sec: Number(interval),
    timeout_sec: Number(timeout),
    enabled,
    failure_threshold: Number(failureThreshold),
    latency_degraded_ms: latencyDegraded === "" ? null : Number(latencyDegraded),
    degraded_threshold: Number(degradedThreshold),
    down_threshold: Number(downThreshold),
    tolerance_checks: Number(toleranceChecks),
    recovery_threshold: Number(recoveryThreshold),
    retries: Number(retries),
    config: editing ? { ...cfg, ...buildConfig() } : buildConfig(),
  });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (editing) await api.patch(`/admin/probes/${probe!.id}`, commonPayload());
      else await api.post("/admin/probes", { server_id: serverId, type, ...commonPayload() });
      toast.success(t("toast.saved"));
      onSaved();
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTestRes(null);
    try {
      const res = await testProbe({
        type, server_id: editing ? probe!.server_id : serverId,
        timeout_sec: Number(timeout), config: buildConfig(),
      });
      setTestRes(res);
    } catch {
      toast.error(t("probe.checkFail"));
    } finally {
      setTesting(false);
    }
  };

  const hasLatency = type === "http" || type === "tcp" || type === "tls";
  const pingUrl = cfg.token ? `${location.origin}/api/ping/${cfg.token}` : null;

  // inline validation
  const urlInvalid = type === "http" && url.trim() !== "" && !/^https?:\/\//i.test(url.trim());
  const portInvalid = (type === "tcp" || type === "tls") && !(port >= 1 && port <= 65535);
  const formInvalid = urlInvalid || portInvalid;

  return (
    <form onSubmit={submit} className="form-grid">
      <div>
        <label>{t("probe.type")}</label>
        <select value={type} onChange={(e) => setType(e.target.value as ProbeType)}
          disabled={editing} title={editing ? t("probe.typeLocked") : undefined}>
          <option value="http">HTTP</option>
          <option value="tcp">TCP</option>
          <option value="icmp">ICMP</option>
          <option value="tls">TLS</option>
          <option value="heartbeat">{t("probe.heartbeat")}</option>
        </select>
      </div>
      <div>
        <label>{t("probe.name")}</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("optional")} />
      </div>

      {type === "http" && (
        <>
          <div className="full">
            <label>{t("probe.url")}</label>
            <input className={urlInvalid ? "invalid" : ""} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://host/health" />
            {urlInvalid && <div className="field-msg err">{t("valid.url")}</div>}
          </div>
          <div>
            <label>{t("probe.method")}</label>
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="GET">GET</option>
              <option value="HEAD">HEAD</option>
              <option value="POST">POST</option>
            </select>
          </div>
          <div>
            <label>{t("probe.expected")}</label>
            <input type="number" value={expectedStatus} onChange={(e) => setExpectedStatus(+e.target.value)} />
          </div>
          {(method === "POST" || method === "PUT" || method === "PATCH") && (
            <>
              <div className="full">
                <label>{t("probe.reqBody")}</label>
                <textarea rows={3} value={reqBody} onChange={(e) => setReqBody(e.target.value)}
                  placeholder={'{"key": "value"}'} />
              </div>
              <div className="full">
                <label>{t("probe.contentType")}</label>
                <input value={contentType} onChange={(e) => setContentType(e.target.value)}
                  placeholder="application/json" />
              </div>
            </>
          )}
          {/^https:\/\//i.test(url.trim()) && (
            <>
              <label className="check-cell full">
                <input type="checkbox" checked={checkCert} onChange={(e) => setCheckCert(e.target.checked)} /> 🔒 {t("probe.checkCert")}
              </label>
              {checkCert && (
                <div>
                  <label>{t("probe.warnDays")}</label>
                  <input type="number" min={1} value={warnDays} onChange={(e) => setWarnDays(+e.target.value)} />
                </div>
              )}
              {checkCert && (
                <label className="check-cell full">
                  <input type="checkbox" checked={trackCertChange} onChange={(e) => setTrackCertChange(e.target.checked)} /> 📜 {t("probe.trackCertChange")}
                </label>
              )}
              {checkCert && trackCertChange && (
                <div className="full">
                  <span className="hint">{t("probe.trackCertChangeHint")}</span>
                </div>
              )}
              {checkCert && (
                <label className="check-cell full">
                  <input type="checkbox" checked={certExpiryReminders} onChange={(e) => setCertExpiryReminders(e.target.checked)} /> ⏳ {t("probe.certExpiryReminders")}
                </label>
              )}
              {checkCert && certExpiryReminders && (
                <div className="full">
                  <span className="hint">{t("probe.certExpiryRemindersHint")}</span>
                </div>
              )}
            </>
          )}
          <label className="check-cell full">
            <input type="checkbox" checked={trackIp} onChange={(e) => setTrackIp(e.target.checked)} /> 🔄 {t("probe.trackIp")}
          </label>
          {trackIp && (
            <div className="full">
              <span className="hint">
                {t("probe.trackIpHint")}
                {editing && probe?.last_ip ? ` ${t("probe.trackIpCurrent")}: ${probe.last_ip}` : ""}
              </span>
            </div>
          )}
          <label className="check-cell full">
            <input type="checkbox" checked={trackContent} onChange={(e) => setTrackContent(e.target.checked)} /> 📝 {t("probe.trackContent")}
          </label>
          {trackContent && (
            <div className="full">
              <span className="hint">{t("probe.trackContentHint")}</span>
            </div>
          )}
          <details className="full advanced">
            <summary>{t("probe.advanced")}</summary>
            <div className="form-grid" style={{ marginTop: 10 }}>
              <div className="full">
                <label>{t("probe.body")}</label>
                <input value={bodySubstr} onChange={(e) => setBodySubstr(e.target.value)} placeholder={t("optional")} />
              </div>
              <div className="full">
                <label>{t("probe.bodyRegex")}</label>
                <input value={bodyRegex} onChange={(e) => setBodyRegex(e.target.value)} placeholder={t("optional")} />
              </div>
              <div>
                <label>{t("probe.jsonPath")}</label>
                <input value={jsonPath} onChange={(e) => setJsonPath(e.target.value)} placeholder={t("optional")} />
              </div>
              <div>
                <label>{t("probe.jsonExpected")}</label>
                <input value={jsonExpected} onChange={(e) => setJsonExpected(e.target.value)} placeholder={t("optional")} />
              </div>
              <div className="full">
                <label>{t("probe.headers")}</label>
                <textarea rows={2} value={headers} onChange={(e) => setHeaders(e.target.value)} placeholder="Authorization: Bearer …" />
              </div>
              <div>
                <label>{t("probe.basicUser")}</label>
                <input value={basicUser} onChange={(e) => setBasicUser(e.target.value)} placeholder={t("optional")} />
              </div>
              <div>
                <label>{t("probe.basicPass")}</label>
                <input type="password" value={basicPass} onChange={(e) => setBasicPass(e.target.value)} placeholder={t("optional")} />
              </div>
              <div className="full">
                <label>{t("probe.bearer")}</label>
                <input value={bearer} onChange={(e) => setBearer(e.target.value)} placeholder={t("optional")} />
              </div>
              <label className="check-cell full">
                <input type="checkbox" checked={followRedirects} onChange={(e) => setFollowRedirects(e.target.checked)} /> {t("probe.redirects")}
              </label>
              <div className="full"><span className="hint">{t("probe.redirectsHint")}</span></div>
            </div>
          </details>
        </>
      )}
      {type === "tcp" && (
        <div>
          <label>{t("probe.port")}</label>
          <input type="number" value={port} onChange={(e) => setPort(+e.target.value)} />
        </div>
      )}
      {type === "tls" && (
        <>
          <div>
            <label>{t("probe.port")}</label>
            <input className={portInvalid ? "invalid" : ""} type="number" value={port} onChange={(e) => setPort(+e.target.value)} />
            {portInvalid && <div className="field-msg err">{t("valid.port")}</div>}
          </div>
          <div>
            <label>{t("probe.warnDays")}</label>
            <input type="number" value={warnDays} onChange={(e) => setWarnDays(+e.target.value)} />
          </div>
          <label className="check-cell full">
            <input type="checkbox" checked={trackCertChange} onChange={(e) => setTrackCertChange(e.target.checked)} /> 📜 {t("probe.trackCertChange")}
          </label>
          {trackCertChange && (
            <div className="full">
              <span className="hint">{t("probe.trackCertChangeHint")}</span>
            </div>
          )}
          <label className="check-cell full">
            <input type="checkbox" checked={certExpiryReminders} onChange={(e) => setCertExpiryReminders(e.target.checked)} /> ⏳ {t("probe.certExpiryReminders")}
          </label>
          {certExpiryReminders && (
            <div className="full">
              <span className="hint">{t("probe.certExpiryRemindersHint")}</span>
            </div>
          )}
        </>
      )}
      {type === "icmp" && (
        <>
          <div>
            <label>{t("probe.count")}</label>
            <input type="number" value={count} onChange={(e) => setCount(+e.target.value)} />
          </div>
          <div>
            <label>{t("probe.packetSize")}</label>
            <input type="number" value={packetSize} onChange={(e) => setPacketSize(+e.target.value)} />
          </div>
        </>
      )}
      {type === "heartbeat" && (
        <>
          <div>
            <label>{t("probe.grace")}</label>
            <input type="number" value={grace} onChange={(e) => setGrace(+e.target.value)} />
          </div>
          <div className="full">
            {pingUrl ? (
              <>
                <label>{t("probe.pushUrl")}</label>
                <div className="inline">
                  <input readOnly value={pingUrl} onFocus={(e) => e.currentTarget.select()} />
                  <button type="button" className="secondary btn-sm" onClick={() => {
                    navigator.clipboard?.writeText(pingUrl); toast.success(t("io.copied"));
                  }}>🔗</button>
                </div>
              </>
            ) : (
              <div className="hint">{t("probe.pushHint")}</div>
            )}
          </div>
        </>
      )}

      <div>
        <label>{type === "heartbeat" ? t("probe.period") : t("probe.interval")}</label>
        <input type="number" value={interval} onChange={(e) => setInterval(+e.target.value)} />
      </div>
      {type !== "heartbeat" && (
        <div>
          <label>{t("probe.timeout")}</label>
          <input type="number" value={timeout} onChange={(e) => setTimeoutSec(+e.target.value)} />
        </div>
      )}
      <details className="full advanced" open={tuningOpen}
        onToggle={(e) => setTuningOpen(e.currentTarget.open)}>
        <summary>{t("probe.detection")}</summary>
        <div className="form-grid" style={{ marginTop: 10 }}>
          <div>
            <label>{t("probe.failureThreshold")}</label>
            <input type="number" min={1} value={failureThreshold} onChange={(e) => setFailureThreshold(+e.target.value)} />
            <span className="hint">{t("probe.failureThresholdHint")}</span>
          </div>
          <div>
            <label>{t("probe.degradedThreshold")}</label>
            <input type="number" min={1} value={degradedThreshold} onChange={(e) => setDegradedThreshold(+e.target.value)} />
            <span className="hint">{t("probe.degradedThresholdHint")}</span>
          </div>
          <div>
            <label>{t("probe.downThreshold")}</label>
            <input type="number" min={1} value={downThreshold} onChange={(e) => setDownThreshold(+e.target.value)} />
            <span className="hint">{t("probe.downThresholdHint")}</span>
          </div>
          <div>
            <label>{t("probe.tolerance")}</label>
            <input type="number" min={0} value={toleranceChecks} onChange={(e) => setToleranceChecks(+e.target.value)} />
            <span className="hint">{t("probe.toleranceHint")}</span>
          </div>
          <div>
            <label>{t("probe.recovery")}</label>
            <input type="number" min={1} value={recoveryThreshold} onChange={(e) => setRecoveryThreshold(+e.target.value)} />
            <span className="hint">{t("probe.recoveryHint")}</span>
          </div>
          <div>
            <label>{t("probe.retries")}</label>
            <input type="number" min={0} max={5} value={retries} onChange={(e) => setRetries(+e.target.value)} />
            <span className="hint">{t("probe.retriesHint")}</span>
          </div>
          {hasLatency && (
            <div>
              <label>{t("probe.latencyDegraded")}</label>
              <input type="number" value={latencyDegraded}
                onChange={(e) => setLatencyDegraded(e.target.value === "" ? "" : +e.target.value)} placeholder={t("optional")} />
            </div>
          )}
        </div>
      </details>
      <label className="check-cell full">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {t("probe.enabled")}
      </label>

      <div className="form-actions">
        {type !== "heartbeat" && (
          <button type="button" className="secondary btn-sm" onClick={runTest} disabled={testing}>
            <RefreshIcon size={14} /> {testing ? t("probe.testing") : t("probe.test")}
          </button>
        )}
        <span style={{ flex: 1 }} />
        {onCancel && (
          <button type="button" className="ghost" onClick={onCancel} disabled={busy}>{t("cancel")}</button>
        )}
        <button disabled={busy || formInvalid}>{editing ? t("save") : t("probe.add")}</button>
      </div>

      {testRes && (() => {
        const m = testRes.meta;
        const errKey = classifyError(testRes.error);
        const hint = errKey ? t(errKey) : testRes.error;
        const certDays = m?.expires_at
          ? Math.floor((new Date(m.expires_at).getTime() - Date.now()) / 864e5) : null;
        const gotStatus = m?.http_status;
        const showRedirectWarn = type === "http" && m?.redirected && followRedirects;
        const showStatusHint = type === "http" && !followRedirects && gotStatus != null
          && gotStatus >= 300 && gotStatus < 400 && Number(expectedStatus) !== gotStatus;
        return (
          <div className={`full test-result ${testRes.status === "up" ? "ok" : "bad"}`}>
            <div className="tr-head">
              <span className={`badge ${testRes.status}`}>{t(`status.${testRes.status}`)}</span>
              {testRes.latency_ms != null && (
                <span className="tr-lat">{testRes.latency_ms.toFixed(0)} {t("dash.ms")}</span>
              )}
              {gotStatus != null && <span className="tr-meta">HTTP {gotStatus}</span>}
            </div>
            {testRes.error && testRes.status !== "up" && (
              <div className="tr-err" title={hint !== testRes.error ? testRes.error : undefined}>{hint}</div>
            )}
            {m?.final_url && (m.redirected || type === "http") && (
              <div className="tr-row"><span className="muted small">{t("test.finalUrl")}</span> <code>{m.final_url}</code></div>
            )}
            {certDays != null && (
              <div className="tr-row">🔒 {certDays < 0 ? t("cert.expired") : t("cert.expiresIn", { days: certDays })}
                {m?.issuer ? ` · ${m.issuer}` : ""}</div>
            )}
            {m?.resolved_ips?.length ? <div className="tr-row">🔄 {m.resolved_ips.join(", ")}</div> : null}
            {m?.content_hash && <div className="tr-row">📝 {m.content_hash.slice(0, 16)}…</div>}
            {showRedirectWarn && <div className="tr-warn">⚠ {t("test.redirectedWarn")}</div>}
            {showStatusHint && <div className="tr-warn">⚠ {t("test.expectedHint", { got: gotStatus, exp: expectedStatus })}</div>}
          </div>
        );
      })()}
    </form>
  );
}

export function probeSummary(p: Probe): string {
  if (p.type === "http") return `${p.config.url ?? ""} → ${p.config.expected_status ?? 200}`;
  if (p.type === "tcp") return `port ${p.config.port ?? "?"}`;
  if (p.type === "tls") return `TLS :${p.config.port ?? 443}`;
  if (p.type === "heartbeat") return "push / heartbeat";
  return `${p.config.count ?? 3} ping`;
}
