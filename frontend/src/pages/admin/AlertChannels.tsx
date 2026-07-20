import { useEffect, useMemo, useState } from "react";
import {
  api,
  type AlertChannel,
  type Page,
  previewChannel,
  sendTestAlert,
  type TelegramChat,
  telegramChats,
  testChannel,
} from "../../api/client";
import { PencilIcon, RefreshIcon, TrashIcon } from "../../components/Icons";
import { Modal } from "../../components/Modal";
import { Skeleton } from "../../components/Skeleton";
import { useConfirm } from "../../confirm";
import { relativeTime, useI18n } from "../../i18n";
import { useToast } from "../../toast";
import { AdminNav } from "./AdminNav";

type ChannelType = "telegram" | "webhook" | "email";

const EVENT_TYPES = ["opened", "ongoing", "escalated", "resolved", "ip_changed", "cert_changed"] as const;

const TYPE_META: Record<ChannelType, { icon: string; label: string }> = {
  telegram: { icon: "✈", label: "Telegram" },
  webhook: { icon: "🔗", label: "Webhook" },
  email: { icon: "✉", label: "Email" },
};

export function AlertChannels() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const confirm = useConfirm();
  const [channels, setChannels] = useState<AlertChannel[]>([]);
  const [pages, setPages] = useState<Page[]>([]);
  const [loaded, setLoaded] = useState(false);
  // null = closed, "new" = create, AlertChannel = edit
  const [editing, setEditing] = useState<AlertChannel | "new" | null>(null);

  const load = () =>
    api.get<AlertChannel[]>("/admin/alert-channels").then(({ data }) => {
      setChannels(data);
      setLoaded(true);
    });

  useEffect(() => {
    void load();
    api.get<Page[]>("/admin/pages").then(({ data }) => setPages(data));
  }, []);

  const pageName = useMemo(() => {
    const m = new Map(pages.map((p) => [p.id, p.title]));
    return (id: number | null) => (id == null ? t("alerts.allPages") : m.get(id) ?? `#${id}`);
  }, [pages, t]);

  const eventSummary = (ch: AlertChannel): string => {
    const evs = (ch.config as Record<string, any>).events;
    if (!Array.isArray(evs) || evs.length === 0) return t("alerts.allEvents");
    return evs.map((e: string) => t(`alerts.event.${e}`)).join(", ");
  };

  const destination = (ch: AlertChannel): string => {
    const c = ch.config as Record<string, any>;
    if (ch.type === "telegram") return c.chat_id ? `chat ${c.chat_id}` : "";
    if (ch.type === "webhook") return c.url ?? "";
    return c.to ?? "";
  };

  const toggle = async (ch: AlertChannel) => {
    await api.patch(`/admin/alert-channels/${ch.id}`, { enabled: !ch.enabled });
    load();
  };

  const remove = async (ch: AlertChannel) => {
    if (!(await confirm({ message: t("alerts.confirmDelete"), danger: true }))) return;
    await api.delete(`/admin/alert-channels/${ch.id}`);
    toast.success(t("toast.deleted"));
    load();
  };

  return (
    <div className="container fade-in">
      <AdminNav />

      <div className="toolbar">
        <button onClick={() => setEditing("new")}>+ {t("alerts.add")}</button>
        <span className="muted small">{t("alerts.intro")}</span>
      </div>

      {!loaded && (
        <div className="card">
          {[0, 1, 2].map((i) => (
            <div key={i} className="incident-row">
              <Skeleton w={`${30 + i * 10}%`} h={14} />
              <span className="spacer" />
              <Skeleton w={70} h={22} r={7} />
            </div>
          ))}
        </div>
      )}

      {loaded && channels.length === 0 && (
        <div className="card empty">
          <span className="e-emoji">🔔</span>
          {t("alerts.empty")}
          <div className="hint" style={{ marginTop: 6 }}>{t("alerts.emptyHint")}</div>
        </div>
      )}

      {loaded && channels.length > 0 && (
        <div className="card">
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("alerts.name")}</th>
                  <th>{t("alerts.scope")}</th>
                  <th>{t("alerts.events")}</th>
                  <th>{t("alerts.delivery")}</th>
                  <th>{t("alerts.enabled")}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {channels.map((ch) => (
                  <tr key={ch.id}>
                    <td>
                      <div className="stack">
                        <span className="inline" style={{ gap: 8, flexWrap: "wrap" }}>
                          <span className="pill" title={TYPE_META[ch.type].label}>
                            {TYPE_META[ch.type].icon} {TYPE_META[ch.type].label}
                          </span>
                          <b>{ch.name}</b>
                          {ch.escalate_after_min > 0 && (
                            <span className="pill" title={t("alerts.escalateAfter")}>
                              ↑ {ch.escalate_after_min}′
                            </span>
                          )}
                        </span>
                        {destination(ch) && (
                          <span className="muted small srv-note" title={destination(ch)}>
                            {destination(ch)}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="muted">{pageName(ch.page_id)}</td>
                    <td className="muted small">{eventSummary(ch)}</td>
                    <td className="small">
                      {ch.last_sent_at ? (
                        <span
                          className="inline"
                          style={{ gap: 6 }}
                          title={ch.last_error ?? undefined}
                        >
                          <span className={`dot ${ch.last_ok ? "up" : "down"}`} />
                          {t(ch.last_ok ? "alerts.deliveredOk" : "alerts.deliveredFail", {
                            time: relativeTime(ch.last_sent_at, lang),
                          })}
                        </span>
                      ) : (
                        <span className="faint">{t("alerts.neverSent")}</span>
                      )}
                    </td>
                    <td>
                      <button
                        className="secondary btn-sm"
                        onClick={() => toggle(ch)}
                        title={ch.enabled ? t("alerts.on") : t("alerts.off")}
                      >
                        <span className={`dot ${ch.enabled ? "up" : "paused"}`} />
                        {ch.enabled ? t("alerts.on") : t("alerts.off")}
                      </button>
                    </td>
                    <td>
                      <div className="row-actions" style={{ justifyContent: "flex-end" }}>
                        <button
                          className="icon-btn"
                          title={t("edit")}
                          aria-label={t("edit")}
                          onClick={() => setEditing(ch)}
                        >
                          <PencilIcon />
                        </button>
                        <button
                          className="icon-btn danger"
                          title={t("delete")}
                          aria-label={t("delete")}
                          onClick={() => remove(ch)}
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {editing && (
        <Modal
          title={editing === "new" ? t("alerts.add") : t("alerts.editTitle")}
          onClose={() => setEditing(null)}
        >
          <ChannelForm
            channel={editing === "new" ? undefined : editing}
            pages={pages}
            onSaved={() => {
              setEditing(null);
              load();
            }}
            onCancel={() => setEditing(null)}
          />
        </Modal>
      )}
    </div>
  );
}

function ChannelForm({
  channel,
  pages,
  onSaved,
  onCancel,
}: {
  channel?: AlertChannel;
  pages: Page[];
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const editing = channel != null;
  const cfg = (channel?.config ?? {}) as Record<string, any>;

  const [type, setType] = useState<ChannelType>(channel?.type ?? "telegram");
  const [name, setName] = useState(channel?.name ?? "");
  // telegram — token left blank on edit means "keep the stored one"
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState<string>(cfg.chat_id ?? "");
  const [proxy, setProxy] = useState<string>(cfg.proxy ?? "");
  // webhook
  const [url, setUrl] = useState<string>(cfg.url ?? "");
  const [format, setFormat] = useState<string>(cfg.format ?? "generic");
  // email — password blank on edit means "keep the stored one"
  const [smtpHost, setSmtpHost] = useState<string>(cfg.smtp_host ?? "");
  const [smtpPort, setSmtpPort] = useState<string>(String(cfg.smtp_port ?? 587));
  const [smtpUser, setSmtpUser] = useState<string>(cfg.username ?? "");
  const [smtpPass, setSmtpPass] = useState("");
  const [from, setFrom] = useState<string>(cfg.from ?? "");
  const [to, setTo] = useState<string>(cfg.to ?? "");
  // routing & tuning
  const [pageId, setPageId] = useState<string>(channel?.page_id == null ? "" : String(channel.page_id));
  const [escalate, setEscalate] = useState<string>(String(channel?.escalate_after_min ?? 0));
  const [events, setEvents] = useState<string[]>(Array.isArray(cfg.events) ? cfg.events : []);
  const [template, setTemplate] = useState<string>(cfg.template ?? "");

  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [sendingAlert, setSendingAlert] = useState(false);
  // telegram chat_id discovery (getUpdates helper)
  const [chats, setChats] = useState<TelegramChat[] | null>(null);
  const [pickingChat, setPickingChat] = useState(false);
  // live message preview (debounced on type + template)
  const [preview, setPreview] = useState<{ text: string; is_html: boolean } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const id = setTimeout(() => {
      previewChannel({ type, config: { template } })
        .then((p) => !cancelled && setPreview(p))
        .catch(() => !cancelled && setPreview(null));
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(id);
    };
  }, [type, template]);

  const toggleEvent = (ev: string) =>
    setEvents((s) => (s.includes(ev) ? s.filter((x) => x !== ev) : [...s, ev]));

  // Build the type-specific config, preserving stored keys/secrets when editing.
  const buildConfig = (): Record<string, any> => {
    const base: Record<string, any> = editing ? { ...cfg } : {};
    if (type === "telegram") {
      base.bot_token = botToken; // blank → backend keeps the stored token
      base.chat_id = chatId;
      if (proxy.trim()) base.proxy = proxy.trim();
      else delete base.proxy;
    } else if (type === "webhook") {
      base.url = url;
      if (format && format !== "generic") base.format = format;
      else delete base.format;
    } else {
      base.smtp_host = smtpHost;
      base.smtp_port = Number(smtpPort) || 587;
      base.from = from;
      base.to = to;
      if (smtpUser.trim()) base.username = smtpUser.trim();
      else delete base.username;
      if (smtpPass) base.password = smtpPass; // blank → backend keeps the stored secret
    }
    if (events.length) base.events = events;
    else delete base.events;
    if (template.trim()) base.template = template;
    else delete base.template;
    return base;
  };

  // --- inline validation ---
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  // a secret left blank while editing means "keep the stored one" — not missing
  const tokenMissing = type === "telegram" && !editing && !botToken.trim();
  const chatMissing = type === "telegram" && !chatId.trim();
  const urlMissing = type === "webhook" && !url.trim();
  const urlBad = type === "webhook" && url.trim() !== "" && !/^https?:\/\//i.test(url.trim());
  const hostMissing = type === "email" && !smtpHost.trim();
  const fromMissing = type === "email" && !from.trim();
  const fromBad = type === "email" && from.trim() !== "" && !EMAIL_RE.test(from.trim());
  const toMissing = type === "email" && !to.trim();
  const toBad = type === "email" && to.trim() !== "" && !EMAIL_RE.test(to.trim());
  const formInvalid =
    tokenMissing || chatMissing || urlMissing || urlBad ||
    hostMissing || fromMissing || fromBad || toMissing || toBad;

  // surface "required" messages once a field has been left, format errors always
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const touch = (k: string) => () => setTouched((s) => ({ ...s, [k]: true }));
  const reqMsg = (k: string, missing: boolean) =>
    touched[k] && missing ? <div className="field-msg err">{t("valid.required")}</div> : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (formInvalid) return;
    setBusy(true);
    try {
      const payload = {
        name: name || type,
        config: buildConfig(),
        page_id: pageId ? Number(pageId) : null,
        escalate_after_min: Number(escalate) || 0,
      };
      if (editing) await api.patch(`/admin/alert-channels/${channel!.id}`, payload);
      else await api.post("/admin/alert-channels", { ...payload, type });
      toast.success(t("toast.saved"));
      onSaved();
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setBusy(false);
    }
  };

  const testBody = () => ({
    type,
    ...(editing ? { channel_id: channel!.id } : {}),
    config: buildConfig(),
  });

  const runTest = async () => {
    setTesting(true);
    try {
      const res = await testChannel(testBody());
      toast.show(res.detail, res.ok ? "success" : "error");
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setTesting(false);
    }
  };

  const runSendAlert = async () => {
    setSendingAlert(true);
    try {
      const res = await sendTestAlert(testBody());
      toast.show(res.detail, res.ok ? "success" : "error");
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setSendingAlert(false);
    }
  };

  const pickChat = async () => {
    setPickingChat(true);
    try {
      const res = await telegramChats({
        ...(editing ? { channel_id: channel!.id } : {}),
        config: { bot_token: botToken, proxy: proxy.trim() },
      });
      if (!res.ok) {
        toast.error(res.detail || t("toast.error"));
      } else if (!res.chats || res.chats.length === 0) {
        toast.show(t("alerts.noChats"), "info");
      } else if (res.chats.length === 1) {
        setChatId(String(res.chats[0].id));
        setChats(null);
      } else {
        setChats(res.chats);
      }
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setPickingChat(false);
    }
  };

  return (
    <form onSubmit={submit} className="form-grid">
      <div>
        <label>{t("alerts.type")}</label>
        <select
          value={type}
          disabled={editing}
          onChange={(e) => setType(e.target.value as ChannelType)}
        >
          <option value="telegram">Telegram</option>
          <option value="webhook">Webhook</option>
          <option value="email">Email</option>
        </select>
      </div>
      <div>
        <label>{t("alerts.name")}</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("optional")} />
      </div>

      {type === "telegram" && (
        <>
          <div>
            <label>{t("alerts.botToken")}</label>
            <input
              value={botToken}
              className={touched.botToken && tokenMissing ? "invalid" : ""}
              placeholder={editing ? "••••••" : ""}
              onBlur={touch("botToken")}
              onChange={(e) => setBotToken(e.target.value)}
            />
            {reqMsg("botToken", tokenMissing)}
          </div>
          <div>
            <label>{t("alerts.chatId")}</label>
            <input
              value={chatId}
              className={touched.chatId && chatMissing ? "invalid" : ""}
              onBlur={touch("chatId")}
              onChange={(e) => setChatId(e.target.value)}
            />
            {reqMsg("chatId", chatMissing)}
            <button
              type="button"
              className="ghost btn-sm"
              style={{ marginTop: 6 }}
              onClick={pickChat}
              disabled={pickingChat || (!editing && !botToken.trim())}
            >
              {pickingChat ? t("alerts.pickingChat") : t("alerts.pickChat")}
            </button>
          </div>
          <div className="full">
            <label>{t("alerts.proxy")}</label>
            <input
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="socks5://user:pass@host:1080"
            />
          </div>
          {chats && chats.length > 0 && (
            <div className="full">
              <div className="hint">{t("alerts.pickChatHint")}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
                {chats.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    className="secondary btn-sm"
                    onClick={() => {
                      setChatId(String(c.id));
                      setChats(null);
                    }}
                  >
                    {c.title} <span className="faint">#{c.id}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {type === "webhook" && (
        <>
          <div className="full">
            <label>{t("alerts.webhookUrl")}</label>
            <input
              value={url}
              className={urlBad || (touched.url && urlMissing) ? "invalid" : ""}
              onBlur={touch("url")}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://hooks…"
            />
            {urlBad ? <div className="field-msg err">{t("valid.url")}</div> : reqMsg("url", urlMissing)}
          </div>
          <div className="full">
            <label>{t("alerts.webhookFormat")}</label>
            <select value={format} onChange={(e) => setFormat(e.target.value)}>
              <option value="generic">{t("alerts.format.generic")}</option>
              <option value="slack">Slack</option>
              <option value="discord">Discord</option>
              <option value="mattermost">Mattermost</option>
            </select>
            <div className="hint">{t("alerts.webhookFormatHint")}</div>
          </div>
        </>
      )}

      {type === "email" && (
        <>
          <div>
            <label>{t("alerts.smtpHost")}</label>
            <input
              value={smtpHost}
              className={touched.smtpHost && hostMissing ? "invalid" : ""}
              onBlur={touch("smtpHost")}
              onChange={(e) => setSmtpHost(e.target.value)}
              placeholder="smtp.example.com"
            />
            {reqMsg("smtpHost", hostMissing)}
          </div>
          <div>
            <label>{t("alerts.smtpPort")}</label>
            <input value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} />
          </div>
          <div>
            <label>{t("alerts.from")}</label>
            <input
              value={from}
              className={fromBad || (touched.from && fromMissing) ? "invalid" : ""}
              onBlur={touch("from")}
              onChange={(e) => setFrom(e.target.value)}
              placeholder="mon@example.com"
            />
            {fromBad ? <div className="field-msg err">{t("valid.email")}</div> : reqMsg("from", fromMissing)}
          </div>
          <div>
            <label>{t("alerts.to")}</label>
            <input
              value={to}
              className={toBad || (touched.to && toMissing) ? "invalid" : ""}
              onBlur={touch("to")}
              onChange={(e) => setTo(e.target.value)}
              placeholder="ops@example.com"
            />
            {toBad ? <div className="field-msg err">{t("valid.email")}</div> : reqMsg("to", toMissing)}
          </div>
          <div>
            <label>{t("alerts.smtpUser")}</label>
            <input value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} placeholder={t("optional")} />
          </div>
          <div>
            <label>{t("alerts.smtpPass")}</label>
            <input
              type="password"
              value={smtpPass}
              placeholder={editing ? "••••••" : t("optional")}
              onChange={(e) => setSmtpPass(e.target.value)}
            />
          </div>
        </>
      )}

      <div className="hint full">
        {type === "telegram"
          ? t("alerts.hintTelegram")
          : type === "webhook"
          ? t("alerts.hintWebhook")
          : t("alerts.hintEmail")}
      </div>

      <details className="full advanced" open={editing && (pageId !== "" || Number(escalate) > 0 || events.length > 0 || !!template)}>
        <summary>{t("alerts.advanced")}</summary>
        <div className="form-grid" style={{ marginTop: 10 }}>
          <div>
            <label>{t("alerts.scope")}</label>
            <select value={pageId} onChange={(e) => setPageId(e.target.value)}>
              <option value="">{t("alerts.allPages")}</option>
              {pages.map((p) => (
                <option key={p.id} value={p.id}>{p.title}</option>
              ))}
            </select>
          </div>
          <div>
            <label>{t("alerts.escalateAfter")}</label>
            <input type="number" min={0} value={escalate} onChange={(e) => setEscalate(e.target.value)} />
          </div>
          <div className="full">
            <label>{t("alerts.events")}</label>
            <div className="row" style={{ marginTop: 2 }}>
              {EVENT_TYPES.map((ev) => (
                <div key={ev} className="check-cell">
                  <label>
                    <input type="checkbox" checked={events.includes(ev)} onChange={() => toggleEvent(ev)} />
                    {t(`alerts.event.${ev}`)}
                  </label>
                </div>
              ))}
            </div>
            <div className="hint">{t("alerts.eventsHint")}</div>
          </div>
          <div className="full">
            <label>{t("alerts.template")}</label>
            <textarea
              value={template}
              rows={2}
              onChange={(e) => setTemplate(e.target.value)}
              placeholder={"{verb}: {probe} на {host}\nстатус: {status}"}
            />
            <div className="hint">{t("alerts.templateHint")}</div>
          </div>
          <div className="full">
            <label>{t("alerts.preview")}</label>
            <div className="alert-preview">
              {preview == null ? (
                <span className="faint">…</span>
              ) : preview.is_html ? (
                <span dangerouslySetInnerHTML={{ __html: preview.text }} />
              ) : (
                preview.text
              )}
            </div>
          </div>
        </div>
      </details>

      <div className="form-actions">
        <button type="button" className="secondary btn-sm" onClick={runTest} disabled={testing}>
          <RefreshIcon size={14} /> {testing ? t("alerts.testing") : t("alerts.test")}
        </button>
        <button type="button" className="secondary btn-sm" onClick={runSendAlert} disabled={sendingAlert}>
          {sendingAlert ? t("alerts.sendingAlert") : t("alerts.testAlert")}
        </button>
        <span style={{ flex: 1 }} />
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
          {t("cancel")}
        </button>
        <button disabled={busy || formInvalid}>{editing ? t("save") : t("add")}</button>
      </div>
    </form>
  );
}
