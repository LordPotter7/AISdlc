import type { EventsPayload, GateId, IntentDetail } from "@/lib/contract";
import { ago, plural, statusLabel, when } from "@/lib/format";
import { GatePacketPanel } from "@/components/GatePacketPanel";
import { Term } from "@/components/Guide";
import { StageRail } from "@/components/StageRail";
import { Empty, ErrorBox, IntentStatus, Loading, Panel, Pill, TaskStatus } from "@/components/ui";
import { useStudio, type IntentTab } from "@/state/store";
import { useProjection } from "@/state/useProjection";
import { describeEvent } from "./CockpitView";
import { IntentFootprint } from "./CodebaseView";
import { FilesTab } from "./FilesTab";
import { SpecificationTab } from "./SpecificationTab";
import { SummaryTab } from "./SummaryTab";
import { TraceabilityTab } from "./TraceabilityTab";

const TABS: { id: IntentTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "summary", label: "What we're building" },
  { id: "specification", label: "Specification" },
  { id: "files", label: "Files" },
  { id: "traceability", label: "Traceability" },
  { id: "activity", label: "Activity" },
];

/** Identifiers side by side; each wraps as a whole, never at its hyphens. */
function idList(ids: string[]) {
  if (ids.length === 0) return "—";
  return ids.map((id) => <span key={id}>{id}</span>);
}

/** Evidence is the runner's record, not something a person opens file by file here. */
const isEvidence = (path: string) => path.includes("/evidence/");
const authored = (paths: string[]) => paths.filter((path) => !isEvidence(path));
const evidenceCount = (paths: string[]) => paths.filter(isEvidence).length;

/** The gate most worth showing: the one waiting, else the latest decided. */
function focusGate(intent: IntentDetail): GateId {
  if (intent.waiting_on) return intent.waiting_on.gate;
  const decided = (["G5", "G4", "G3", "G2", "G1"] as GateId[]).find((g) => intent.gates[g]?.status !== "undecided");
  return decided ?? "G1";
}

function Overview({ intent }: { intent: IntentDetail }) {
  const openFile = useStudio((s) => s.openFile);
  const phases = [...new Set(intent.task_list.map((task) => task.phase ?? "unphased"))];
  return (
    <div className="grid-2">
      <div style={{ display: "grid", gap: 16 }}>
        <Panel title="Tasks" aside={plural(intent.task_list.length, "Task")}>
          {intent.task_list.length === 0 ? (
            <Empty>No Tasks yet. Tasks are planned after Gate 2 and approved at Gate 3.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Task</th>
                    <th>Status</th>
                    <th>Traces to</th>
                    <th>Depends on</th>
                    <th><Term id="holdout">Held-out</Term></th>
                    <th>Review</th>
                  </tr>
                </thead>
                <tbody>
                  {phases.map((phase) =>
                    intent.task_list
                      .filter((task) => (task.phase ?? "unphased") === phase)
                      .map((task) => (
                        <tr key={task.id}>
                          <td>
                            <span className="mono">{task.id}</span>
                            <br />
                            <span className="ink2">{task.title}</span>
                          </td>
                          <td>
                            <TaskStatus status={task.status} />
                            {task.attempt ? <span className="muted"> attempt {task.attempt}</span> : null}
                          </td>
                          <td className="mono ids">{idList([...task.requirements, ...task.criteria])}</td>
                          <td className="mono muted ids">{idList(task.depends_on)}</td>
                          <td>{task.has_holdout ? <Pill tone="ok">declared</Pill> : <Pill tone="warn">none</Pill>}</td>
                          <td>{task.reviewed ? <Pill tone="ok">recorded</Pill> : <span className="muted">—</span>}</td>
                        </tr>
                      )),
                  )}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <IntentFootprint intentId={intent.id} />
        <Panel title="Artifacts by stage">
          <div className="table-wrap">
            <table className="grid">
              <tbody>
                {intent.rail.map((stage) => (
                  <tr key={stage.id}>
                    <td style={{ width: 120 }}>
                      <b>{stage.title}</b>
                      <br />
                      <span className="mono muted">{stage.folder}</span>
                    </td>
                    <td className="artifact-list">
                      {(intent.artifacts[stage.id] ?? []).length === 0 ? (
                        <span className="muted">nothing yet</span>
                      ) : (
                        authored(intent.artifacts[stage.id] ?? []).map((path) => (
                          <button
                            key={path}
                            type="button"
                            className="source-link"
                            title={`Open ${path} (read-only)`}
                            onClick={() => openFile({ scope: "intent", intentId: intent.id, path })}
                          >
                            {path.replace(`${stage.folder}/`, "")}
                          </button>
                        ))
                      )}
                      {evidenceCount(intent.artifacts[stage.id] ?? []) > 0 && (
                        <span className="muted evidence-note">
                          + {evidenceCount(intent.artifacts[stage.id] ?? [])} evidence files the runner captured
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
      <Panel title="Gate decision" pad>
        <GatePacketPanel intentId={intent.id} gate={focusGate(intent)} compact />
      </Panel>
    </div>
  );
}

function Activity({ intentId }: { intentId: string }) {
  const { data, error, loading, reload } = useProjection<EventsPayload>("events.page", { intent_id: intentId, limit: 200 });
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox message={error} retry={reload} />;
  if (!data || data.events.length === 0) return <Empty>No audit events for this Intent yet.</Empty>;
  return (
    <Panel title="Audit log" aside={`${data.matched_total} events, newest first`}>
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>#</th>
              <th>When</th>
              <th>What happened</th>
              <th>Actor</th>
            </tr>
          </thead>
          <tbody>
            {data.events.map((event) => (
              <tr key={`${event.sequence}-${event.entity_id}-${event.to}`}>
                <td className="num muted">{event.sequence}</td>
                <td className="num" title={event.at ?? ""}>{when(event.at)}</td>
                <td>
                  {event.event === "advance_refused" ? <Pill tone="bad">refused</Pill> : null} {describeEvent(event)}
                </td>
                <td className="mono muted">{event.actor}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export function IntentView({ intentId, tab }: { intentId: string; tab: IntentTab }) {
  const navigate = useStudio((s) => s.navigate);
  const { data, error, loading, reload } = useProjection<IntentDetail>("intent.inspect", { intent_id: intentId });

  if (loading && !data) return <div className="page"><Loading height={360} /></div>;
  if (error) return <div className="page"><ErrorBox message={error} retry={reload} /></div>;
  if (!data) return null;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{data.title}</h1>
          <div className="meta-row">
            <span className="id-chip mono">{data.id}</span>
            <IntentStatus status={data.status} />
            {data.waiting_on && <Pill tone="gate">waiting on {data.waiting_on.gate}</Pill>}
            <Pill tone="muted">{data.mode ?? "mode unrecorded"}</Pill>
            <Pill tone="muted">{data.type ?? "type unrecorded"}</Pill>
            {data.profile && (
              <button type="button" className="pill-button" onClick={() => navigate({ view: "pipeline" })}
                title={data.profile.loosened.length ? `Loosened by waiver: ${data.profile.loosened.map((item) => `${item.rule} (${item.approved_by}: ${item.reason})`).join("; ")}` : "Open the pipeline"}>
                <Pill tone={data.profile.loosened.length ? "warn" : "muted"}>{data.profile.name} profile</Pill>
              </button>
            )}
            {data.waiting_on && (data.gates[data.waiting_on.gate]?.needed ?? 1) > 1 && (
              <Pill tone="gate">
                {data.gates[data.waiting_on.gate]?.approvals?.length ?? 0} of {data.gates[data.waiting_on.gate]?.needed} approvals
                {data.gates[data.waiting_on.gate]?.approvals?.length ? ` (${data.gates[data.waiting_on.gate]!.approvals!.join(", ")})` : ""}
              </Pill>
            )}
            {data.status_since && <span className="muted">{`${statusLabel(data.status)} ${ago(data.status_since)}`}</span>}
          </div>
        </div>
        <div className="actions">
          <button type="button" className="btn primary" onClick={() => navigate({ view: "story", intentId })}>
            Present in Story Mode
          </button>
        </div>
      </header>

      <StageRail rail={data.rail} />

      <div
        className="tabs"
        role="tablist"
        aria-label="Intent views"
        onKeyDown={(event) => {
          // Arrow keys move between tabs; Home and End jump to the ends (WAI-ARIA tabs pattern).
          const index = TABS.findIndex((item) => item.id === tab);
          const next =
            event.key === "ArrowRight" ? (index + 1) % TABS.length
            : event.key === "ArrowLeft" ? (index - 1 + TABS.length) % TABS.length
            : event.key === "Home" ? 0
            : event.key === "End" ? TABS.length - 1
            : -1;
          if (next < 0) return;
          event.preventDefault();
          navigate({ view: "intent", intentId, tab: TABS[next]!.id });
          requestAnimationFrame(() => document.getElementById(`tab-${TABS[next]!.id}`)?.focus());
        }}
      >
        {TABS.map((item) => (
          <button
            key={item.id}
            id={`tab-${item.id}`}
            role="tab"
            type="button"
            aria-selected={tab === item.id}
            aria-controls="intent-panel"
            tabIndex={tab === item.id ? 0 : -1}
            onClick={() => navigate({ view: "intent", intentId, tab: item.id })}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="intent-panel" aria-labelledby={`tab-${tab}`}>
        {tab === "overview" && <Overview intent={data} />}
        {tab === "summary" && <SummaryTab intentId={intentId} />}
        {tab === "specification" && <SpecificationTab intentId={intentId} />}
        {tab === "files" && <FilesTab intent={data} />}
        {tab === "traceability" && <TraceabilityTab intentId={intentId} />}
        {tab === "activity" && <Activity intentId={intentId} />}
      </div>
    </div>
  );
}
