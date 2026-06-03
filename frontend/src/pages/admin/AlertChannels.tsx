import { useEffect, useMemo, useState } from "react";
import { api, type AlertChannel, type Page, testChannel } from "../../api/client";
import { PencilIcon, TrashIcon } from "../../components/Icons";
import { useConfirm } from "../../confirm";
import { useI18n } from "../../i18n";
import { useToast } from "../../toast";
import { AdminNav } from "./AdminNav";

type ChannelType = "telegram" | "webhook" | "email";

const emptyEmail = { smtpHost: "", smtpPort: "587", smtpUser: "", smtpPass: "", from: "", to: "" };

const EVENT_TYPES = ["opened", "ongoing", "escalated", "resolved"] as const;

// Merge the per-channel tuning keys (events filter, custom template) into a
// config object, removing them when empty so we don't store dead keys.
const withTuning = (config: Record<string, any>, events: string[], template: string) => {
  if (events.length) config.events = events;
  else delete config.events;
  if (template.trim()) config.template = template;
  else delete config.template;
  return config;
};

export function AlertChannels() {
  const { t } = useI18n();
  const toast = useToast();
  const confirm = useConfirm();
  const [channels, setChannels] = useState<AlertChannel[]>([]);
  const [pages, setPages] = useState<Page[]>([]);
  const [type, setType] = useState<ChannelType>("telegram");
  const [name, setName] = useState("");
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [proxy, setProxy] = useState("");
  const [url, setUrl] = useState("");
  const [email, setEmail] = useState({ ...emptyEmail });
  const [pageId, setPageId] = useState<string>("");
  const [escalate, setEscalate] = useState<string>("0");
  const [events, setEvents] = useState<string[]>([]);
  const [template, setTemplate] = useState("");
  const [testing, setTesting] = useState(false);

  // edit state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [edit, setEdit] = useState({ name: "", botToken: "", chatId: "", proxy: "", url: "", pageId: "", escalate: "0", events: [] as string[], template: "" });

  const toggleCreateEvent = (ev: string) =>
    setEvents((s) => (s.includes(ev) ? s.filter((x) => x !== ev) : [...s, ev]));
  const toggleEditEvent = (ev: string) =>
    setEdit((s) => ({ ...s, events: s.events.includes(ev) ? s.events.filter((x) => x !== ev) : [...s.events, ev] }));

  const runTest = async (body: { config: Record<string, unknown>; channel_id?: number }) => {
    setTesting(true);
    try {
      const res = await testChannel({ type: "telegram", ...body });
      toast.show(res.detail, res.ok ? "success" : "error");
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setTesting(false);
    }
  };

  const emailConfig = (e: typeof emptyEmail) => ({
    smtp_host: e.smtpHost,
    smtp_port: Number(e.smtpPort) || 587,
    username: e.smtpUser || undefined,
    password: e.smtpPass || undefined,
    from: e.from,
    to: e.to,
  });

  const load = () => api.get<AlertChannel[]>("/admin/alert-channels").then(({ data }) => setChannels(data));
  useEffect(() => {
    void load();
    api.get<Page[]>("/admin/pages").then(({ data }) => setPages(data));
  }, []);

  const pageName = useMemo(() => {
    const m = new Map(pages.map((p) => [p.id, p.title]));
    return (id: number | null) => (id == null ? t("alerts.allPages") : m.get(id) ?? `#${id}`);
  }, [pages, t]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const base =
      type === "telegram"
        ? { bot_token: botToken, chat_id: chatId, ...(proxy.trim() ? { proxy: proxy.trim() } : {}) }
        : type === "webhook"
        ? { url }
        : emailConfig(email);
    const config = withTuning({ ...base }, events, template);
    await api.post("/admin/alert-channels", {
      name: name || type, type, config, page_id: pageId ? Number(pageId) : null,
      escalate_after_min: Number(escalate) || 0,
    });
    toast.success(t("toast.saved"));
    setName("");
    setBotToken("");
    setChatId("");
    setProxy("");
    setUrl("");
    setEmail({ ...emptyEmail });
    setPageId("");
    setEscalate("0");
    setEvents([]);
    setTemplate("");
    load();
  };

  const startEdit = (ch: AlertChannel) => {
    const c = ch.config as Record<string, any>;
    setEdit({
      name: ch.name,
      botToken: "", // secret not returned; blank = keep existing
      chatId: c.chat_id ?? "",
      proxy: c.proxy ?? "",
      url: c.url ?? "",
      pageId: ch.page_id == null ? "" : String(ch.page_id),
      escalate: String(ch.escalate_after_min ?? 0),
      events: Array.isArray(c.events) ? c.events : [],
      template: c.template ?? "",
    });
    setEditingId(ch.id);
  };

  const saveEdit = async (ch: AlertChannel) => {
    let config: Record<string, any>;
    if (ch.type === "telegram") {
      config = { ...(ch.config as object), bot_token: edit.botToken, chat_id: edit.chatId };
      if (edit.proxy.trim()) config.proxy = edit.proxy.trim();
      else delete config.proxy;
    } else if (ch.type === "webhook") {
      config = { ...(ch.config as object), url: edit.url };
    } else {
      config = { ...(ch.config as object) };
    }
    withTuning(config, edit.events, edit.template);
    await api.patch(`/admin/alert-channels/${ch.id}`, {
      name: edit.name || ch.type, config, page_id: edit.pageId ? Number(edit.pageId) : null,
      escalate_after_min: Number(edit.escalate) || 0,
    });
    toast.success(t("toast.saved"));
    setEditingId(null);
    load();
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

      <div className="card">
        <div className="card-head">
          <h3>{t("alerts.add")}</h3>
        </div>
        <form onSubmit={create} className="row">
          <div style={{ flex: "0 0 150px" }}>
            <label>{t("alerts.type")}</label>
            <select value={type} onChange={(e) => setType(e.target.value as ChannelType)}>
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
                <input value={botToken} onChange={(e) => setBotToken(e.target.value)} />
              </div>
              <div>
                <label>{t("alerts.chatId")}</label>
                <input value={chatId} onChange={(e) => setChatId(e.target.value)} />
              </div>
              <div style={{ flex: 2 }}>
                <label>{t("alerts.proxy")}</label>
                <input
                  value={proxy}
                  onChange={(e) => setProxy(e.target.value)}
                  placeholder="socks5://user:pass@host:1080"
                />
              </div>
              <div className="btn-cell">
                <button
                  type="button"
                  className="secondary"
                  disabled={testing}
                  onClick={() => runTest({ config: { bot_token: botToken, proxy: proxy.trim() } })}
                >
                  {testing ? t("alerts.testing") : t("alerts.test")}
                </button>
              </div>
            </>
          )}
          {type === "webhook" && (
            <div style={{ flex: 2 }}>
              <label>{t("alerts.webhookUrl")}</label>
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks…" />
            </div>
          )}
          {type === "email" && (
            <>
              <div>
                <label>{t("alerts.smtpHost")}</label>
                <input value={email.smtpHost} onChange={(e) => setEmail((s) => ({ ...s, smtpHost: e.target.value }))} placeholder="smtp.example.com" />
              </div>
              <div style={{ flex: "0 0 80px" }}>
                <label>{t("alerts.smtpPort")}</label>
                <input value={email.smtpPort} onChange={(e) => setEmail((s) => ({ ...s, smtpPort: e.target.value }))} />
              </div>
              <div>
                <label>{t("alerts.from")}</label>
                <input value={email.from} onChange={(e) => setEmail((s) => ({ ...s, from: e.target.value }))} placeholder="mon@example.com" />
              </div>
              <div>
                <label>{t("alerts.to")}</label>
                <input value={email.to} onChange={(e) => setEmail((s) => ({ ...s, to: e.target.value }))} placeholder="ops@example.com" />
              </div>
              <div>
                <label>{t("alerts.smtpUser")}</label>
                <input value={email.smtpUser} onChange={(e) => setEmail((s) => ({ ...s, smtpUser: e.target.value }))} placeholder={t("optional")} />
              </div>
              <div>
                <label>{t("alerts.smtpPass")}</label>
                <input type="password" value={email.smtpPass} onChange={(e) => setEmail((s) => ({ ...s, smtpPass: e.target.value }))} placeholder={t("optional")} />
              </div>
            </>
          )}
          <div style={{ flex: "0 0 180px" }}>
            <label>{t("alerts.scope")}</label>
            <select value={pageId} onChange={(e) => setPageId(e.target.value)}>
              <option value="">{t("alerts.allPages")}</option>
              {pages.map((p) => (
                <option key={p.id} value={p.id}>{p.title}</option>
              ))}
            </select>
          </div>
          <div style={{ flex: "0 0 150px" }}>
            <label>{t("alerts.escalateAfter")}</label>
            <input type="number" min={0} value={escalate} onChange={(e) => setEscalate(e.target.value)} />
          </div>
          <div style={{ flex: "0 0 100%" }}>
            <label>{t("alerts.events")}</label>
            <div className="row" style={{ marginTop: 2 }}>
              {EVENT_TYPES.map((ev) => (
                <div key={ev} className="check-cell">
                  <label>
                    <input type="checkbox" checked={events.includes(ev)} onChange={() => toggleCreateEvent(ev)} />
                    {t(`alerts.event.${ev}`)}
                  </label>
                </div>
              ))}
            </div>
            <div className="hint">{t("alerts.eventsHint")}</div>
          </div>
          <div style={{ flex: "0 0 100%" }}>
            <label>{t("alerts.template")}</label>
            <textarea
              value={template}
              rows={2}
              onChange={(e) => setTemplate(e.target.value)}
              placeholder={"{verb}: {probe} на {host}\nстатус: {status}"}
              style={{ resize: "vertical" }}
            />
            <div className="hint">{t("alerts.templateHint")}</div>
          </div>
          <div className="btn-cell">
            <button>{t("add")}</button>
          </div>
        </form>
        <div className="hint">
          {type === "telegram"
            ? t("alerts.hintTelegram")
            : type === "webhook"
            ? t("alerts.hintWebhook")
            : t("alerts.hintEmail")}
        </div>
      </div>

      <div className="card">
        <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>{t("alerts.name")}</th>
              <th>{t("alerts.type")}</th>
              <th>{t("alerts.scope")}</th>
              <th>{t("alerts.enabled")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {channels.map((ch) =>
              editingId === ch.id ? (
                <tr key={ch.id}>
                  <td colSpan={5}>
                    <form
                      className="row"
                      onSubmit={(e) => {
                        e.preventDefault();
                        saveEdit(ch);
                      }}
                    >
                      <div>
                        <label>{t("alerts.name")}</label>
                        <input
                          value={edit.name}
                          onChange={(e) => setEdit((s) => ({ ...s, name: e.target.value }))}
                        />
                      </div>
                      {ch.type === "telegram" && (
                        <>
                          <div>
                            <label>{t("alerts.botToken")}</label>
                            <input
                              value={edit.botToken}
                              placeholder="••••••"
                              onChange={(e) => setEdit((s) => ({ ...s, botToken: e.target.value }))}
                            />
                          </div>
                          <div>
                            <label>{t("alerts.chatId")}</label>
                            <input
                              value={edit.chatId}
                              onChange={(e) => setEdit((s) => ({ ...s, chatId: e.target.value }))}
                            />
                          </div>
                          <div style={{ flex: 2 }}>
                            <label>{t("alerts.proxy")}</label>
                            <input
                              value={edit.proxy}
                              placeholder="socks5://host:1080"
                              onChange={(e) => setEdit((s) => ({ ...s, proxy: e.target.value }))}
                            />
                          </div>
                          <div className="btn-cell">
                            <button
                              type="button"
                              className="secondary"
                              disabled={testing}
                              onClick={() =>
                                runTest({
                                  channel_id: ch.id,
                                  config: { bot_token: edit.botToken, proxy: edit.proxy.trim() },
                                })
                              }
                            >
                              {testing ? t("alerts.testing") : t("alerts.test")}
                            </button>
                          </div>
                        </>
                      )}
                      {ch.type === "webhook" && (
                        <div style={{ flex: 2 }}>
                          <label>{t("alerts.webhookUrl")}</label>
                          <input
                            value={edit.url}
                            onChange={(e) => setEdit((s) => ({ ...s, url: e.target.value }))}
                          />
                        </div>
                      )}
                      <div style={{ flex: "0 0 180px" }}>
                        <label>{t("alerts.scope")}</label>
                        <select
                          value={edit.pageId}
                          onChange={(e) => setEdit((s) => ({ ...s, pageId: e.target.value }))}
                        >
                          <option value="">{t("alerts.allPages")}</option>
                          {pages.map((p) => (
                            <option key={p.id} value={p.id}>{p.title}</option>
                          ))}
                        </select>
                      </div>
                      <div style={{ flex: "0 0 150px" }}>
                        <label>{t("alerts.escalateAfter")}</label>
                        <input
                          type="number"
                          min={0}
                          value={edit.escalate}
                          onChange={(e) => setEdit((s) => ({ ...s, escalate: e.target.value }))}
                        />
                      </div>
                      <div style={{ flex: "0 0 100%" }}>
                        <label>{t("alerts.events")}</label>
                        <div className="row" style={{ marginTop: 2 }}>
                          {EVENT_TYPES.map((ev) => (
                            <div key={ev} className="check-cell">
                              <label>
                                <input
                                  type="checkbox"
                                  checked={edit.events.includes(ev)}
                                  onChange={() => toggleEditEvent(ev)}
                                />
                                {t(`alerts.event.${ev}`)}
                              </label>
                            </div>
                          ))}
                        </div>
                        <div className="hint">{t("alerts.eventsHint")}</div>
                      </div>
                      <div style={{ flex: "0 0 100%" }}>
                        <label>{t("alerts.template")}</label>
                        <textarea
                          value={edit.template}
                          rows={2}
                          onChange={(e) => setEdit((s) => ({ ...s, template: e.target.value }))}
                          placeholder={"{verb}: {probe} на {host}\nстатус: {status}"}
                          style={{ resize: "vertical" }}
                        />
                        <div className="hint">{t("alerts.templateHint")}</div>
                      </div>
                      <div className="btn-cell">
                        <button>{t("save")}</button>
                        <button type="button" className="ghost" onClick={() => setEditingId(null)}>
                          {t("cancel")}
                        </button>
                      </div>
                    </form>
                  </td>
                </tr>
              ) : (
                <tr key={ch.id}>
                  <td>{ch.name}</td>
                  <td className="muted">{ch.type}</td>
                  <td className="muted">{pageName(ch.page_id)}</td>
                  <td>
                    <button className="secondary btn-sm" onClick={() => toggle(ch)}>
                      {ch.enabled ? t("alerts.on") : t("alerts.off")}
                    </button>
                  </td>
                  <td>
                    <div className="row-actions" style={{ justifyContent: "flex-end" }}>
                      <button
                        className="icon-btn"
                        title={t("edit")}
                        aria-label={t("edit")}
                        onClick={() => startEdit(ch)}
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
              )
            )}
            {channels.length === 0 && (
              <tr>
                <td colSpan={5} className="empty">
                  {t("alerts.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  );
}
