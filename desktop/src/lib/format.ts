/** Display helpers. Unknown stays visibly unknown; nothing here invents a value. */

export function ago(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "time not recorded";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "time not recorded";
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} d ago`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const STATUS_LABEL: Record<string, string> = {
  initialized: "Registered",
  framed: "Framed",
  specified: "Specified",
  planned: "Planned",
  building: "Building",
  converging: "Converging",
  awaiting_acceptance: "Awaiting acceptance",
  completed: "Completed",
  aborted: "Aborted",
};

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Not in runner state";
  return STATUS_LABEL[status] ?? `${status} (unknown)`;
}

export const GATE_QUESTION: Record<string, string> = {
  G1: "Is this the right problem, framed correctly?",
  G2: "Are the requirements complete, testable, and correctly classified?",
  G3: "Is the plan sound, ordered, and safely parallelised?",
  G4: "Does the walking skeleton prove the approach?",
  G5: "Is this Intent complete, converged, and acceptable?",
};

export const TASK_TONE: Record<string, "ok" | "warn" | "bad" | "info" | "muted"> = {
  verified: "ok",
  merged: "ok",
  review: "info",
  verifying: "info",
  in_progress: "info",
  ready: "muted",
  pending: "muted",
  retry: "warn",
  blocked: "bad",
  failed: "bad",
  reverted: "bad",
  superseded: "muted",
};

export const GAP_LABEL: Record<string, string> = {
  success_unlinked: "Success measure without a criterion",
  requirement_unplanned: "Requirement with no Task",
  criterion_orphan: "Criterion no requirement owns",
  criterion_untasked: "Criterion no Task declares",
  evidence_missing: "Verified without evidence",
  evidence_stale: "Evidence pinned to an older spec",
};

export function sum(values: Record<string, number> | undefined): number {
  return Object.values(values ?? {}).reduce((total, value) => total + value, 0);
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}
