import { useEffect, useRef, useState } from "react";
import type { Envelope, OperationPreview, OperationResult } from "@/lib/contract";
import { errorMessage, getTransport } from "@/lib/ipc";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useStudio } from "@/state/store";
import { DraftReportView } from "./DraftReport";

export interface OperationRequest {
  name: string;
  title: string;
  args: Record<string, unknown>;
  /** Field the person must type (their own identity for a gate decision). */
  identityField?: "approver" | "actor";
  /** Extra free-text field, e.g. the reason for reopening a gate. */
  reasonField?: boolean;
  explanation: string;
}

type Phase = "input" | "previewing" | "confirm" | "running" | "done";

/**
 * Preview → confirm → the runner acts. The app never records a decision itself: the
 * command shown is exactly what runs, the runner applies every check it always applies,
 * and execution is refused if runner state moved after the preview.
 */
export function OperationDialog({
  request,
  onClose,
  onResult,
}: {
  request: OperationRequest;
  onClose(changed: boolean): void;
  /** Called once with the runner's answer, e.g. to open the Intent it just created. */
  onResult?(result: OperationResult): void;
}) {
  const projectId = useStudio((s) => s.project?.projectId);
  const [identity, setIdentity] = useState("");
  const [reason, setReason] = useState("");
  const [phase, setPhase] = useState<Phase>(request.identityField || request.reasonField ? "input" : "previewing");
  const [preview, setPreview] = useState<OperationPreview | null>(null);
  const [result, setResult] = useState<OperationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useStudio((s) => s.toast);
  const root = useRef<HTMLDivElement>(null);
  useFocusTrap(root, true);

  const args = { ...request.args };
  if (request.identityField) args[request.identityField] = identity.trim();
  if (request.reasonField) args.reason = reason.trim();

  async function runPreview() {
    if (!projectId) return;
    setError(null);
    setPhase("previewing");
    try {
      const envelope = await getTransport().bridgeRequest<OperationPreview>("operation.preview", { project_id: projectId, name: request.name, args });
      if (envelope.error) throw new Error(envelope.error.message);
      setPreview(envelope.payload);
      setPhase("confirm");
    } catch (err) {
      setError(errorMessage(err));
      setPhase(request.identityField || request.reasonField ? "input" : "confirm");
    }
  }

  useEffect(() => {
    if (phase === "previewing" && !preview && !request.identityField && !request.reasonField) void runPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function execute() {
    if (!projectId || !preview) return;
    setPhase("running");
    setError(null);
    try {
      const envelope: Envelope<OperationResult> = await getTransport().bridgeRequest<OperationResult>("operation.execute", {
        project_id: projectId,
        name: request.name,
        args,
        token: preview.token,
        base_hashes: preview.base_hashes,
      });
      if (envelope.error) throw new Error(envelope.error.message);
      setResult(envelope.payload);
      setPhase("done");
      onResult?.(envelope.payload);
      toast(
        envelope.payload.succeeded
          ? { tone: "ok", title: request.title, detail: "The runner completed it and recorded it in the audit log." }
          : { tone: "bad", title: `${request.title}: refused`, detail: envelope.payload.error_text ?? `exit ${envelope.payload.exit_code}` },
      );
    } catch (err) {
      setError(errorMessage(err));
      setPhase("confirm");
      toast({ tone: "bad", title: `${request.title}: not run`, detail: errorMessage(err) });
    }
  }

  async function cancel() {
    if (projectId && preview && phase === "confirm") {
      void getTransport().bridgeRequest("operation.cancel", { project_id: projectId, token: preview.token });
    }
    onClose(phase === "done" && !!result?.succeeded);
  }

  const identityValid = !request.identityField || /^[\w .@'-]{1,80}$/.test(identity.trim());
  const reasonValid = !request.reasonField || (reason.trim().length > 0 && reason.length <= 500);

  return (
    <div className="overlay" role="presentation" onKeyDown={(e) => e.key === "Escape" && void cancel()}>
      <div className={`dialog${preview?.draft ? " wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="op-title" ref={root} tabIndex={-1}>
        <div>
          <span className="eyebrow">Runner operation</span>
          <h2 id="op-title">{request.title}</h2>
        </div>
        <p className="ink2" style={{ margin: 0 }}>{request.explanation}</p>

        {phase === "input" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (identityValid && reasonValid) void runPreview();
            }}
            style={{ display: "grid", gap: 12 }}
          >
            {request.identityField && (
              <label htmlFor="op-identity">
                {request.identityField === "approver" ? "Your name, as the approver on record" : "Your name, as the actor on record"}
                <input id="op-identity" autoFocus value={identity} onChange={(e) => setIdentity(e.target.value)} placeholder="e.g. priya.product" />
              </label>
            )}
            {request.reasonField && (
              <label htmlFor="op-reason">
                Reason (recorded in the audit log)
                <input id="op-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
              </label>
            )}
            <div className="row">
              <button type="button" className="btn" onClick={() => void cancel()}>
                Cancel
              </button>
              <button type="submit" className="btn primary" disabled={!identityValid || !reasonValid}>
                Preview
              </button>
            </div>
          </form>
        )}

        {phase === "previewing" && <p className="muted">Asking the runner for a preview…</p>}

        {(phase === "confirm" || phase === "running") && preview && (
          <>
            <div>
              <span className="eyebrow">Exactly this runs</span>
              <div className="cmd">{preview.command}</div>
            </div>
            <p style={{ margin: 0 }} className="ink2">{preview.effect}</p>
            {preview.draft && <DraftReportView draft={preview.draft} />}
            {preview.writes && !preview.draft && (
              <div className="note">
                This records a decision in runner state and the audit log. The runner still applies every check it always does; it
                refuses if anything changed since this preview.
              </div>
            )}
            <div className="row">
              <button type="button" className="btn" onClick={() => void cancel()} disabled={phase === "running"}>
                Cancel
              </button>
              <button
                type="button"
                className={`btn ${preview.writes ? "gate" : "primary"}`}
                onClick={() => void execute()}
                disabled={phase === "running" || !!preview.draft?.refusal || preview.draft?.changed === false}
              >
                {phase === "running" ? "Running…" : preview.draft ? "Save through the runner" : preview.writes ? "Confirm and run" : "Run"}
              </button>
            </div>
          </>
        )}

        {phase === "done" && result && (
          <>
            <div className={result.succeeded ? "note info" : "error-box"}>
              {result.succeeded ? "The runner completed the operation." : `The runner refused (exit ${result.exit_code}).`}
              {result.error_text && <div style={{ marginTop: 6 }}>{result.error_text}</div>}
            </div>
            {result.output != null && (
              <details>
                <summary className="muted">What the runner returned</summary>
                <pre className="result">{JSON.stringify(result.output, null, 2)}</pre>
              </details>
            )}
            <div className="row">
              <button type="button" className="btn primary" onClick={() => onClose(result.succeeded)}>
                Close
              </button>
            </div>
          </>
        )}

        {error && <div className="error-box">{error}</div>}
      </div>
    </div>
  );
}
