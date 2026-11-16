import { useStudio } from "@/state/store";
import { Icon } from "./Icon";

const ICON = { ok: "check", bad: "alert", info: "refresh", gate: "gate" } as const;

/**
 * What just happened, in the runner's words, without taking focus. One polite live region,
 * so a screen reader announces each result once; refusals stay on screen longer.
 */
export function Toaster() {
  const toasts = useStudio((s) => s.toasts);
  const dismiss = useStudio((s) => s.dismissToast);
  return (
    <div className="toaster" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.tone}`}>
          <Icon name={ICON[toast.tone]} size={16} className="toast-icon" />
          <div className="toast-body">
            <b>{toast.title}</b>
            {toast.detail && <span>{toast.detail}</span>}
          </div>
          <button type="button" className="icon-btn" aria-label="Dismiss" onClick={() => dismiss(toast.id)}>
            <Icon name="close" size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
