import { useState, type ReactNode } from "react";
import type { GateId, PipelinePayload, PipelineProfile } from "@/lib/contract";
import { plural } from "@/lib/format";
import { Icon } from "@/components/Icon";
import { OperationDialog, type OperationRequest } from "@/components/OperationDialog";
import { ErrorBox, IntentStatus, Loading, Panel, Pill } from "@/components/ui";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

const GATES: GateId[] = ["G1", "G2", "G3", "G4", "G5"];
const BARS = ["never", "error", "warning"];
type Change = "same" | "tight" | "loose";

/** One cell: its value, and how it compares with `standard`. A loosened cell names its waiver. */
function Cell({ change, children, waiver }: { change: Change; children: ReactNode; waiver?: PipelineProfile["loosened"][number]["waiver"] }) {
  return (
    <td className={`rule-cell ${change}`}>
      <div>{children}</div>
      {change === "tight" && <span className="rule-tag">stricter</span>}
      {change === "loose" && (
        <span className="rule-tag loose" title={waiver?.reason ?? ""}>
          <Icon name="alert" size={11} /> loosened, waived by {waiver?.approved_by ?? "?"}
        </span>
      )}
    </td>
  );
}

function waiverFor(profile: PipelineProfile, rule: string) {
  return profile.loosened.find((item) => item.rule === rule || item.rule.startsWith(`${rule}.`))?.waiver;
}

function gateText(profile: PipelineProfile) {
  const lines = GATES.map((gate) => {
    const policy = profile.gates[gate];
    if (!policy) return null;
    const who = policy.approvers.length ? ` from ${policy.approvers.join(", ")}` : "";
    return policy.approvals > 1 || who ? `${gate}: ${policy.approvals > 1 ? `${policy.approvals} different people` : "1 person"}${who}` : null;
  }).filter(Boolean);
  return lines.length ? lines.map((line) => <div key={line}>{line}</div>) : <span className="muted">one person each</span>;
}

function gateChange(profile: PipelineProfile, base: PipelineProfile): Change {
  if (profile.loosened.some((item) => item.rule.startsWith("gates."))) return "loose";
  return GATES.some((gate) => {
    const mine = profile.gates[gate];
    const theirs = base.gates[gate];
    return mine && theirs && (mine.approvals > theirs.approvals || mine.approvers.join() !== theirs.approvers.join());
  })
    ? "tight"
    : "same";
}

/**
 * The rules each kind of work runs under (ADR-010). The constitution is fixed; `standard`
 * is the framework defaults; each profile tightens them freely and loosens one only with a
 * named waiver. Read only: `pipeline.yaml` is changed in an editor and reviewed. Moving an
 * Intent to a stricter profile is the one decision made here, through the runner.
 */
export function PipelineView() {
  const navigate = useStudio((s) => s.navigate);
  const refresh = useStudio((s) => s.refresh);
  const { data, error, loading, reload } = useProjection<PipelinePayload>("pipeline.inspect");
  const [operation, setOperation] = useState<OperationRequest | null>(null);

  if (loading && !data) return <div className="page"><Loading height={420} /></div>;
  if (error) return <div className="page"><ErrorBox message={error} retry={reload} /></div>;
  if (!data) return null;

  const base = data.profiles.standard!;
  const names = ["standard", ...Object.keys(data.profiles).filter((name) => name !== "standard").sort()];
  const selectors = (name: string) => [
    ...Object.entries(data.by_type).filter(([, profile]) => profile === name).map(([kind]) => kind.replace(/_/g, " ")),
    ...(data.default === name ? ["every other type"] : []),
  ];
  const waivers = names.flatMap((name) => data.profiles[name]!.loosened.map((item) => ({ name, ...item })));

  return (
    <div className="page pipeline">
      <header className="page-head">
        <div>
          <h1>Pipeline</h1>
          <span className="sub">
            The rules each kind of work runs under. The constitution is fixed. Profiles tighten the framework defaults
            freely, and loosen one only with a named waiver. A profile is pinned when an Intent is created, so editing
            the pipeline never moves work in flight.
          </span>
        </div>
      </header>

      {data.error ? (
        <div className="note bad">
          <b>pipeline.yaml is invalid.</b> {data.error} New Intents cannot be created until it is fixed; Intents already
          created keep their pinned profile.
        </div>
      ) : !data.present ? (
        <div className="note info">
          No <span className="mono">.ai-sdlc/pipeline.yaml</span> yet, so every Intent runs <b>standard</b>, the framework
          defaults. Add profiles there to give a kind of work stricter rules (ADR-010).
        </div>
      ) : null}

      <section className="constitution" aria-labelledby="constitution-title">
        <h2 id="constitution-title"><Icon name="lock" size={15} /> The constitution</h2>
        <p className="muted">Fixed for every Intent and every profile. Changing any of these is an amendment to the constitution, never a setting.</p>
        <ul>
          {data.constitution.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      </section>

      <Panel title="Profiles" aside={`${names.length} including standard`}>
        <div className="table-wrap">
          <table className="grid profiles">
            <thead>
              <tr>
                <th>Rule</th>
                {names.map((name) => {
                  const profile = data.profiles[name]!;
                  return (
                    <th key={name} scope="col">
                      <span className="profile-name mono">{name}</span>
                      <span className="profile-desc">{profile.description}</span>
                      <span className="profile-meta">
                        {selectors(name).length ? `Selected by ${selectors(name).join(", ")}` : "Chosen by name only"}
                      </span>
                      <span className="profile-meta">{plural(data.pinned_counts[name] ?? 0, "Intent")} run it</span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">Artifact modes</th>
                {names.map((name) => {
                  const profile = data.profiles[name]!;
                  return (
                    <Cell key={name} change={profile.modes.length < base.modes.length ? "tight" : "same"}>
                      {profile.modes.join(" or ")}
                    </Cell>
                  );
                })}
              </tr>
              <tr>
                <th scope="row">Required before a stage</th>
                {names.map((name) => {
                  const entries = Object.entries(data.profiles[name]!.required_artifacts);
                  return (
                    <Cell key={name} change={entries.length ? "tight" : "same"}>
                      {entries.length ? entries.map(([stage, paths]) => (
                        <div key={stage}><span className="muted">{stage}:</span> <span className="mono">{paths.map((path) => path.split("/").pop()).join(", ")}</span></div>
                      )) : <span className="muted">nothing extra</span>}
                    </Cell>
                  );
                })}
              </tr>
              <tr>
                <th scope="row">Lint blocks G2 on</th>
                {names.map((name) => {
                  const profile = data.profiles[name]!;
                  const change: Change = BARS.indexOf(profile.fail_on) > BARS.indexOf(base.fail_on) ? "tight" : BARS.indexOf(profile.fail_on) < BARS.indexOf(base.fail_on) ? "loose" : "same";
                  return (
                    <Cell key={name} change={change} waiver={waiverFor(profile, "specification.fail_on")}>
                      {profile.fail_on === "never" ? "nothing (reported only)" : profile.fail_on === "warning" ? "warnings and errors" : "errors"}
                    </Cell>
                  );
                })}
              </tr>
              <tr>
                <th scope="row">Lint rules counted differently</th>
                {names.map((name) => {
                  const profile = data.profiles[name]!;
                  const entries = Object.entries(profile.rules);
                  const loose = profile.loosened.some((item) => item.rule.startsWith("specification.rules."));
                  return (
                    <Cell key={name} change={loose ? "loose" : entries.length ? "tight" : "same"} waiver={waiverFor(profile, "specification.rules")}>
                      {entries.length ? entries.map(([code, severity]) => <div key={code}><span className="mono">{code}</span> as {severity}</div>) : <span className="muted">none</span>}
                    </Cell>
                  );
                })}
              </tr>
              <tr>
                <th scope="row">Gate approvals</th>
                {names.map((name) => {
                  const profile = data.profiles[name]!;
                  return (
                    <Cell key={name} change={gateChange(profile, base)} waiver={waiverFor(profile, "gates")}>
                      {gateText(profile)}
                    </Cell>
                  );
                })}
              </tr>
              <tr>
                <th scope="row">Held-out waivers</th>
                {names.map((name) => {
                  const profile = data.profiles[name]!;
                  const change: Change = profile.holdout_waiver === base.holdout_waiver ? "same" : profile.holdout_waiver === "forbidden" ? "tight" : "loose";
                  return (
                    <Cell key={name} change={change} waiver={waiverFor(profile, "verification.holdout_waiver")}>
                      {profile.holdout_waiver === "forbidden" ? "forbidden" : "allowed with a named reason"}
                    </Cell>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>

      {waivers.length > 0 && (
        <Panel title="Waivers" aside={plural(waivers.length, "loosened default")}>
          <ul className="plain panel-pad waiver-list">
            {waivers.map((item) => (
              <li key={`${item.name}-${item.rule}`}>
                <Pill tone="warn">{item.name}</Pill> <span className="mono">{item.rule}</span> relaxed to <b>{String(item.to)}</b>
                <div className="muted">
                  Waived by {item.waiver?.approved_by} on {item.waiver?.approved_at}: “{item.waiver?.reason}”
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel title="Intents and their profiles" aside={plural(data.intents.length, "Intent")}>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Intent</th>
                <th>Type</th>
                <th>Stage</th>
                <th>Runs under</th>
                <th>Move to a stricter profile</th>
              </tr>
            </thead>
            <tbody>
              {data.intents.map((intent) => (
                <tr key={intent.id}>
                  <td>
                    <button type="button" className="chain-open" onClick={() => navigate({ view: "intent", intentId: intent.id, tab: "overview" })}>
                      <span className="mono">{intent.id}</span>
                      <span className="chain-text">{intent.title}</span>
                    </button>
                  </td>
                  <td>{intent.type?.replace(/_/g, " ")}</td>
                  <td><IntentStatus status={intent.status} /></td>
                  <td>
                    <span className="mono">{intent.profile}</span>
                    {intent.implicit && <span className="muted"> (created before profiles)</span>}
                    {intent.loosened.length > 0 && <Pill tone="warn" title={intent.loosened.join(", ")}>loosened by waiver</Pill>}
                  </td>
                  <td>
                    {intent.stricter.length === 0 ? (
                      <span className="muted">—</span>
                    ) : (
                      <select
                        className="priority-select"
                        aria-label={`Move ${intent.id} to a stricter profile`}
                        value=""
                        onChange={(event) =>
                          event.target.value &&
                          setOperation({
                            name: "intent_profile",
                            title: `Move ${intent.id} to the ${event.target.value} profile`,
                            args: { intent_id: intent.id, profile: event.target.value },
                            identityField: "actor",
                            reasonField: true,
                            explanation:
                              "Pins a profile at least as strict on every rule; its rules apply from the next transition. " +
                              "Recorded with your name and reason. There is no move back to a weaker profile.",
                          })
                        }
                      >
                        <option value="">Choose…</option>
                        {intent.stricter.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <p className="muted pipeline-foot">
        To change a profile, edit <span className="mono">.ai-sdlc/pipeline.yaml</span> and review it like any governance
        file; <span className="mono">ai-sdlc pipeline</span> and Doctor check it. Changes apply to Intents created
        afterwards.
      </p>

      {operation && (
        <OperationDialog
          request={operation}
          onClose={(changed) => {
            setOperation(null);
            reload();
            if (changed) void refresh();
          }}
        />
      )}
    </div>
  );
}
