import { useEffect, useRef, useState } from "react";
import type { Envelope } from "@/lib/contract";
import { errorMessage, getTransport, type BridgeOp } from "@/lib/ipc";
import { useStudio } from "./store";

export interface Projection<P> {
  data: P | null;
  envelope: Envelope<P> | null;
  loading: boolean;
  error: string | null;
  reload(): void;
}

/**
 * Fetch one bridge projection for the open project, refetching whenever the project's
 * generation moves (live updates) or the user refreshes. Responses that arrive out of
 * order are dropped, so a slow reply can never overwrite a newer one.
 */
export function useProjection<P>(op: BridgeOp, params: Record<string, unknown> = {}, enabled = true): Projection<P> {
  const project = useStudio((s) => s.project);
  const nonce = useStudio((s) => s.refreshNonce);
  const [local, setLocal] = useState(0);
  const [state, setState] = useState<{ data: P | null; envelope: Envelope<P> | null; loading: boolean; error: string | null }>({
    data: null,
    envelope: null,
    loading: true,
    error: null,
  });
  const sequence = useRef(0);
  const key = JSON.stringify(params);

  useEffect(() => {
    if (!project || !enabled) return;
    const mine = ++sequence.current;
    setState((prev) => ({ ...prev, loading: true }));
    getTransport()
      .bridgeRequest<P>(op, { project_id: project.projectId, ...JSON.parse(key) })
      .then((envelope) => {
        if (mine !== sequence.current) return;
        setState({
          data: envelope.error ? null : envelope.payload,
          envelope,
          loading: false,
          error: envelope.error ? envelope.error.message : null,
        });
      })
      .catch((err) => {
        if (mine !== sequence.current) return;
        setState({ data: null, envelope: null, loading: false, error: errorMessage(err) });
      });
  }, [op, key, project?.projectId, project?.generation, nonce, local, enabled]);

  return { ...state, reload: () => setLocal((n) => n + 1) };
}
