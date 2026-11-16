import { useEffect, useRef, useState } from "react";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useStudio } from "@/state/store";
import { ArtifactPane } from "./ArtifactPane";

/**
 * The file drawer: any file opened from anywhere in Studio, over the current view. The
 * work happens in `ArtifactPane`, the same pane an Intent's Files tab uses: reading goes
 * through `artifact.read`, the runner checks drafts as you type (`artifact.check`), and
 * saving goes through `artifact_write`, a preview with the diff and the runner's checks.
 */
export function FileViewer() {
  const target = useStudio((s) => s.viewer);
  const close = useStudio((s) => s.closeFile);
  const openFile = useStudio((s) => s.openFile);
  const panel = useRef<HTMLElement>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  useFocusTrap(panel, !!target && !busy);

  useEffect(() => {
    if (!target) return;
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      // The editor uses Escape to close its own popups; those arrive already handled.
      if (event.key === "Escape" && !event.defaultPrevented && !busy && !dirty) close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [target, close, busy, dirty]);

  if (!target) return null;
  return (
    <div className="drawer-scrim" onClick={() => !dirty && close()}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="viewer-title"
        ref={panel}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <ArtifactPane
          target={target}
          layout="drawer"
          onClose={close}
          onOpen={(path, line) => openFile({ scope: target.scope, intentId: target.intentId, path, line })}
          onDirty={setDirty}
          onBusy={setBusy}
        />
      </aside>
    </div>
  );
}
