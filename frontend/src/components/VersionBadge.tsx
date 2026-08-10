import { useState } from "react";
import { useLocation } from "react-router-dom";
import { CHANGELOG, VERSION } from "../changelog";
import { useI18n } from "../i18n";
import { Modal } from "./Modal";

/** Small fixed version label; click to open the changelog. Hidden on the kiosk
 *  wall-board so it doesn't clutter an always-on display. */
export function VersionBadge() {
  const { t } = useI18n();
  const loc = useLocation();
  const [open, setOpen] = useState(false);

  if (loc.pathname.endsWith("/wall")) return null;

  return (
    <>
      <button className="version-badge" onClick={() => setOpen(true)} title={t("changelog.open")}>
        v{VERSION}
      </button>
      {open && (
        <Modal title={t("changelog.title")} onClose={() => setOpen(false)}>
          <div className="changelog">
            {CHANGELOG.map((e) => (
              <div className="cl-entry" key={e.version}>
                <div className="cl-head">
                  <b>v{e.version}</b> <span className="muted small">{e.date}</span>
                </div>
                <ul>
                  {e.changes.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}
