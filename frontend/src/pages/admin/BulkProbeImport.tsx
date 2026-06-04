import { useMemo, useState } from "react";
import { api } from "../../api/client";
import { useI18n } from "../../i18n";
import { useToast } from "../../toast";

type Parsed = { name: string; type: string; config: Record<string, unknown>; summary: string };
type Row = { line: string; ok?: Parsed; err?: string };

const hostname = (u: string) => {
  try {
    return new URL(u).hostname;
  } catch {
    return u;
  }
};

/** Parse one line into a probe (host = the server's host). Grammar:
 *   https://host/path [name]        → http (200)
 *   http /health [name]             → http on server host
 *   tcp 5432 [name]                 → tcp
 *   tls 443 [name]                  → tls
 *   icmp [name] / heartbeat [name]
 *   5432                            → tcp shorthand
 */
function parseLine(raw: string, host: string): Parsed | { err: string } | null {
  const line = raw.trim();
  if (!line || line.startsWith("#")) return null;

  if (/^https?:\/\//i.test(line)) {
    const [url, ...rest] = line.split(/\s+/);
    return { name: rest.join(" ") || hostname(url), type: "http", config: { url, expected_status: 200 }, summary: `${url} → 200` };
  }
  const p = line.split(/\s+/);
  const type = p[0].toLowerCase();

  if (type === "http") {
    if (!p[1]) return { err: "http: укажите URL или путь" };
    const url = /^https?:\/\//i.test(p[1]) ? p[1] : `https://${host}${p[1].startsWith("/") ? p[1] : "/" + p[1]}`;
    return { name: p.slice(2).join(" ") || hostname(url), type: "http", config: { url, expected_status: 200 }, summary: `${url} → 200` };
  }
  if (type === "tcp" || /^\d+$/.test(type)) {
    const port = type === "tcp" ? Number(p[1]) : Number(type);
    if (!port || port < 1 || port > 65535) return { err: "tcp: неверный порт" };
    const name = (type === "tcp" ? p.slice(2) : p.slice(1)).join(" ") || `TCP ${port}`;
    return { name, type: "tcp", config: { port }, summary: `port ${port}` };
  }
  if (type === "tls") {
    const port = Number(p[1]) || 443;
    return { name: p.slice(2).join(" ") || `TLS ${port}`, type: "tls", config: { port, warn_days: 14 }, summary: `TLS :${port}` };
  }
  if (type === "icmp") {
    return { name: p.slice(1).join(" ") || "ICMP", type: "icmp", config: { count: 3 }, summary: "3 ping" };
  }
  if (type === "heartbeat") {
    return { name: p.slice(1).join(" ") || "Heartbeat", type: "heartbeat", config: { grace_sec: 60 }, summary: "push" };
  }
  return { err: "неизвестный формат строки" };
}

export function BulkProbeImport({
  serverId,
  host,
  defaultTolerance = 0,
  onSaved,
  onCancel,
}: {
  serverId: number;
  host: string;
  defaultTolerance?: number;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const rows: Row[] = useMemo(() => {
    const out: Row[] = [];
    for (const line of text.split("\n")) {
      const r = parseLine(line, host);
      if (r === null) continue;
      out.push("err" in r ? { line, err: r.err } : { line, ok: r });
    }
    return out;
  }, [text, host]);

  const valid = rows.filter((r) => r.ok).map((r) => r.ok!);

  const submit = async () => {
    setBusy(true);
    try {
      await Promise.all(
        valid.map((v) =>
          api.post("/admin/probes", {
            server_id: serverId,
            type: v.type,
            name: v.name,
            config: v.config,
            tolerance_checks: defaultTolerance,
          })
        )
      );
      toast.success(t("toast.saved"));
      onSaved();
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="hint" style={{ marginBottom: 8, whiteSpace: "pre-line" }}>{t("bulk.importHint")}</div>
      <textarea
        rows={6}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={"https://example.com/health\ntcp 443\ntls 443\nicmp ping"}
        style={{ fontFamily: "monospace", fontSize: 13 }}
      />

      {rows.length > 0 && (
        <div style={{ marginTop: 10, maxHeight: 220, overflowY: "auto" }}>
          {rows.map((r, i) => (
            <div className="log-row" key={i}>
              {r.ok ? (
                <>
                  <span className="pill">{r.ok.type}</span>
                  <span>{r.ok.name}</span>
                  <span className="muted small" style={{ marginLeft: "auto" }}>{r.ok.summary}</span>
                </>
              ) : (
                <>
                  <span className="pill" style={{ background: "var(--down-soft)", color: "var(--down-text)" }}>×</span>
                  <span className="muted small">{r.line}</span>
                  <span className="small" style={{ marginLeft: "auto", color: "var(--down-text)" }}>{r.err}</span>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>{t("cancel")}</button>
        <button disabled={busy || valid.length === 0} onClick={submit}>
          {t("bulk.importBtn", { n: valid.length })}
        </button>
      </div>
    </div>
  );
}
