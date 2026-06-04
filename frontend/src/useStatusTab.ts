import { useEffect } from "react";
import type { Status } from "./api/client";

const COLOR: Record<Status, string> = {
  up: "#16a34a",
  recovered: "#0d9488",
  degraded: "#d97706",
  down: "#dc2626",
  unknown: "#9ca3af",
  paused: "#9ca3af",
};
const GLYPH: Record<Status, string> = { up: "🟢", recovered: "🎉", degraded: "🟠", down: "🔴", unknown: "⚪", paused: "⏸" };

function faviconDataUri(color: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="${color}"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

/** Reflect the page's overall status in the browser tab: title prefix + favicon. */
export function useStatusTab(status: Status | null, title: string | null) {
  useEffect(() => {
    if (!status || !title) return;

    const prevTitle = document.title;
    document.title = `${GLYPH[status]} ${title}`;

    let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    const created = !link;
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    const prevHref = link.getAttribute("href");
    link.setAttribute("type", "image/svg+xml");
    link.href = faviconDataUri(COLOR[status]);

    return () => {
      document.title = prevTitle;
      if (created) link?.remove();
      else if (prevHref) link?.setAttribute("href", prevHref);
    };
  }, [status, title]);
}
