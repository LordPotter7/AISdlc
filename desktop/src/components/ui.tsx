import type { ReactNode } from "react";
import type { GateId, GateRecord, VerificationClass } from "@/lib/contract";
import { TASK_TONE, statusLabel } from "@/lib/format";

export type Tone = "ok" | "warn" | "bad" | "info" | "gate" | "muted";

export function Pill({ tone = "muted", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`pill ${tone}`} title={title}>
      {children}
    </span>
  );
}

const CLASS_NAME: Record<VerificationClass, string> = {
  D: "Deterministic: a command exits zero or not",
  S: "Statistical: a rate over a dataset against a predeclared threshold",
  J: "Judged: a calibrated judge against an agreement floor",
  N: "Adversarial: zero attacker successes",
};

/** Verification class as shape + letter + color (never color alone). */
export function ClassGlyph({ value }: { value: VerificationClass | string | undefined }) {
  const known = value === "D" || value === "S" || value === "J" || value === "N";
  if (!known) return <span className="pill muted">{value ?? "?"}</span>;
  return (
    <span className={`cls ${value}`} role="img" aria-label={`Class ${value}`} title={CLASS_NAME[value]}>
      {value}
    </span>
  );
}

export function TaskStatus({ status }: { status: string }) {
  const tone = TASK_TONE[status] ?? "muted";
  return <Pill tone={tone}>{status.replace(/_/g, " ")}</Pill>;
}

export function IntentStatus({ status }: { status: string | null }) {
  const tone: Tone =
    status === "completed" ? "ok" : status === "aborted" ? "bad" : status === "building" || status === "converging" ? "info" : "muted";
  return <Pill tone={tone}>{statusLabel(status)}</Pill>;
}

export function GateTag({ gate, record }: { gate: GateId; record: GateRecord | null | undefined }) {
  const status = record?.status ?? "undecided";
  const symbol = status === "approved" ? "✓" : status === "reopened" ? "↺" : "·";
  const label =
    status === "approved"
      ? `${gate} approved by ${record?.approver ?? "unrecorded"}`
      : status === "reopened"
        ? `${gate} reopened: ${record?.reason ?? "no reason recorded"}`
        : `${gate} not decided`;
  return (
    <span className={`gate-tag ${status}`} title={label} aria-label={label}>
      {gate} {symbol}
    </span>
  );
}

export function Panel({ title, aside, children, pad = false }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; pad?: boolean }) {
  return (
    <section className="panel">
      {title !== undefined && (
        <div className="panel-head">
          <h2>{title}</h2>
          {aside !== undefined && <span className="aside">{aside}</span>}
        </div>
      )}
      <div className={pad ? "panel-pad" : undefined}>{children}</div>
    </section>
  );
}

export function Loading({ height = 120 }: { height?: number }) {
  return <div className="skeleton" style={{ minHeight: height }} aria-busy="true" aria-label="Loading" />;
}

export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      {message}
      {retry && (
        <>
          {" "}
          <button className="btn sm" onClick={retry} type="button">
            Try again
          </button>
        </>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
