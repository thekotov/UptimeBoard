import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ackIncident,
  adminListIncidents,
  type Announcement,
  api,
  type CheckResult,
  checkProbeNow,
  clearIncidents,
  clearMetrics,
  clone,
  createAnnouncement,
  createMaintenance,
  deleteAnnouncement,
  deleteIncident,
  deleteMaintenance,
  exportPage,
  type Incident,
  listAnnouncements,
  listMaintenance,
  type MaintenanceWindow,
  type PageDetail,
  type Probe,
  type Server,
  type Service,
  type Status,
  unackIncident,
} from "../../api/client";
import { CopyIcon, GripIcon, PencilIcon, RefreshIcon, TrashIcon } from "../../components/Icons";
import { classifyError } from "../../errors";
import { Modal } from "../../components/Modal";
import { SlugMsg } from "../../components/SlugMsg";
import { SkeletonCard } from "../../components/Skeleton";
import { Spinner } from "../../components/Spinner";
import { IncidentBadge, StatusDot } from "../../components/StatusBadge";
import { useConfirm } from "../../confirm";
import { formatDuration, relativeShort, relativeTime, useI18n } from "../../i18n";
import { useToast } from "../../toast";
import { slugInvalid, useSlugCheck } from "../../useSlugCheck";
import { AdminNav } from "./AdminNav";
import { BulkProbeImport } from "./BulkProbeImport";
import { ProbeForm, probeSummary } from "./ProbeForm";

type ModalState =
  | { kind: "page" }
  | { kind: "service-add" }
  | { kind: "service-edit"; service: Service }
  | { kind: "server-add"; serviceId: number }
  | { kind: "server-edit"; server: Server }
  | { kind: "probe-add"; serverId: number }
  | { kind: "probe-bulk"; serverId: number; host: string }
  | { kind: "probe-edit"; probe: Probe }
  | { kind: "maintenance" }
  | { kind: "incidents" }
  | { kind: "announcements" }
  | { kind: "bulk-tolerance" }
  | null;

/** Client-side staleness mirror of the backend rule (interval*3 + 30s grace). */
function liveStatus(probe: Probe): { status: Status; stale: boolean } {
  if (!probe.enabled) return { status: "paused", stale: false };
  if (!probe.last_checked_at) return { status: "unknown", stale: true };
  const ageSec = (Date.now() - new Date(probe.last_checked_at).getTime()) / 1000;
  const stale = ageSec > probe.interval_sec * 3 + 30;
  return { status: stale ? "unknown" : probe.last_status, stale };
}

export function PageEditor() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const { t, lang } = useI18n();
  const toast = useToast();
  const confirm = useConfirm();
  const [page, setPage] = useState<PageDetail | null>(null);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [collapsedServers, setCollapsedServers] = useState<Set<number>>(new Set());
  const [checkingProbe, setCheckingProbe] = useState<number | null>(null);
  const [checkRes, setCheckRes] = useState<Record<number, CheckResult>>({});
  const [modal, setModal] = useState<ModalState>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const drag = useRef<{ key: string; index: number } | null>(null);
  const probeDrag = useRef<{ probeId: number; serverId: number; index: number } | null>(null);
  const [renaming, setRenaming] = useState<{ kind: "service" | "server" | "probe"; id: number } | null>(null);
  const [renameVal, setRenameVal] = useState("");

  const startRename = (kind: "service" | "server" | "probe", id: number, current: string) => {
    setRenameVal(current);
    setRenaming({ kind, id });
  };
  const saveRename = async () => {
    if (!renaming) return;
    const val = renameVal.trim();
    const r = renaming;
    setRenaming(null);
    if (!val) return;
    await api.patch(`/admin/${r.kind}s/${r.id}`, { name: val });
    load();
  };

  // move a probe to another server (cross-server drag)
  const moveProbe = async (probeId: number, toServerId: number, order: number) => {
    await api.patch(`/admin/probes/${probeId}`, { server_id: toServerId, order });
    load();
  };
  const probeDndRow = (probe: Probe, serverId: number, index: number, service: Service, server: Server) => ({
    draggable: true,
    onDragStart: (e: React.DragEvent) => {
      e.stopPropagation();
      probeDrag.current = { probeId: probe.id, serverId, index };
      e.dataTransfer.effectAllowed = "move";
    },
    onDragOver: (e: React.DragEvent) => {
      if (probeDrag.current) { e.preventDefault(); e.stopPropagation(); }
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const d = probeDrag.current;
      probeDrag.current = null;
      if (!d) return;
      if (d.serverId === serverId) {
        if (d.index !== index) reorderProbes(service, server, d.index, index);
      } else {
        moveProbe(d.probeId, serverId, index);
      }
    },
    onDragEnd: () => { probeDrag.current = null; },
  });
  // drop zone on a whole server (move probe here, e.g. into an empty server)
  const probeDropServer = (serverId: number) => ({
    onDragOver: (e: React.DragEvent) => {
      if (probeDrag.current && probeDrag.current.serverId !== serverId) { e.preventDefault(); }
    },
    onDrop: (e: React.DragEvent) => {
      const d = probeDrag.current;
      if (d && d.serverId !== serverId) {
        e.preventDefault();
        probeDrag.current = null;
        moveProbe(d.probeId, serverId, 999);
      }
    },
  });

  const toggleSelect = (probeId: number) =>
    setSelected((s) => {
      const next = new Set(s);
      next.has(probeId) ? next.delete(probeId) : next.add(probeId);
      return next;
    });

  const collapseInit = useRef(false);
  const load = () => {
    api.get<PageDetail>(`/admin/pages/${id}`).then(({ data }) => {
      setPage(data);
      if (!collapseInit.current) {
        collapseInit.current = true;
        setCollapsed(new Set(data.services.map((s) => s.id)));
        setCollapsedServers(new Set(data.services.flatMap((s) => s.servers.map((sv) => sv.id))));
      }
    });
  };
  useEffect(load, [id]);

  const onSaved = () => {
    setModal(null);
    load();
  };

  const totalProbes = useMemo(
    () =>
      page?.services.reduce((n, s) => n + s.servers.reduce((m, sv) => m + sv.probes.length, 0), 0) ?? 0,
    [page]
  );

  if (!page)
    return (
      <div className="container">
        <AdminNav />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );

  const togglePublished = async () => {
    await api.patch(`/admin/pages/${page.id}`, { is_published: !page.is_published });
    toast.success(t("toast.saved"));
    load();
  };

  const checkNow = async (probeId: number, name: string) => {
    setCheckingProbe(probeId);
    try {
      const res = await checkProbeNow(probeId);
      setCheckRes((prev) => ({ ...prev, [probeId]: res }));
      const lat = res.latency_ms != null ? ` (${res.latency_ms.toFixed(0)} ${t("dash.ms")})` : "";
      const errKey = classifyError(res.error);
      const err = res.status !== "up" && res.error ? ` — ${errKey ? t(errKey) : res.error}` : "";
      toast.show(`${name}: ${t(`status.${res.status}`)}${lat}${err}`, res.status === "up" ? "success" : "error");
      load();
    } catch {
      toast.error(t("probe.checkFail"));
    } finally {
      setCheckingProbe(null);
    }
  };

  const togglePause = async (probe: Probe) => {
    await api.patch(`/admin/probes/${probe.id}`, { enabled: !probe.enabled });
    toast.success(t("toast.saved"));
    load();
  };

  const remove = async (url: string, message: string) => {
    if (!(await confirm({ message, danger: true }))) return;
    await api.delete(url);
    toast.success(t("toast.deleted"));
    load();
  };

  const doClone = async (kind: "pages" | "services" | "servers" | "probes", cid: number) => {
    await clone(kind, cid);
    toast.success(t("toast.cloned"));
    load();
  };

  const bulkSetEnabled = async (enabled: boolean) => {
    await Promise.all([...selected].map((pid) => api.patch(`/admin/probes/${pid}`, { enabled })));
    toast.success(t("toast.saved"));
    setSelected(new Set());
    load();
  };
  const bulkCheck = async () => {
    await Promise.all([...selected].map((pid) => checkProbeNow(pid).catch(() => null)));
    toast.success(t("toast.saved"));
    load();
  };
  const bulkDelete = async () => {
    if (!(await confirm({ message: t("bulk.confirmDelete", { n: selected.size }), danger: true }))) return;
    await Promise.all([...selected].map((pid) => api.delete(`/admin/probes/${pid}`)));
    toast.success(t("toast.deleted"));
    setSelected(new Set());
    load();
  };

  const doExport = async () => {
    const data = await exportPage(page.id);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${page.slug}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const clearPageMetrics = async () => {
    if (!(await confirm({ message: t("metrics.confirmPage", { title: page.title }), danger: true }))) return;
    try {
      const n = await clearMetrics(page.id);
      toast.success(t("metrics.cleared", { n }));
    } catch {
      toast.error(t("toast.error"));
    }
  };

  // --- drag & drop ordering ---
  const dnd = (key: string, index: number, onReorder: (from: number, to: number) => void) => ({
    draggable: true,
    onDragStart: (e: React.DragEvent) => {
      e.stopPropagation();
      drag.current = { key, index };
      e.dataTransfer.effectAllowed = "move";
    },
    onDragOver: (e: React.DragEvent) => {
      if (drag.current?.key === key) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    onDrop: (e: React.DragEvent) => {
      if (drag.current?.key !== key) return;
      e.preventDefault();
      e.stopPropagation();
      const d = drag.current;
      if (d && d.key === key && d.index !== index) onReorder(d.index, index);
      drag.current = null;
    },
    onDragEnd: () => {
      drag.current = null;
    },
  });

  const persistOrder = async (kind: "services" | "servers" | "probes", items: { id: number }[]) => {
    await Promise.all(items.map((it, i) => api.patch(`/admin/${kind}/${it.id}`, { order: i })));
    load();
  };

  const reorder = <T,>(arr: T[], from: number, to: number): T[] => {
    const copy = [...arr];
    const [moved] = copy.splice(from, 1);
    copy.splice(to, 0, moved);
    return copy;
  };

  const reorderServices = (from: number, to: number) => {
    const next = reorder(page.services, from, to);
    setPage({ ...page, services: next });
    persistOrder("services", next);
  };
  const reorderServers = (service: Service, from: number, to: number) => {
    const next = reorder(service.servers, from, to);
    setPage({ ...page, services: page.services.map((s) => (s.id === service.id ? { ...s, servers: next } : s)) });
    persistOrder("servers", next);
  };
  const reorderProbes = (service: Service, server: Server, from: number, to: number) => {
    const next = reorder(server.probes, from, to);
    setPage({
      ...page,
      services: page.services.map((s) =>
        s.id === service.id
          ? { ...s, servers: s.servers.map((sv) => (sv.id === server.id ? { ...sv, probes: next } : sv)) }
          : s
      ),
    });
    persistOrder("probes", next);
  };

  const toggle = (sid: number) =>
    setCollapsed((c) => {
      const next = new Set(c);
      next.has(sid) ? next.delete(sid) : next.add(sid);
      return next;
    });
  const toggleServer = (svid: number) =>
    setCollapsedServers((c) => {
      const next = new Set(c);
      next.has(svid) ? next.delete(svid) : next.add(svid);
      return next;
    });
  const expandAll = () => {
    setCollapsed(new Set());
    setCollapsedServers(new Set());
  };
  const collapseAll = () => {
    setCollapsed(new Set(page.services.map((s) => s.id)));
    setCollapsedServers(new Set(page.services.flatMap((s) => s.servers.map((sv) => sv.id))));
  };

  return (
    <div className="container fade-in">
      <AdminNav />

      <div className="card">
        <div className="card-head">
          <div className="stack">
            <h3>{page.title}</h3>
            <span className="muted small">
              {page.group_name && <span className="pill">{page.group_name}</span>}{" "}
              <a href={`/status/${page.slug}`} target="_blank">
                {t("editor.viewPublic")} · /status/{page.slug}
              </a>
            </span>
          </div>
          <div className="row-actions">
            <button className="secondary btn-sm" onClick={() => nav(`/admin/pages/${page.id}/events`)}>
              🗞 {t("nav.events")}
            </button>
            <button className="secondary btn-sm" onClick={() => nav(`/admin/pages/${page.id}/certs`)}>
              🔒 {t("nav.certs")}
            </button>
            <button className="secondary btn-sm" onClick={() => setModal({ kind: "announcements" })}>
              📢 {t("ann.title")}
            </button>
            <button className="secondary btn-sm" onClick={() => setModal({ kind: "incidents" })}>
              🔔 {t("incidents.manage")}
            </button>
            <button className="secondary btn-sm" onClick={() => setModal({ kind: "maintenance" })}>
              🛠 {t("maintenance.title")}
            </button>
            <button className="secondary btn-sm" onClick={doExport}>
              ⬇ {t("io.export")}
            </button>
            <button className="secondary btn-sm" onClick={clearPageMetrics} title={t("metrics.hintPage")}>
              🧹 {t("metrics.clear")}
            </button>
            <button
              className="secondary btn-sm"
              title={t("io.copyLink")}
              onClick={() => {
                navigator.clipboard?.writeText(`${location.origin}/status/${page.slug}`);
                toast.success(t("io.copied"));
              }}
            >
              🔗
            </button>
            <button className="secondary btn-sm" onClick={() => setModal({ kind: "page" })}>
              <PencilIcon size={14} /> {t("edit")}
            </button>
            <button className="secondary btn-sm" onClick={togglePublished}>
              {page.is_published ? t("editor.unpublish") : t("editor.publish")}
            </button>
          </div>
        </div>
      </div>

      <div className="toolbar">
        <button onClick={() => setModal({ kind: "service-add" })}>+ {t("editor.addService")}</button>
        {page.services.length > 0 && (
          <span className="muted small">
            {t("editor.counts", {
              servers: page.services.reduce((n, s) => n + s.servers.length, 0),
              probes: totalProbes,
            })}
          </span>
        )}
        <span className="spacer" />
        {page.services.length > 1 && (
          <>
            <button className="ghost" onClick={expandAll}>
              {t("expandAll")}
            </button>
            <button className="ghost" onClick={collapseAll}>
              {t("collapseAll")}
            </button>
          </>
        )}
      </div>

      {selected.size > 0 && (
        <div className="bulk-bar">
          <span><b>{t("bulk.selected", { n: selected.size })}</b></span>
          <span className="spacer" />
          <button className="secondary btn-sm" onClick={bulkCheck}>
            <RefreshIcon size={14} /> {t("bulk.checkAll")}
          </button>
          <button className="secondary btn-sm" onClick={() => bulkSetEnabled(true)}>{t("bulk.enable")}</button>
          <button className="secondary btn-sm" onClick={() => bulkSetEnabled(false)}>{t("bulk.disable")}</button>
          <button className="secondary btn-sm" onClick={() => setModal({ kind: "bulk-tolerance" })}>{t("bulk.tolerance")}</button>
          <button className="danger btn-sm" onClick={bulkDelete}>{t("delete")}</button>
          <button className="ghost btn-sm" onClick={() => setSelected(new Set())}>✕</button>
        </div>
      )}

      {page.services.length === 0 && (
        <div className="card empty">
          <span className="e-emoji">📦</span>
          {t("editor.noServices")}
        </div>
      )}

      {page.services.map((service, si) => {
        const isCollapsed = collapsed.has(service.id);
        const probeCount = service.servers.reduce((m, sv) => m + sv.probes.length, 0);
        return (
          <div className="card" key={service.id} {...dnd("svc", si, reorderServices)}>
            <div className="card-head collapse-head" onClick={() => toggle(service.id)}>
              <h3>
                <span className="grip" title="drag"><GripIcon size={14} /></span>
                <span className={`chev ${isCollapsed ? "" : "open"}`}>▶</span>{" "}
                {renaming?.kind === "service" && renaming.id === service.id ? (
                  <input
                    autoFocus
                    className="rename-input"
                    value={renameVal}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setRenameVal(e.target.value)}
                    onBlur={saveRename}
                    onKeyDown={(e) => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") setRenaming(null); }}
                  />
                ) : (
                  <span
                    title={t("editor.dblclickRename")}
                    onDoubleClick={(e) => { e.stopPropagation(); startRename("service", service.id, service.name); }}
                  >
                    {service.name}
                  </span>
                )}
                <span className="count-pill">
                  {t("editor.counts", { servers: service.servers.length, probes: probeCount })}
                </span>
              </h3>
              <div className="row-actions" onClick={(e) => e.stopPropagation()}>
                <button className="icon-btn" title={t("clone")} aria-label={t("clone")}
                  onClick={() => doClone("services", service.id)}>
                  <CopyIcon />
                </button>
                <button className="icon-btn" title={t("edit")} aria-label={t("edit")}
                  onClick={() => setModal({ kind: "service-edit", service })}>
                  <PencilIcon />
                </button>
                <button className="icon-btn danger" title={t("editor.deleteService")} aria-label={t("editor.deleteService")}
                  onClick={() => remove(`/admin/services/${service.id}`, t("editor.confirmDeleteService"))}>
                  <TrashIcon />
                </button>
              </div>
            </div>

            {!isCollapsed && (
              <>
                {service.servers.length === 0 && <div className="empty">{t("editor.noServers")}</div>}

                {service.servers.map((server, sj) => {
                  const isServerCollapsed = collapsedServers.has(server.id);
                  return (
                  <div className="server" key={server.id} {...dnd(`srv-${service.id}`, sj, (f, tt) => reorderServers(service, f, tt))}>
                    <div className="server-head collapse-head" onClick={() => toggleServer(server.id)}>
                      <div className="inline">
                        <span className="grip" title="drag"><GripIcon size={14} /></span>
                        <span className={`chev ${isServerCollapsed ? "" : "open"}`}>▶</span>{" "}
                        {renaming?.kind === "server" && renaming.id === server.id ? (
                          <input
                            autoFocus
                            className="rename-input"
                            value={renameVal}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => setRenameVal(e.target.value)}
                            onBlur={saveRename}
                            onKeyDown={(e) => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") setRenaming(null); }}
                          />
                        ) : (
                          <strong title={t("editor.dblclickRename")} onDoubleClick={(e) => { e.stopPropagation(); startRename("server", server.id, server.name); }}>
                            {server.name}
                          </strong>
                        )}
                        <span className="muted small">{server.host}</span>
                        {server.note && <span className="muted small server-note" title={server.note}>· {server.note}</span>}
                        <span className="count-pill">{server.probes.length} {t("probe.count")}</span>
                      </div>
                      <div className="row-actions" onClick={(e) => e.stopPropagation()}>
                        <button className="secondary btn-sm" onClick={() => setModal({ kind: "probe-add", serverId: server.id })}>
                          + {t("editor.addProbe")}
                        </button>
                        <button className="secondary btn-sm" title={t("bulk.importTitle")}
                          onClick={() => setModal({ kind: "probe-bulk", serverId: server.id, host: server.host })}>
                          ⇪ {t("bulk.import")}
                        </button>
                        <button className="icon-btn" title={t("clone")} aria-label={t("clone")}
                          onClick={() => doClone("servers", server.id)}>
                          <CopyIcon />
                        </button>
                        <button className="icon-btn" title={t("edit")} aria-label={t("edit")}
                          onClick={() => setModal({ kind: "server-edit", server })}>
                          <PencilIcon />
                        </button>
                        <button className="icon-btn danger" title={t("delete")} aria-label={t("delete")}
                          onClick={() => remove(`/admin/servers/${server.id}`, t("editor.confirmDeleteServer"))}>
                          <TrashIcon />
                        </button>
                      </div>
                    </div>

                    {!isServerCollapsed && (
                    <div className="probe-list" {...probeDropServer(server.id)}>
                        {server.probes.length === 0 && <div className="hint">{t("editor.noProbes")}</div>}
                        {server.probes.map((probe, pk) => {
                          const live = liveStatus(probe);
                          return (
                            <div className="probe-row" key={probe.id}
                              {...probeDndRow(probe, server.id, pk, service, server)}>
                              <div className="probe-name">
                                <input
                                  type="checkbox"
                                  className="sel-box"
                                  checked={selected.has(probe.id)}
                                  onChange={() => toggleSelect(probe.id)}
                                  aria-label="select"
                                />
                                <span className="grip" title="drag"><GripIcon size={13} /></span>
                                <StatusDot status={live.status} />
                                {renaming?.kind === "probe" && renaming.id === probe.id ? (
                                  <input
                                    autoFocus
                                    className="rename-input"
                                    value={renameVal}
                                    onChange={(e) => setRenameVal(e.target.value)}
                                    onBlur={saveRename}
                                    onKeyDown={(e) => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") setRenaming(null); }}
                                  />
                                ) : (
                                  <span title={t("editor.dblclickRename")} onDoubleClick={() => startRename("probe", probe.id, probe.name)}>
                                    {probe.name}
                                  </span>
                                )}
                                <span className="pill">{probe.type}</span>
                                {!probe.enabled && <span className="pill">{t("status.paused")}</span>}
                                {probe.tls_expires_at && (() => {
                                  const days = Math.floor((new Date(probe.tls_expires_at).getTime() - Date.now()) / 864e5);
                                  const cls = days < 0 ? "cert-bad" : days <= 14 ? "cert-warn" : days <= 30 ? "cert-soon" : "cert-ok";
                                  const label = days < 0 ? t("cert.expired") : days === 0 ? t("cert.expiresToday") : days === 1 ? t("cert.expiresInDay") : t("cert.expiresIn", { days });
                                  return (
                                    <span className={`cert-pill ${cls}`} title={t("cert.validUntil", { date: new Date(probe.tls_expires_at).toLocaleString() })}>
                                      🔒 {label}
                                    </span>
                                  );
                                })()}
                                <span className="muted small">
                                  {probeSummary(probe)} · {t("probe.every", { n: probe.interval_sec })}
                                  {probe.last_checked_at && !live.stale && (
                                    <> · {probe.last_latency_ms != null ? `${probe.last_latency_ms.toFixed(0)} ${t("dash.ms")}` : ""} · {relativeShort(probe.last_checked_at, lang)}</>
                                  )}
                                </span>
                                {checkRes[probe.id] && (() => {
                                  const r = checkRes[probe.id];
                                  const k = classifyError(r.error);
                                  return (
                                    <span className={`check-result ${r.status === "up" ? "ok" : "bad"}`}>
                                      <StatusDot status={r.status} />
                                      {t(`status.${r.status}`)}
                                      {r.status !== "up" && r.error && (
                                        <span className="cr-err" title={r.error}>— {k ? t(k) : r.error}</span>
                                      )}
                                      {r.meta?.redirected && r.meta.final_url && (
                                        <span className="cr-url" title={r.meta.final_url}>→ {r.meta.final_url}</span>
                                      )}
                                    </span>
                                  );
                                })()}
                              </div>
                              <div className="row-actions">
                                <button className="secondary btn-sm" disabled={checkingProbe === probe.id}
                                  onClick={() => checkNow(probe.id, probe.name)}>
                                  <RefreshIcon size={14} />
                                  {checkingProbe === probe.id ? t("probe.checking") : t("probe.checkNow")}
                                </button>
                                <button
                                  className="icon-btn"
                                  title={t("probe.copyLink")}
                                  aria-label={t("probe.copyLink")}
                                  onClick={() => {
                                    navigator.clipboard?.writeText(`${location.origin}/status/${page.slug}?probe=${probe.id}`);
                                    toast.success(t("io.copied"));
                                  }}
                                >
                                  🔗
                                </button>
                                <button
                                  className="icon-btn"
                                  title={probe.enabled ? t("probe.pause") : t("probe.resume")}
                                  aria-label={probe.enabled ? t("probe.pause") : t("probe.resume")}
                                  onClick={() => togglePause(probe)}
                                >
                                  {probe.enabled ? "⏸" : "▶"}
                                </button>
                                <button className="icon-btn" title={t("clone")} aria-label={t("clone")}
                                  onClick={() => doClone("probes", probe.id)}>
                                  <CopyIcon />
                                </button>
                                <button className="icon-btn" title={t("edit")} aria-label={t("edit")}
                                  onClick={() => setModal({ kind: "probe-edit", probe })}>
                                  <PencilIcon />
                                </button>
                                <button className="icon-btn danger" title={t("remove")} aria-label={t("remove")}
                                  onClick={() => remove(`/admin/probes/${probe.id}`, t("editor.confirmDeleteProbe"))}>
                                  <TrashIcon />
                                </button>
                              </div>
                            </div>
                          );
                        })}
                    </div>
                    )}
                  </div>
                  );
                })}

                <div style={{ marginTop: 12 }}>
                  <button className="secondary" onClick={() => setModal({ kind: "server-add", serviceId: service.id })}>
                    + {t("editor.addServer")}
                  </button>
                </div>
              </>
            )}
          </div>
        );
      })}

      {/* ---- modals ---- */}
      {modal?.kind === "page" && (
        <Modal title={t("editor.pageSettings")} onClose={() => setModal(null)}>
          <PageForm page={page} onSaved={onSaved} onCancel={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "service-add" && (
        <Modal title={t("editor.addService")} onClose={() => setModal(null)}>
          <ServiceForm pageId={page.id} onSaved={onSaved} onCancel={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "service-edit" && (
        <Modal title={t("editor.editService")} onClose={() => setModal(null)}>
          <ServiceForm service={modal.service} onSaved={onSaved} onCancel={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "server-add" && (
        <Modal title={t("editor.addServer")} onClose={() => setModal(null)}>
          <ServerForm serviceId={modal.serviceId} onSaved={onSaved} onCancel={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "server-edit" && (
        <Modal title={t("editor.editServer")} onClose={() => setModal(null)}>
          <ServerForm server={modal.server} onSaved={onSaved} onCancel={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "probe-add" && (
        <Modal title={t("editor.addProbe")} onClose={() => setModal(null)}>
          <ProbeForm serverId={modal.serverId} pageDefaultTolerance={page.default_tolerance_checks} onSaved={onSaved} onCancel={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "probe-bulk" && (
        <Modal title={t("bulk.importTitle")} onClose={() => setModal(null)}>
          <BulkProbeImport serverId={modal.serverId} host={modal.host} defaultTolerance={page.default_tolerance_checks} onSaved={onSaved} onCancel={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "probe-edit" && (
        <Modal title={t("editor.editProbe")} onClose={() => setModal(null)}>
          <ProbeForm probe={modal.probe} onSaved={onSaved} onCancel={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "maintenance" && (
        <Modal title={t("maintenance.title")} onClose={() => setModal(null)}>
          <MaintenanceManager pageId={page.id} onClose={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "incidents" && (
        <Modal title={t("incidents.manage")} onClose={() => setModal(null)}>
          <IncidentsManager pageId={page.id} />
        </Modal>
      )}
      {modal?.kind === "announcements" && (
        <Modal title={t("ann.title")} onClose={() => setModal(null)}>
          <AnnouncementsManager pageId={page.id} onClose={() => setModal(null)} />
        </Modal>
      )}
      {modal?.kind === "bulk-tolerance" && (
        <Modal title={t("bulk.toleranceTitle")} onClose={() => setModal(null)}>
          <BulkToleranceForm
            count={selected.size}
            onApply={async (n) => {
              await Promise.all(
                [...selected].map((pid) => api.patch(`/admin/probes/${pid}`, { tolerance_checks: n }))
              );
              toast.success(t("toast.saved"));
              setSelected(new Set());
              setModal(null);
              load();
            }}
            onCancel={() => setModal(null)}
          />
        </Modal>
      )}
    </div>
  );
}

function BulkToleranceForm({
  count,
  onApply,
  onCancel,
}: {
  count: number;
  onApply: (n: number) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState(0);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onApply(Number(value));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="form-grid">
      <div className="full">
        <label>{t("probe.tolerance")}</label>
        <input type="number" min={0} value={value} autoFocus onChange={(e) => setValue(+e.target.value)} />
        <div className="muted" style={{ fontSize: "0.85em" }}>{t("bulk.toleranceHint", { n: count })}</div>
      </div>
      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>{t("cancel")}</button>
        <button disabled={busy}>{t("bulk.toleranceApply", { n: count })}</button>
      </div>
    </form>
  );
}

function IncidentsManager({ pageId }: { pageId: number }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const confirm = useConfirm();
  const [items, setItems] = useState<Incident[] | null>(null);

  const load = () => adminListIncidents(pageId).then(setItems);
  useEffect(() => void load(), [pageId]);

  const removeOne = async (inc: Incident) => {
    if (!(await confirm({ message: t("incidents.confirmDelete"), danger: true }))) return;
    await deleteIncident(inc.id);
    toast.success(t("toast.deleted"));
    load();
  };
  const toggleAck = async (inc: Incident) => {
    if (inc.acknowledged_at) await unackIncident(inc.id);
    else await ackIncident(inc.id);
    toast.success(t("toast.saved"));
    load();
  };
  const clearAll = async (onlyResolved: boolean) => {
    const msg = onlyResolved ? t("incidents.confirmClearResolved") : t("incidents.confirmClearAll");
    if (!(await confirm({ message: msg, danger: true }))) return;
    await clearIncidents(pageId, onlyResolved);
    toast.success(t("toast.deleted"));
    load();
  };

  if (!items) return <Spinner />;

  return (
    <div>
      <div className="toolbar">
        <span className="muted small">{items.length}</span>
        <span className="spacer" />
        <button className="secondary btn-sm" disabled={!items.length} onClick={() => clearAll(true)}>
          {t("incidents.clearResolved")}
        </button>
        <button className="danger btn-sm" disabled={!items.length} onClick={() => clearAll(false)}>
          {t("incidents.clearAll")}
        </button>
      </div>

      {items.length === 0 ? (
        <div className="empty">
          <span className="e-emoji">🎉</span>
          {t("incidents.empty")}
        </div>
      ) : (
        items.map((inc) => (
          <div className="incident-row" key={inc.id}>
            <IncidentBadge ongoing={inc.ongoing} status={inc.last_status} />
            <div className="incident-where">
              <div className="what">
                {inc.service_name} · {inc.probe_name}{" "}
                <span className="muted small">({inc.server_name})</span>
                {inc.acknowledged_at && (
                  <span className="badge ack-badge" title={relativeTime(inc.acknowledged_at, lang)}>
                    {t("incidents.acknowledged")}
                  </span>
                )}
              </div>
              <div className="muted small">
                {relativeTime(inc.started_at, lang)} ·{" "}
                {inc.ongoing ? t("incidents.ongoing") : formatDuration(inc.duration_sec, lang)}
              </div>
            </div>
            {inc.ongoing && (
              <button
                className="secondary btn-sm"
                title={inc.acknowledged_at ? t("incidents.unack") : t("incidents.ackHint")}
                onClick={() => toggleAck(inc)}
              >
                {inc.acknowledged_at ? t("incidents.unack") : t("incidents.ack")}
              </button>
            )}
            <button className="icon-btn danger" title={t("delete")} onClick={() => removeOne(inc)}>
              <TrashIcon />
            </button>
          </div>
        ))
      )}
    </div>
  );
}

// ---- form components ----

function PageForm({ page, onSaved, onCancel }: { page: PageDetail; onSaved: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [title, setTitle] = useState(page.title);
  const [slug, setSlug] = useState(page.slug);
  const [description, setDescription] = useState(page.description ?? "");
  const [group, setGroup] = useState(page.group_name ?? "");
  const [isPrivate, setIsPrivate] = useState(page.is_private);
  const [defaultCollapsed, setDefaultCollapsed] = useState(page.default_collapsed);
  const [defaultTolerance, setDefaultTolerance] = useState(page.default_tolerance_checks);
  const [maskIp, setMaskIp] = useState(page.mask_ip);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slugState = useSlugCheck(slug, page.id);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (slugInvalid(slugState) || !slug.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/admin/pages/${page.id}`, {
        title, slug, description: description.trim() || null, group_name: group.trim() || null,
        is_private: isPrivate, default_collapsed: defaultCollapsed,
        default_tolerance_checks: Number(defaultTolerance), mask_ip: maskIp,
      });
      toast.success(t("toast.saved"));
      onSaved();
    } catch (err: any) {
      setError(err.response?.data?.detail ?? t("toast.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="form-grid">
      <div>
        <label>{t("pages.title")}</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
      </div>
      <div>
        <label>{t("pages.slug")}</label>
        <input
          className={slugInvalid(slugState) ? "invalid" : ""}
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
        />
        <SlugMsg state={slugState} />
      </div>
      <div className="full">
        <label>{t("editor.description")}</label>
        <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t("optional")} />
      </div>
      <div className="full">
        <label>{t("editor.group")}</label>
        <input value={group} onChange={(e) => setGroup(e.target.value)} placeholder={t("optional")} />
      </div>
      <div className="full">
        <label>{t("editor.defaultView")}</label>
        <select
          value={defaultCollapsed ? "collapsed" : "expanded"}
          onChange={(e) => setDefaultCollapsed(e.target.value === "collapsed")}
        >
          <option value="collapsed">{t("editor.viewCollapsed")}</option>
          <option value="expanded">{t("editor.viewExpanded")}</option>
        </select>
        <div className="muted" style={{ fontSize: "0.85em" }}>{t("editor.defaultViewHint")}</div>
      </div>
      <div className="full">
        <label>{t("editor.defaultTolerance")}</label>
        <input
          type="number"
          min={0}
          value={defaultTolerance}
          onChange={(e) => setDefaultTolerance(+e.target.value)}
        />
        <div className="muted" style={{ fontSize: "0.85em" }}>{t("editor.defaultToleranceHint")}</div>
      </div>
      <div className="full">
        <label className="check-cell full">
          <input type="checkbox" checked={isPrivate} onChange={(e) => setIsPrivate(e.target.checked)} /> {t("editor.private")}
        </label>
        <div className="muted" style={{ fontSize: "0.85em" }}>{t("editor.privateHint")}</div>
      </div>
      <div className="full">
        <label className="check-cell full">
          <input type="checkbox" checked={maskIp} onChange={(e) => setMaskIp(e.target.checked)} /> {t("editor.maskIp")}
        </label>
        <div className="muted" style={{ fontSize: "0.85em" }}>{t("editor.maskIpHint")}</div>
      </div>
      {error && <div className="error full">{error}</div>}
      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>{t("cancel")}</button>
        <button disabled={busy || slugInvalid(slugState) || !slug.trim()}>{t("save")}</button>
      </div>
    </form>
  );
}

function ServiceForm({ pageId, service, onSaved, onCancel }: { pageId?: number; service?: Service; onSaved: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [name, setName] = useState(service?.name ?? "");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      if (service) await api.patch(`/admin/services/${service.id}`, { name: name.trim() });
      else await api.post("/admin/services", { page_id: pageId, name: name.trim() });
      toast.success(t("toast.saved"));
      onSaved();
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="form-grid">
      <div className="full">
        <label>{t("editor.serviceName")}</label>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="API" />
      </div>
      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>{t("cancel")}</button>
        <button disabled={busy}>{service ? t("save") : t("add")}</button>
      </div>
    </form>
  );
}

function ServerForm({ serviceId, server, onSaved, onCancel }: { serviceId?: number; server?: Server; onSaved: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [name, setName] = useState(server?.name ?? "");
  const [host, setHost] = useState(server?.host ?? "");
  const [note, setNote] = useState(server?.note ?? "");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !host.trim()) return;
    setBusy(true);
    const payload = { name: name.trim(), host: host.trim(), note: note.trim() || null };
    try {
      if (server) await api.patch(`/admin/servers/${server.id}`, payload);
      else await api.post("/admin/servers", { service_id: serviceId, ...payload });
      toast.success(t("toast.saved"));
      onSaved();
    } catch {
      toast.error(t("toast.error"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="form-grid">
      <div>
        <label>{t("editor.newServerName")}</label>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="web-1" />
      </div>
      <div>
        <label>{t("editor.host")}</label>
        <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="example.com" />
      </div>
      <div className="full">
        <label>{t("editor.note")}</label>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("editor.notePlaceholder")} maxLength={255} />
        <div className="hint">{t("editor.noteHint")}</div>
      </div>
      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>{t("cancel")}</button>
        <button disabled={busy}>{server ? t("save") : t("add")}</button>
      </div>
    </form>
  );
}

function MaintenanceManager({ pageId, onClose }: { pageId: number; onClose: () => void }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [items, setItems] = useState<MaintenanceWindow[]>([]);
  const [title, setTitle] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const load = () => listMaintenance(pageId).then(setItems);
  useEffect(() => void load(), [pageId]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !from || !to) return;
    await createMaintenance({
      page_id: pageId,
      title: title.trim(),
      starts_at: new Date(from).toISOString(),
      ends_at: new Date(to).toISOString(),
    });
    toast.success(t("toast.saved"));
    setTitle(""); setFrom(""); setTo("");
    load();
  };
  const del = async (id: number) => {
    await deleteMaintenance(id);
    toast.success(t("toast.deleted"));
    load();
  };

  return (
    <div>
      <form onSubmit={add} className="form-grid">
        <div className="full">
          <label>{t("pages.title")}</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Плановые работы" />
        </div>
        <div>
          <label>{t("maintenance.from")}</label>
          <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div>
          <label>{t("maintenance.to")}</label>
          <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="form-actions">
          <button type="button" className="ghost" onClick={onClose}>{t("cancel")}</button>
          <button>{t("maintenance.add")}</button>
        </div>
      </form>

      <div style={{ marginTop: 14 }}>
        {items.length === 0 && <div className="empty">{t("maintenance.empty")}</div>}
        {items.map((w) => (
          <div className="incident-row" key={w.id}>
            <div className="incident-where">
              <div className="what">{w.title}</div>
              <div className="muted small">
                {new Date(w.starts_at).toLocaleString(lang)} — {new Date(w.ends_at).toLocaleString(lang)}
              </div>
            </div>
            <button className="icon-btn danger" title={t("delete")} onClick={() => del(w.id)}>
              <TrashIcon />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function AnnouncementsManager({ pageId, onClose }: { pageId: number; onClose: () => void }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const confirm = useConfirm();
  const [items, setItems] = useState<Announcement[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [level, setLevel] = useState<"info" | "warning">("info");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const load = () => listAnnouncements(pageId).then(setItems);
  useEffect(() => void load(), [pageId]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !from || !to) return;
    await createAnnouncement({
      page_id: pageId, title: title.trim(), body: body.trim() || null, level,
      starts_at: new Date(from).toISOString(), ends_at: new Date(to).toISOString(),
    });
    toast.success(t("toast.saved"));
    setTitle(""); setBody(""); setFrom(""); setTo("");
    load();
  };
  const del = async (id: number) => {
    if (!(await confirm({ message: t("ann.confirmDelete"), danger: true }))) return;
    await deleteAnnouncement(id);
    toast.success(t("toast.deleted"));
    load();
  };

  return (
    <div>
      <form onSubmit={add} className="form-grid">
        <div className="full">
          <label>{t("ann.heading")}</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Плановое обновление в 22:00" />
        </div>
        <div className="full">
          <label>{t("ann.body")}</label>
          <textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        <div>
          <label>{t("ann.level")}</label>
          <select value={level} onChange={(e) => setLevel(e.target.value as "info" | "warning")}>
            <option value="info">{t("ann.info")}</option>
            <option value="warning">{t("ann.warning")}</option>
          </select>
        </div>
        <div>
          <label>{t("maintenance.from")}</label>
          <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div>
          <label>{t("maintenance.to")}</label>
          <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="form-actions">
          <button type="button" className="ghost" onClick={onClose}>{t("cancel")}</button>
          <button>{t("ann.add")}</button>
        </div>
      </form>

      <div style={{ marginTop: 14 }}>
        {items.length === 0 && <div className="empty">{t("ann.empty")}</div>}
        {items.map((a) => (
          <div className="incident-row" key={a.id}>
            <span className={`badge ${a.level === "warning" ? "degraded" : "unknown"}`}>
              {a.level === "warning" ? t("ann.warning") : t("ann.info")}
            </span>
            <div className="incident-where">
              <div className="what">{a.title}</div>
              <div className="muted small">
                {new Date(a.starts_at).toLocaleString(lang)} — {new Date(a.ends_at).toLocaleString(lang)}
              </div>
            </div>
            <button className="icon-btn danger" title={t("delete")} onClick={() => del(a.id)}>
              <TrashIcon />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
