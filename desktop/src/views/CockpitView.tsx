import { useEffect, useState } from "react";
import type { AuditEvent, GateId, IntentSummary, Portfolio } from "@/lib/contract";
import { ago, plural, statusLabel, sum } from "@/lib/format";
import { GatePacketPanel } from "@/components/GatePacketPanel";
import { ClassGlyph, Empty, ErrorBox, GateTag, Loading, Panel, Pill } from "@/components/ui";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

const COLUMNS: { title: string; statuses: string[] }[] = [
  { title: "Frame & specify", statuses: ["initialized", "framed", "specified"] },
  { title: "Plan", statuses: ["planned"] },
  { title: "Build", statuses: ["building"] },
  { title: "Converge & done", statuses: ["converging", "awaiting_acceptance", "completed", "aborted"] },
];

export function describeEvent(event: AuditEvent): string {
  if (event.event === "advance_refused") {
    const details = (event.details ?? {}) as { target?: string; check?: string; count?: number };
    return `Runner refused ${event.entity_id} → ${details.target} (${details.check}, ${details.count ?? "?"} problems)`;
  }
  if (event.event === "priority_set") {
    const details = (event.details ?? {}) as { priority?: string; reason?: string };
    return `${event.entity_id} priority set to ${details.priority} by ${event.actor ?? "unrecorded"}: ${details.reason ?? ""}`;
  }
  if (event.event === "gate_approval") {
    const details = (event.details ?? {}) as { approvals?: number; needed?: number };
    return `${event.entity_id} approved by ${event.actor ?? "unrecorded"}: ${details.approvals} of ${details.needed} approvals`;
  }
  if (event.event === "profile_changed") {
    const details = (event.details ?? {}) as { from?: string; to?: string; reason?: string };
    return `${event.entity_id} moved from the ${details.from} profile to ${details.to} by ${event.actor ?? "unrecorded"}: ${details.reason ?? ""}`;
  }
  if (event.event === "dependency_waived") {
    const details = (event.details ?? {}) as { predecessor?: string; reason?: string };
    return `${event.entity_id} may build before ${details.predecessor}, waived by ${event.actor ?? "unrecorded"}: ${details.reason ?? ""}`;
  }
  if (event.event && event.event !== "transition") return `${event.entity_id}: ${event.event.replace(/_/g, " ")}`;
  if (event.entity_type === "gate") return `${event.entity_id} ${event.to} by ${event.actor ?? "unrecorded"}`;
  return `${event.entity_id} ${event.from ?? "·"} → ${event.to}`;
}

function IntentCard({ intent, onOpen }: { intent: IntentSummary; onOpen(): void }) {
  const tasksDone = (intent.tasks.verified ?? 0) + (intent.tasks.merged ?? 0);
  const tasksTotal = sum(intent.tasks);
  const high = intent.open_findings.critical + intent.open_findings.high;
  return (
    <button type="button" className="intent-card" onClick={onOpen}>
      <span className="row">
        <span className="mono muted">{intent.id}</span>
        {intent.waiting_on ? <Pill tone="gate">{intent.waiting_on.gate} waiting</Pill> : <Pill tone="muted">{statusLabel(intent.status)}</Pill>}
      </span>
      <span className="title">{intent.title}</span>
      {tasksTotal > 0 && (
        <span className="bar" aria-label={`${tasksDone} of ${tasksTotal} Tasks verified`}>
          <i style={{ width: `${(tasksDone / tasksTotal) * 100}%`, background: "var(--ok)" }} />
          <i style={{ width: `${((intent.tasks.retry ?? 0) / tasksTotal) * 100}%`, background: "var(--warn)" }} />
        </span>
      )}
      <span className="meta">
        {intent.requirements != null && <span>{plural(intent.requirements, "req")}</span>}
        {(Object.keys(intent.classes) as (keyof typeof intent.classes)[]).map((cls) => (
          <span key={cls} style={{ display: "inline-flex", gap: 3, alignItems: "center" }}>
            <ClassGlyph value={cls} />
            {intent.classes[cls]}
          </span>
        ))}
        {tasksTotal > 0 && <span>{tasksDone}/{tasksTotal} tasks</span>}
        {intent.mode && <span>{intent.mode}</span>}
        {high > 0 && <Pill tone="bad">{high} high</Pill>}
        {intent.trigger_flags.multi_tenant && <Pill tone="info">multi-tenant</Pill>}
      </span>
    </button>
  );
}

export function CockpitView() {
  const navigate = useStudio((s) => s.navigate);
  // A repository that is not run by AISDLC (or has a broken config) still opens, so the
  // bridge's problems must be said here: otherwise the Cockpit reads as "0 Intents".
  const problems = useStudio((s) => s.project?.context.problems ?? []);
  const { data, error, loading, reload } = useProjection<Portfolio>("project.snapshot");
  const [selected, setSelected] = useState<{ intent: string; gate: GateId } | null>(null);

  useEffect(() => {
    if (!selected && data?.waiting[0]) setSelected({ intent: data.waiting[0].intent, gate: data.waiting[0].gate });
  }, [data, selected]);

  if (loading && !data) return <div className="page"><Loading height={400} /></div>;
  if (error) return <div className="page"><ErrorBox message={error} retry={reload} /></div>;
  if (!data) return null;
  const k = data.kpis;
  const open = (intentId: string) => navigate({ view: "intent", intentId, tab: "overview" });

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Cockpit</h1>
          <span className="sub">
            {plural(k.intents, "Intent")}, {k.waiting_gates ? `${plural(k.waiting_gates, "gate")} waiting on a person` : "no gate waiting"},{" "}
            {plural(data.audit_events, "runner event")} on the record.
          </span>
        </div>
      </header>

      {problems.length > 0 && (
        <div className="note" role="alert">
          {problems.map((problem) => <div key={problem}>{problem}</div>)}
        </div>
      )}

      <div className="kpis">
        <Panel>
          <div className="kpi">
            <span className="label">Waiting on a person</span>
            <span className="value" style={{ color: k.waiting_gates ? "var(--gate)" : undefined }}>{plural(k.waiting_gates, "gate")}</span>
            <span className="detail">{data.waiting.map((w) => `${w.intent} ${w.gate}`).join(", ") || "No gate is waiting"}</span>
          </div>
        </Panel>
        <Panel>
          <div className="kpi">
            <span className="label">Tasks verified with reviewed evidence</span>
            <span className="value">
              {k.tasks_verified} / {k.tasks_total}
            </span>
            <span className="bar">
              <i style={{ width: k.tasks_total ? `${(k.tasks_verified / k.tasks_total) * 100}%` : 0, background: "var(--ok)" }} />
            </span>
          </div>
        </Panel>
        <Panel>
          <div className="kpi">
            <span className="label">Open critical or high findings</span>
            <span className="value" style={{ color: k.open_high ? "var(--bad)" : undefined }}>{k.open_high}</span>
            <span className="detail">{k.open_high ? "These block convergence" : "None open"}</span>
          </div>
        </Panel>
        <Panel>
          <div className="kpi">
            <span className="label">Contract contention</span>
            <span className="value" style={{ color: k.contention ? "var(--warn)" : undefined }}>{k.contention}</span>
            <span className="detail">
              {data.contention[0] ? `${data.contention[0].resource} · ${data.contention[0].claimant} ↔ ${data.contention[0].intent}` : "No two active Intents share an operation"}
            </span>
          </div>
        </Panel>
      </div>

      <div className="grid-2">
        <div style={{ display: "grid", gap: 16 }}>
          <Panel title="Waiting on a person" aside="oldest first">
            {data.waiting.length === 0 ? (
              <Empty>No gate is waiting. The next moves belong to the team.</Empty>
            ) : (
              [...data.waiting]
                .sort((a, b) => (a.since ?? "").localeCompare(b.since ?? ""))
                .map((item) => {
                  const active = selected?.intent === item.intent && selected.gate === item.gate;
                  return (
                    <div key={`${item.intent}-${item.gate}`} className="waiting-card" style={active ? { background: "var(--selected)" } : undefined}>
                      <span className="gate-badge" aria-hidden="true">{item.gate}</span>
                      <button
                        type="button"
                        className="btn ghost"
                        style={{ justifyContent: "flex-start", textAlign: "left", whiteSpace: "normal", display: "block" }}
                        onClick={() => setSelected({ intent: item.intent, gate: item.gate })}
                        aria-pressed={active}
                      >
                        <b>{item.title}</b>
                        <br />
                        <span className="muted mono">{item.intent}</span> <span className="muted">· {item.gate} waiting {ago(item.since)}</span>
                      </button>
                      <button type="button" className="btn sm" onClick={() => open(item.intent)}>
                        Open Intent
                      </button>
                    </div>
                  );
                })
            )}
          </Panel>

          <section aria-label="Intents by stage" className="board">
            {COLUMNS.map((column) => {
              const intents = data.intents.filter((intent) => column.statuses.includes(intent.status ?? ""));
              return (
                <div className="column" key={column.title}>
                  <h3>
                    {column.title} <span>{intents.length}</span>
                  </h3>
                  {intents.map((intent) => (
                    <IntentCard key={intent.id} intent={intent} onOpen={() => open(intent.id)} />
                  ))}
                </div>
              );
            })}
          </section>

          <Panel title="Recent runner activity" aside={`latest 15 of ${data.audit_events}`}>
            {data.recent_events.map((event) => (
              <div className="event-row" key={`${event.sequence}-${event.entity_id}`}>
                <span className="muted num">{ago(event.at)}</span>
                <span className="what">{describeEvent(event)}</span>
              </div>
            ))}
          </Panel>
        </div>

        <Panel title={selected ? `Decision packet: ${selected.intent} ${selected.gate}` : "Decision packet"} pad>
          {selected ? (
            <GatePacketPanel intentId={selected.intent} gate={selected.gate} />
          ) : (
            <Empty>Select a waiting gate to see what approving it means.</Empty>
          )}
        </Panel>
      </div>

      <Panel title="Gate record by Intent">
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Intent</th>
                <th>Status</th>
                {(["G1", "G2", "G3", "G4", "G5"] as GateId[]).map((g) => (
                  <th key={g}>{g}</th>
                ))}
                <th>Next the runner allows</th>
              </tr>
            </thead>
            <tbody>
              {data.intents.map((intent) => (
                <tr key={intent.id} onClick={() => open(intent.id)} style={{ cursor: "pointer" }}>
                  <td>
                    <span className="mono">{intent.id}</span> {intent.title}
                  </td>
                  <td>{statusLabel(intent.status)}</td>
                  {(["G1", "G2", "G3", "G4", "G5"] as GateId[]).map((g) => (
                    <td key={g}>
                      <GateTag gate={g} record={intent.gates[g]} />
                    </td>
                  ))}
                  <td className="muted">{intent.next_legal_actions.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
