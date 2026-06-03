import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listPublicPages, type PageListItem } from "../api/client";
import { useI18n } from "../i18n";

interface Cmd {
  label: string;
  hint?: string;
  to: string;
}

export function CommandPalette() {
  const nav = useNavigate();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [pages, setPages] = useState<PageListItem[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  // global Cmd/Ctrl+K toggle
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setSel(0);
    listPublicPages().then(setPages).catch(() => undefined);
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const commands: Cmd[] = useMemo(() => {
    const base: Cmd[] = [
      { label: t("home.toAdmin"), hint: "/admin", to: "/admin" },
      { label: t("app.title"), hint: "/", to: "/" },
    ];
    const pageCmds = pages.map((p) => ({
      label: p.title,
      hint: `/status/${p.slug}${p.group_name ? " · " + p.group_name : ""}`,
      to: `/status/${p.slug}`,
    }));
    return [...pageCmds, ...base];
  }, [pages, t]);

  const filtered = useMemo(() => {
    const n = q.trim().toLowerCase();
    if (!n) return commands;
    return commands.filter((c) => (c.label + " " + (c.hint ?? "")).toLowerCase().includes(n));
  }, [commands, q]);

  if (!open) return null;

  const go = (c: Cmd) => {
    setOpen(false);
    nav(c.to);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter" && filtered[sel]) {
      go(filtered[sel]);
    }
  };

  return (
    <div className="cmdk-overlay" onClick={() => setOpen(false)}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <input
          ref={inputRef}
          className="cmdk-input"
          value={q}
          placeholder={t("cmd.placeholder")}
          onChange={(e) => {
            setQ(e.target.value);
            setSel(0);
          }}
          onKeyDown={onKey}
        />
        <div className="cmdk-list">
          {filtered.length === 0 && <div className="empty">{t("nothingFound")}</div>}
          {filtered.map((c, i) => (
            <button
              key={c.to + i}
              className={`cmdk-item ${i === sel ? "sel" : ""}`}
              onMouseEnter={() => setSel(i)}
              onClick={() => go(c)}
            >
              <span>{c.label}</span>
              {c.hint && <span className="muted small">{c.hint}</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
