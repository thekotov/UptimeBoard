import type { Status } from "../api/client";
import { useI18n } from "../i18n";

const GLYPHS: Record<Status, string> = {
  up: "✓",
  degraded: "!",
  down: "✕",
  unknown: "?",
  paused: "⏸",
};

export function StatusBadge({ status }: { status: Status }) {
  const { t } = useI18n();
  return (
    <span className={`badge ${status}`}>
      <span aria-hidden>{GLYPHS[status]}</span>
      {t(`status.${status}`)}
    </span>
  );
}

export function StatusDot({ status }: { status: Status }) {
  return <span className={`dot ${status}`} />;
}

/** Badge for an incident row: shows the bad status while ongoing, or a green
 *  "resolved" state once it's over (so resolved incidents don't read as "down"). */
export function IncidentBadge({ ongoing, status }: { ongoing: boolean; status: Status }) {
  const { t } = useI18n();
  if (ongoing) return <StatusBadge status={status} />;
  return (
    <span className="badge up">
      <span aria-hidden>✓</span>
      {t("incidents.resolved")}
    </span>
  );
}

export const overallGlyph: Record<Status, string> = {
  up: "🟢",
  degraded: "🟠",
  down: "🔴",
  unknown: "⚪",
  paused: "⏸",
};
