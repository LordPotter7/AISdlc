import { useState } from "react";
import type { GateId, GatePacketPayload } from "@/lib/contract";
import { plural } from "@/lib/format";
import { useProjection } from "@/state/useProjection";
import { Term } from "./Guide";
import { OperationDialog, type OperationRequest } from "./OperationDialog";
import { ErrorBox, Loading, Pill } from "./ui";

const QUALITY_CATEGORIES = [
  "performance", "scalability", "availability", "reliability", "security", "privacy", "compliance", "accessibility",
  "usability", "maintainability", "portability", "compatibility", "observability", "operability", "safety", "cost",
];

/**
 * A two-minute decision on a prepared packet (W2.3). Everything deterministic is already
 * here — outstanding checks, spec quality, the price of approving a reopened Gate 2, the
 * adversarial review and whether it is still current — so the person decides on judgement.
 */
export function GatePacketPanel({ intentId, gate, compact = false }: { intentId: string; gate: GateId; compact?: boolean }) {
  const { data, error, loading, reload } = useProjection<GatePacketPayload>("gate.packet", { intent_id: intentId, gate });
  const [operation, setOperation] = useState<OperationRequest | null>(null);

  if (loading && !data) return <Loading height={260} />;
  if (error) return <ErrorBox message={error} retry={reload} />;
  if (!data) return null;
  const packet = data.packet;
  const review = packet.adversarial_review;
  const quality = packet.specification_quality;
  const diff = packet.specification_diff;
  const approved = packet.gate_status === "approved";
  const changed = diff ? diff.requirements.added.length + diff.requirements.changed.length + diff.requirements.removed.length : 0;

  return (
    <div className="packet" aria-label={`${gate} decision packet for ${intentId}`}>
      <div>
        <span className="eyebrow">
          {gate} decision for {intentId}
          {packet.revision ? `, revision ${packet.revision}` : ""}
        </span>
        {!compact && <h2 style={{ fontSize: 16, marginTop: 2 }}>{packet.title}</h2>}
      </div>
      <p className="note question" style={{ margin: 0 }}>
        {packet.question}
      </p>

      <section>
        <h3>Runner says outstanding</h3>
        {packet.outstanding.length === 0 ? (
          <div className="list">
            <div className="item">
              <span className="dot ok" /> Nothing outstanding. The runner found no deterministic blocker for this gate.
            </div>
          </div>
        ) : (
          <div className="list">
            {packet.outstanding.map((item) => (
              <div className="item" key={item}>
                <span className="dot bad" />
                <span>{item}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {diff && !diff.error && (
        <section>
          <h3><Term id="price-of-approving">Price of approving</Term> (since revision {diff.baseline_revision ?? "?"})</h3>
          <div className="price">
            <div>
              <b>{changed}</b>
              <span>requirements moved</span>
            </div>
            <div>
              <b>{diff.criteria.added.length + diff.criteria.changed.length + diff.criteria.removed.length}</b>
              <span>criteria moved</span>
            </div>
            <div>
              <b style={{ color: diff.tasks_losing_evidence.length ? "var(--warn)" : undefined }}>{diff.tasks_losing_evidence.length}</b>
              <span>Tasks lose evidence</span>
            </div>
          </div>
          {changed > 0 && (
            <div className="list">
              {diff.requirements.added.map((id) => (
                <div className="item" key={`a-${id}`}>
                  <Pill tone="info">added</Pill> <span className="mono">{id}</span>
                </div>
              ))}
              {diff.requirements.changed.map((id) => (
                <div className="item" key={`c-${id}`}>
                  <Pill tone="warn">changed</Pill> <span className="mono">{id}</span>
                </div>
              ))}
              {diff.requirements.removed.map((id) => (
                <div className="item" key={`r-${id}`}>
                  <Pill tone="bad">removed</Pill> <span className="mono">{id}</span>
                </div>
              ))}
              {diff.tasks_losing_evidence.map((item) => (
                <div className="item" key={item.task}>
                  <span className="dot warn" />
                  <span>
                    <span className="mono">{item.task}</span> evidence goes stale ({item.requirements.join(", ")})
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {quality && !quality.error && (
        <section>
          <h3>Specification quality</h3>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Pill tone={quality.counts.error ? "bad" : "ok"}>{plural(quality.counts.error ?? 0, "error")}</Pill>
            <Pill tone={quality.counts.warning ? "warn" : "muted"}>{plural(quality.counts.warning ?? 0, "warning")}</Pill>
            <Pill tone="muted">{plural(quality.counts.info ?? 0, "note")}</Pill>
            <Pill tone="muted">
              {quality.prioritised ?? 0}/{quality.requirements ?? 0} prioritised
            </Pill>
          </div>
          {!compact && (
            <div className="coverage" aria-label="Quality attributes addressed">
              {QUALITY_CATEGORIES.map((category) => (
                <span key={category} className={quality.quality_coverage?.[category] ? "on" : undefined} title={quality.quality_coverage?.[category]?.join(", ") ?? "not addressed"}>
                  {category}
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      {review.applicable && (
        <section>
          <h3>Adversarial review</h3>
          {!review.recorded ? (
            <div className="list">
              <div className="item">
                <span className="dot bad" /> No review recorded. Delegate to the red-team agent from a fresh context before deciding.
              </div>
            </div>
          ) : (
            <div className="list">
              <div className="item">
                <span className={`dot ${review.current ? "ok" : "warn"}`} />
                <span>
                  {review.reviewer_model ?? "unrecorded model"}: <b>{review.decision}</b> with {plural(review.findings ?? 0, "finding")}
                  {!review.current && <> — <b>stale:</b> {review.stale_reason}</>}
                </span>
              </div>
            </div>
          )}
        </section>
      )}

      {packet.open_findings.length > 0 && (
        <section>
          <h3>Open findings</h3>
          <div className="list">
            {packet.open_findings.map((finding) => (
              <div className="item" key={finding.id}>
                <Pill tone={finding.severity === "high" || finding.severity === "critical" ? "bad" : "warn"}>{finding.severity}</Pill>
                <span>
                  <span className="mono">{finding.id}</span> {finding.summary}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {approved ? (
          <>
            <Pill tone="ok">Approved by {packet.previous_approver ?? "unrecorded"}</Pill>
            <button
              type="button"
              className="btn"
              onClick={() =>
                setOperation({
                  name: "gate_reopen",
                  title: `Reopen ${gate} for ${intentId}`,
                  args: { intent_id: intentId, gate },
                  identityField: "actor",
                  reasonField: true,
                  explanation: "Reopening is a recorded act. Evidence pinned to the approved version may go stale once the artifact changes.",
                })
              }
            >
              Reopen…
            </button>
          </>
        ) : (
          <button
            type="button"
            className="btn gate"
            onClick={() =>
              setOperation({
                name: "gate_approve",
                title: `Approve ${gate} for ${intentId}`,
                args: { intent_id: intentId, gate },
                identityField: "approver",
                explanation: `You are answering: ${packet.question}`,
              })
            }
          >
            Approve {gate} as…
          </button>
        )}
      </div>
      <span className="muted" style={{ fontSize: 12 }}>
        The app never approves a gate. The runner records your decision through the same command you would type.
      </span>

      {operation && (
        <OperationDialog
          request={operation}
          onClose={(changed) => {
            setOperation(null);
            if (changed) reload();
          }}
        />
      )}
    </div>
  );
}
