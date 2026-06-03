import { createContext, useContext, useRef, useState, type ReactNode } from "react";
import { Modal } from "./components/Modal";
import { useI18n } from "./i18n";

interface ConfirmOptions {
  title?: string;
  message: string;
  danger?: boolean;
}

type ConfirmFn = (opts: ConfirmOptions | string) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [state, setState] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);

  const confirm: ConfirmFn = (opts) => {
    const normalized = typeof opts === "string" ? { message: opts } : opts;
    setState(normalized);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  };

  const close = (result: boolean) => {
    resolver.current?.(result);
    resolver.current = null;
    setState(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state && (
        <Modal title={state.title ?? t("confirm.title")} onClose={() => close(false)}>
          <p style={{ marginTop: 0 }}>{state.message}</p>
          <div className="form-actions">
            <button className="ghost" onClick={() => close(false)}>
              {t("cancel")}
            </button>
            <button className={state.danger ? "danger" : ""} onClick={() => close(true)} autoFocus>
              {t("confirm.ok")}
            </button>
          </div>
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used within ConfirmProvider");
  return ctx;
}
