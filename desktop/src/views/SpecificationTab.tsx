import { useState } from "react";
import type { Criterion, IntentDetail, LintFinding, SpecPayload } from "@/lib/contract";
import { plural } from "@/lib/format";
import { ClassGlyph, Empty, ErrorBox, Loading, Panel, Pill, type Tone } from "@/components/ui";
import { OperationDialog, type OperationRequest } from "@/components/OperationDialog";
import { SourceLink, Term, useLintRule } from "@/components/Guide";
import { RequirementComposer } from "@/components/RequirementComposer";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

const QUALITY = [
  "performance", "scalability", "availability", "reliability", "security", "privacy", "compliance", "accessibility",
  "usability", "maintainability", "portability", "compatibility", "observability", "operability", "safety", "cost",
];
const STRIDE = ["spoofing", "tampering", "repudiation", "information_disclosure", "denial_of_service", "elevation_of_privilege"];
const PRIORITY_TONE: Record<string, Tone> = { must: "info", should: "muted", could: "muted", wont: "muted" };
const SEVERITY_TONE: Record<string, Tone> = { error: "bad", warning: "warn", info: "muted" };

function CriterionLine({ criterion, intentId }: { criterion: Criterion; intentId: string }) {
  return (
    <div className="crit">
      <ClassGlyph value={criterion.verification_class} />
      <span>
        <span className="mono">{criterion.id}</span> {criterion.statement} <SourceLink intentId={intentId} source={criterion.source} />
        {criterion.check && (
          <>
            <br />
            <span className="mono muted">$ {criterion.check}</span>
          </>
        )}
        {criterion.contract_operations?.length ? (
          <>
            <br />
            <span className="muted">verifies {criterion.contract_operations.join(", ")}</span>
          </>
        ) : null}
      </span>
    </div>
  );
}

/** One lint finding, with what its rule means and a link to the line it names. */
function FindingLine({ finding, intentId }: { finding: LintFinding; intentId: string }) {
  const rule = useLintRule(finding.rule);
  return (
    <div className="finding">
      <Pill tone={SEVERITY_TONE[finding.severity] ?? "muted"}>{finding.severity}</Pill>
      <span className="mono" title={rule ? `${rule.title}. ${rule.explanation}` : undefined}>
        {finding.rule} {finding.location}
      </span>
      <span className="ink2">
        {finding.message}
        {rule && <span className="muted"> Fix: {rule.fix}</span>} <SourceLink intentId={intentId} source={finding.source} />
      </span>
    </div>
  );
}

const CHECKS: Record<"validate_intent" | "spec_lint", Omit<OperationRequest, "args">> = {
  validate_intent: {
    name: "validate_intent",
    title: "Validate the Intent",
    explanation: "Runs the runner's schema and reference checks on this Intent. It writes nothing.",
  },
  spec_lint: {
    name: "spec_lint",
    title: "Lint the specification",
    explanation: "Runs the requirement-quality rules (SQ001–SQ018) on this specification. It writes nothing.",
  },
};

export function SpecificationTab({ intentId }: { intentId: string }) {
  const { data, error, loading, reload } = useProjection<SpecPayload>("spec.inspect", { intent_id: intentId });
  const [check, setCheck] = useState<OperationRequest | null>(null);
  const [composing, setComposing] = useState(false);
  const intent = useProjection<IntentDetail>("intent.inspect", { intent_id: intentId }).data;
  const openFile = useStudio((s) => s.openFile);
  const refresh = useStudio((s) => s.refresh);
  // The runner's rule, read from its state: approved G2 locks the spec; a finished Intent never changes.
  const lockedBy =
    intent?.status === "completed" || intent?.status === "aborted"
      ? `${intentId} is ${intent.status}`
      : intent?.gates.G2?.status === "approved"
        ? "G2 approval"
        : null;
  if (loading && !data) return <Loading height={300} />;
  if (error) return <ErrorBox message={error} retry={reload} />;
  if (!data) return null;
  if (!data.present) return <Empty>No specification yet. It is written in Stage 2 with /aisdlc:specify.</Empty>;

  const criteria = new Map((data.criteria ?? []).map((c) => [c.id, c]));
  const lint = data.lint;
  const findings: LintFinding[] = lint?.findings ?? [];
  const coverage = lint?.quality_coverage ?? {};
  const threatModel = data.threat_model;
  const strideGaps = data.stride_gaps ?? {};

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="toolbar">
        <span className="muted">Checks run by the runner, exactly as <span className="mono">ai-sdlc</span> runs them:</span>
        <button type="button" className="btn sm" onClick={() => setCheck({ ...CHECKS.validate_intent, args: { intent_id: intentId } })}>
          Validate
        </button>
        <button type="button" className="btn sm" onClick={() => setCheck({ ...CHECKS.spec_lint, args: { intent_id: intentId } })}>
          Lint
        </button>
        <span style={{ flex: 1 }} />
        {lockedBy ? (
          <span className="muted">
            🔒 Locked by {lockedBy}.{" "}
            {!lockedBy.includes(" is ") && (
              <button type="button" className="btn sm" onClick={() => openFile({ scope: "intent", intentId, path: "02-specification/requirements.yaml" })}>
                Propose a change…
              </button>
            )}
          </span>
        ) : (
          <button type="button" className="btn sm primary" onClick={() => setComposing(true)}>
            + Requirement
          </button>
        )}
      </div>
      {composing && (
        <RequirementComposer
          intentId={intentId}
          onClose={(changed) => {
            setComposing(false);
            if (changed) {
              reload();
              void refresh();
            }
          }}
        />
      )}
      {check && (
        <OperationDialog
          request={check}
          onClose={() => {
            setCheck(null);
            reload();
          }}
        />
      )}
      {(data.validation_errors?.length ?? 0) > 0 && (
        <Panel title="Runner validation" aside="these block advancing">
          <div className="list panel-pad">
            {data.validation_errors!.map((message) => (
              <div className="item" key={message}>
                <span className="dot bad" /> {message}
              </div>
            ))}
          </div>
        </Panel>
      )}

      <div className="grid-2">
        <Panel title="Requirements" aside={`${plural(data.requirements?.length ?? 0, "requirement")} · ${plural(criteria.size, "criterion", "criteria")}`}>
          {(data.requirements ?? []).map((req) => (
            <div key={req.id} className={`req ${req.priority === "wont" ? "wont" : ""}`}>
              <div className="head">
                <span className="mono">{req.id}</span>
                <SourceLink intentId={intentId} source={req.source} />
                {req.priority ? <Pill tone={PRIORITY_TONE[req.priority] ?? "muted"}>{req.priority === "wont" ? "won't (deferred)" : req.priority}</Pill> : <Pill tone="warn">no priority</Pill>}
                {req.category && <Pill tone="info">{req.category}</Pill>}
                {req.personal_data && <Pill tone="warn">personal data</Pill>}
                {req.data_classification && <Pill tone="muted">{req.data_classification}</Pill>}
                {req.compliance?.map((c) => (
                  <Pill key={`${c.framework}-${c.control}`} tone="muted">
                    {c.framework} {c.control}
                  </Pill>
                ))}
                {req.wcag && <Pill tone="info">WCAG {req.wcag.version} {req.wcag.level}</Pill>}
              </div>
              <div className="statement">{req.statement}</div>
              {req.measure && (
                <div className="ink2" style={{ fontSize: 12.5 }}>
                  Measure: {req.measure.metric}, target {String(req.measure.target)} {req.measure.unit ?? ""}
                  {req.measure.condition ? ` under ${req.measure.condition}` : ""}
                </div>
              )}
              {(req.rationale || req.sources?.length) && (
                <div className="muted" style={{ fontSize: 12 }}>
                  {req.rationale} {req.sources?.length ? `· from ${req.sources.join("; ")}` : ""}
                </div>
              )}
              {req.acceptance_criteria.map((id) => {
                const criterion = criteria.get(id);
                return criterion ? <CriterionLine key={id} criterion={criterion} intentId={intentId} /> : (
                  <div className="crit" key={id}>
                    <Pill tone="bad">missing</Pill> <span className="mono">{id}</span>
                  </div>
                );
              })}
            </div>
          ))}
        </Panel>

        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <Panel title={<Term id="spec-lint">Specification quality</Term>} aside={lint ? (lint.passed ? `passes at ${lint.fail_on}` : `blocked at ${lint.fail_on}`) : "not run"}>
            {!lint || lint.error ? (
              <Empty>{lint?.error ?? "The linter has not run on this specification."}</Empty>
            ) : findings.length === 0 ? (
              <Empty>No findings. Every requirement is singular, measurable, and traced.</Empty>
            ) : (
              findings.map((finding, index) => (
                <FindingLine key={`${finding.rule}-${finding.location}-${index}`} finding={finding} intentId={intentId} />
              ))
            )}
          </Panel>
          <Panel title="Quality attributes" aside="silent categories should be a decision" pad>
            <div className="coverage">
              {QUALITY.map((category) => (
                <span key={category} className={coverage[category] ? "on" : undefined} title={coverage[category]?.join(", ") ?? "not addressed"}>
                  {category}
                </span>
              ))}
            </div>
          </Panel>
          {(data.success_criteria?.length ?? 0) > 0 && (
            <Panel title="Success criteria">
              {data.success_criteria!.map((sc) => (
                <div className="req" key={sc.id}>
                  <div className="head">
                    <span className="mono">{sc.id}</span>
                    {sc.acceptance_criteria?.length ? <span className="muted">shown by {sc.acceptance_criteria.join(", ")}</span> : <Pill tone="warn">no criterion</Pill>}
                  </div>
                  <div className="statement">{sc.statement}</div>
                  {sc.brief_measure && <div className="muted" style={{ fontSize: 12 }}>Brief: {sc.brief_measure}</div>}
                </div>
              ))}
            </Panel>
          )}
        </div>
      </div>

      {data.contracts && data.contracts.operations.length > 0 && (
        <Panel title={<Term id="contract">Interface contracts</Term>} aside="every operation covered or excluded with a reason">
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>Contract</th>
                  <th>Verified by</th>
                </tr>
              </thead>
              <tbody>
                {data.contracts.operations.map((op) => (
                  <tr key={op.reference}>
                    <td className="mono">{op.name}</td>
                    <td className="mono muted">{op.contract}</td>
                    <td>
                      {op.covered_by.length ? (
                        <span className="mono">{op.covered_by.join(", ")}</span>
                      ) : op.excluded_reason ? (
                        <span className="muted">excluded: {op.excluded_reason}</span>
                      ) : (
                        <Pill tone="bad">not covered</Pill>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(data.contracts.breaking_changes.length > 0 || data.contracts.contention.length > 0) && (
            <div className="list panel-pad">
              {data.contracts.breaking_changes.map((change) => (
                <div className="item" key={`${change.operation}-${change.change}`}>
                  <span className={`dot ${change.accepted_by ? "warn" : "bad"}`} />
                  <span>
                    Breaking vs {change.baseline_intent}: <span className="mono">{change.api}#{change.operation}</span> {change.change}
                    {change.accepted_by ? ` — accepted by ${change.accepted_by}` : ""}
                  </span>
                </div>
              ))}
              {data.contracts.contention.map((item) => (
                <div className="item" key={`${item.resource}-${item.intent}`}>
                  <span className="dot warn" />
                  <span>
                    <span className="mono">{item.resource}</span> is also covered by {item.intent} ({item.status}). Serialise or co-approve.
                  </span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}

      {(threatModel || (data.inherited_threats?.length ?? 0) > 0) && (
        <Panel title={<Term id="threat-model">Threat model</Term>} aside={<Term id="stride">STRIDE per trust boundary</Term>}>
          {threatModel ? (
            <>
              <div className="table-wrap">
                <table className="grid">
                  <thead>
                    <tr>
                      <th>Boundary</th>
                      {STRIDE.map((category) => (
                        <th key={category}>{category.replace(/_/g, " ")}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {threatModel.boundaries.map((boundary) => (
                      <tr key={boundary.id}>
                        <td>
                          <span className="mono">{boundary.id}</span> {boundary.name}
                        </td>
                        {STRIDE.map((category) => {
                          const threat = threatModel.threats.find((t) => t.boundary === boundary.id && t.stride === category);
                          const na = boundary.not_applicable?.find((item) => item.category === category);
                          return (
                            <td key={category} title={threat?.description ?? na?.reason ?? "silent"}>
                              {threat ? (
                                <Pill tone={threat.disposition === "mitigate" ? "ok" : "warn"}>
                                  {threat.id} {threat.disposition}
                                </Pill>
                              ) : na ? (
                                <span className="muted">n/a</span>
                              ) : (
                                <Pill tone="bad">silent</Pill>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="list panel-pad">
                {threatModel.threats.map((threat) => (
                  <div className="item" key={threat.id}>
                    <span className={`dot ${threat.disposition === "mitigate" ? "ok" : "warn"}`} />
                    <span>
                      <span className="mono">{threat.id}</span> {threat.description}{" "}
                      <SourceLink intentId={intentId} source={threat.source} />{" "}
                      {threat.inherited_from && <Pill tone="gate">inherited {threat.inherited_from}</Pill>}{" "}
                      {threat.disposition === "mitigate" ? (
                        <span className="muted">
                          mitigated by {threat.mitigated_by?.join(", ")}, proven by {threat.verified_by?.join(", ")}
                        </span>
                      ) : (
                        <span className="muted">
                          {threat.disposition} by {threat.accepted_by}: {threat.reason}
                        </span>
                      )}
                    </span>
                  </div>
                ))}
                {Object.entries(strideGaps).map(([boundary, missing]) => (
                  <div className="item" key={boundary}>
                    <span className="dot warn" /> {boundary} is silent on {missing.join(", ").replace(/_/g, " ")}.
                  </div>
                ))}
              </div>
            </>
          ) : (
            <Empty>Promoted threats apply to this Intent but it has no threat model yet.</Empty>
          )}
        </Panel>
      )}

      {(data.glossary?.length ?? 0) > 0 && (
        <Panel title="Glossary" aside="terms precise by declaration">
          <div className="list panel-pad">
            {data.glossary!.map((entry) => (
              <div className="item" key={entry.term}>
                <b>{entry.term}</b> <span className="ink2">{entry.definition}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
