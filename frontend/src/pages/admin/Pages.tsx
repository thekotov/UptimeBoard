import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, clearMetrics, importPage, type Page } from "../../api/client";
import { PencilIcon, TrashIcon } from "../../components/Icons";
import { Modal } from "../../components/Modal";
import { Skeleton } from "../../components/Skeleton";
import { SlugMsg } from "../../components/SlugMsg";
import { useConfirm } from "../../confirm";
import { useI18n } from "../../i18n";
import { useToast } from "../../toast";
import { useSlugCheck, slugInvalid } from "../../useSlugCheck";
import { AdminNav } from "./AdminNav";

export function Pages() {
  const { t } = useI18n();
  const toast = useToast();
  const confirm = useConfirm();
  const [pages, setPages] = useState<Page[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => {
    api.get<Page[]>("/admin/pages").then(({ data }) => {
      setPages(data);
      setLoaded(true);
    });
  };
  useEffect(load, []);

  const remove = async (id: number) => {
    if (!(await confirm({ message: t("pages.confirmDelete"), danger: true }))) return;
    await api.delete(`/admin/pages/${id}`);
    toast.success(t("toast.deleted"));
    load();
  };

  const clearAllMetrics = async () => {
    if (!(await confirm({ message: t("metrics.confirm"), danger: true }))) return;
    try {
      const n = await clearMetrics();
      toast.success(t("metrics.cleared", { n }));
    } catch {
      toast.error(t("toast.error"));
    }
  };

  const onImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      await importPage(data);
      toast.success(t("toast.saved"));
      load();
    } catch {
      toast.error(t("toast.error"));
    }
  };

  const knownGroups = useMemo(
    () => [...new Set(pages.map((p) => p.group_name).filter(Boolean) as string[])],
    [pages]
  );

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const filtered = pages.filter(
      (p) =>
        !needle ||
        p.title.toLowerCase().includes(needle) ||
        p.slug.toLowerCase().includes(needle) ||
        (p.group_name ?? "").toLowerCase().includes(needle)
    );
    const map = new Map<string, Page[]>();
    for (const p of filtered) {
      const key = p.group_name || t("ungrouped");
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(p);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [pages, q, t]);

  return (
    <div className="container fade-in">
      <AdminNav />

      <div className="toolbar">
        <button onClick={() => setCreating(true)}>+ {t("pages.create")}</button>
        <button className="secondary" onClick={() => fileRef.current?.click()}>
          ⬆ {t("io.import")}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          style={{ display: "none" }}
          onChange={onImportFile}
        />
        <button className="ghost danger" onClick={clearAllMetrics} title={t("metrics.hint")}>
          🧹 {t("metrics.clear")}
        </button>
        <div className="search">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("search")} />
        </div>
      </div>

      {!loaded && (
        <div className="card">
          {[0, 1, 2].map((i) => (
            <div key={i} className="incident-row">
              <Skeleton w={`${30 + i * 10}%`} h={14} />
              <span className="spacer" />
              <Skeleton w={70} h={14} />
            </div>
          ))}
        </div>
      )}

      {loaded && pages.length === 0 && (
        <div className="card empty">
          <span className="e-emoji">📭</span>
          {t("pages.empty")}
        </div>
      )}
      {pages.length > 0 && groups.length === 0 && (
        <div className="card empty">
          <span className="e-emoji">🔍</span>
          {t("nothingFound")}
        </div>
      )}

      {groups.map(([groupName, items]) => (
        <div key={groupName}>
          <div className="group-head">
            📁 {groupName} <span className="count">{items.length}</span>
          </div>
          <div className="card" style={{ marginTop: 0 }}>
            <div className="table-scroll">
            <table className="table">
              <tbody>
                {items.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <Link to={`/admin/pages/${p.id}`}>{p.title}</Link>
                      {p.is_private && (
                        <span title={t("editor.private")} style={{ marginLeft: 6 }}>🔒</span>
                      )}
                    </td>
                    <td className="muted">
                      <a href={`/status/${p.slug}`} target="_blank">
                        /status/{p.slug}
                      </a>
                    </td>
                    <td>{p.is_published ? t("pages.yes") : t("pages.no")}</td>
                    <td>
                      <div className="row-actions" style={{ justifyContent: "flex-end" }}>
                        <Link
                          to={`/admin/pages/${p.id}`}
                          className="icon-btn"
                          title={t("edit")}
                          aria-label={t("edit")}
                        >
                          <PencilIcon />
                        </Link>
                        <button
                          className="icon-btn danger"
                          title={t("delete")}
                          aria-label={t("delete")}
                          onClick={() => remove(p.id)}
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
        </div>
      ))}

      {creating && (
        <Modal title={t("pages.create")} onClose={() => setCreating(false)}>
          <CreatePageForm
            knownGroups={knownGroups}
            onSaved={() => {
              setCreating(false);
              load();
            }}
            onCancel={() => setCreating(false)}
          />
        </Modal>
      )}
    </div>
  );
}

function CreatePageForm({
  knownGroups,
  onSaved,
  onCancel,
}: {
  knownGroups: string[];
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [group, setGroup] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slugState = useSlugCheck(slug);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (slugInvalid(slugState) || !slug.trim()) return;
    setError(null);
    setBusy(true);
    try {
      await api.post("/admin/pages", { slug, title, group_name: group.trim() || null });
      toast.success(t("toast.saved"));
      onSaved();
    } catch (err: any) {
      setError(err.response?.data?.detail ?? t("pages.createError"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="form-grid">
      <div>
        <label>{t("pages.title")}</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus placeholder="My Service" />
      </div>
      <div>
        <label>{t("pages.slug")}</label>
        <input
          className={slugInvalid(slugState) ? "invalid" : ""}
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          placeholder="my-service"
        />
        <SlugMsg state={slugState} />
      </div>
      <div className="full">
        <label>{t("pages.group")}</label>
        <input
          value={group}
          onChange={(e) => setGroup(e.target.value)}
          placeholder={t("optional")}
          list="known-groups"
        />
        <datalist id="known-groups">
          {knownGroups.map((g) => (
            <option key={g} value={g} />
          ))}
        </datalist>
      </div>
      {error && <div className="error full">{error}</div>}
      <div className="form-actions">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
          {t("cancel")}
        </button>
        <button disabled={busy || slugInvalid(slugState) || !slug.trim()}>{t("create")}</button>
      </div>
    </form>
  );
}
