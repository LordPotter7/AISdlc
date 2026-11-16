import type { OverviewPayload, OverviewRow } from "@/lib/contract";
import { Term } from "@/components/Guide";
import { ClassGlyph, Empty, ErrorBox, Loading, Panel, Pill, TaskStatus } from "@/components/ui";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

const SCOPE = [
  { key: "must", label: "Must" },
  { key: "should", label: "Should" },
  { key: "could", label: "Could" },
  { key: "wont", label: "Won't (this time)" },
] as const;

function Meter({ label, value, total, tone = "ok" }: { label: string; value: number; total: number; tone?: "ok" | "warn" }) {
  const percent = total ? Math.round((value / total) * 100) : 0;
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <b>
          {value} / {total}
        </b>
      </div>
      <div
        className={`meter-bar ${tone}`}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={value}
      >
        <i style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function RequirementLine({ row }: { row: OverviewRow }) {
  return (
    <li className={`scope-item${row.suspect.length ? " suspect" : ""}`}>
      <div>
        <span className="mono">{row.id}</span> {row.statement}
      </div>
      <div className="scope-meta">
        {row.criteria.map((criterion) => (
          <span key={criterion.id} title={criterion.statement}>
            <ClassGlyph value={criterion.class} />
          </span>
        ))}
        {row.tasks.length === 0 && row.priority !== "wont" ? (
          <Pill tone="bad">no Task yet</Pill>
        ) : row.proven ? (
          <Pill tone="ok">proven</Pill>
        ) : row.tasks.length ? (
          <span className="muted">{row.tasks.map((task) => task.id.split("-").slice(1).join("-")).join(", ")}</span>
        ) : null}
        {row.suspect.length > 0 && <Pill tone="warn">link may no longer hold</Pill>}
      </div>
    </li>
  );
}

/**
 * "What we're building": one page assembled from the brief, the specification, the
 * contracts and the plan. Nothing here is typed twice; change a file and this page changes.
 * Coverage and suspect links come from records the runner keeps (evidence pins, the G2
 * baseline), and every suspect link names the lifecycle action that clears it.
 */
export function SummaryTab({ intentId }: { intentId: string }) {
  const { data, error, loading, reload } = useProjection<OverviewPayload>("overview.inspect", { intent_id: intentId });
  const openFile = useStudio((s) => s.openFile);
  const navigate = useStudio((s) => s.navigate);
  if (loading && !data) return <Loading height={360} />;
  if (error) return <ErrorBox message={error} retry={reload} />;
  if (!data) return null;
  const brief = data.brief;
  const coverage = data.coverage;
  const suspect = data.requirements.filter((row) => row.suspect.length > 0);
  const unplanned = data.requirements.filter((row) => row.priority !== "wont" && row.tasks.length === 0);
  const open = (path: string) => openFile({ scope: "intent", intentId, path });

  return (
    <div className="summary">
      <div className="summary-head">
        <span className="muted">
          Assembled from{" "}
          <button type="button" className="source-link" onClick={() => open("01-brief/brief.md")}>brief.md</button>,{" "}
          <button type="button" className="source-link" onClick={() => open("02-specification/requirements.yaml")}>requirements.yaml</button>
          {data.contracts.length > 0 && " , contracts"} and the plan. Change a file and this page changes.
        </span>
        <button type="button" className="btn sm" onClick={() => window.print()}>
          Print or save as PDF
        </button>
      </div>

      <div className="grid-2">
        <Panel title="The problem" pad>
          {brief.problem ? <p className="prose">{brief.problem}</p> : <Empty>The brief has no Problem section yet.</Empty>}
          {brief["expected outcome"] && (
            <>
              <h3 className="sub-h">Expected outcome</h3>
              <p className="prose">{brief["expected outcome"]}</p>
            </>
          )}
        </Panel>
        <Panel title="Done means" pad>
          {data.success_criteria.length === 0 ? (
            brief["success measures"] ? <p className="prose">{brief["success measures"]}</p> : <Empty>No success measures yet.</Empty>
          ) : (
            <ul className="plain">
              {data.success_criteria.map((item) => (
                <li key={item.id}>
                  <b>{item.brief_measure ?? item.statement}</b>
                  <div className="muted">
                    <span className="mono">{item.id}</span> proved by {item.criteria.join(", ") || <Pill tone="warn">nothing yet</Pill>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="How much of it is proven" aside={<Term id="traceability">requirement → criterion → Task → evidence</Term>} pad>
        <div className="meters">
          <Meter label="with an acceptance criterion" value={coverage.with_criterion} total={coverage.requirements} />
          <Meter label="with a Task" value={coverage.with_task} total={coverage.requirements} tone={coverage.with_task < coverage.requirements ? "warn" : "ok"} />
          <Meter label="proven by current evidence" value={coverage.proven} total={coverage.requirements} />
        </div>
        {coverage.requirements === 0 && <Empty>No requirements yet. They are written in Stage 2.</Empty>}
      </Panel>

      {(suspect.length > 0 || unplanned.length > 0) && (
        <Panel title="What may no longer hold" aside={`${suspect.length + unplanned.length} to look at`} pad>
          <ul className="plain">
            {unplanned.map((row) => (
              <li key={`gap-${row.id}`}>
                <Pill tone="bad">gap</Pill> <span className="mono">{row.id}</span> has no Task.{" "}
                <span className="muted">Add one to the plan before G3.</span>
              </li>
            ))}
            {suspect.flatMap((row) =>
              row.suspect.map((link, index) => (
                <li key={`${row.id}-${link.kind}-${index}`}>
                  <Pill tone="warn">{link.kind === "evidence" ? "evidence older than the requirement" : "planned before a change"}</Pill>{" "}
                  {link.reason}. <span className="muted">To clear it: {link.clear}.</span>
                </li>
              )),
            )}
          </ul>
        </Panel>
      )}

      <Panel title="Scope" pad>
        <div className="scope">
          {SCOPE.map(({ key, label }) => {
            const rows = data.requirements.filter((row) => (row.priority ?? "must") === key);
            return (
              <div key={key} className="scope-col">
                <h3 className="sub-h">{label}</h3>
                {rows.length ? (
                  <ul className="plain">{rows.map((row) => <RequirementLine key={row.id} row={row} />)}</ul>
                ) : (
                  <span className="muted">{key === "wont" ? "Nothing declared out of scope. Consider saying what is." : "—"}</span>
                )}
              </div>
            );
          })}
        </div>
      </Panel>

      <div className="grid-2">
        <Panel title="Touches" pad>
          {data.touches.length ? (
            <div className="artifact-list">
              {data.touches.map((path) => (
                <span key={path} className="chip mono">{path}</span>
              ))}
            </div>
          ) : (
            <span className="muted">The plan declares no affected paths yet.</span>
          )}
          {data.contracts.map((contract) => (
            <div key={contract.path} style={{ marginTop: 8 }}>
              API <span className="mono">{contract.api ?? contract.path}</span>
              {contract.breaking_changes_accepted > 0 && <Pill tone="warn">{contract.breaking_changes_accepted} breaking change accepted</Pill>}
            </div>
          ))}
          {data.contention.map((item) => (
            <div key={`${item.resource}-${item.intent}`} style={{ marginTop: 6 }}>
              <Pill tone="gate">shared with {item.intent}</Pill> <span className="mono">{item.resource}</span>{" "}
              <button type="button" className="btn sm" onClick={() => navigate({ view: "inbox" })}>Decide</button>
            </div>
          ))}
        </Panel>
        <Panel title={<Term id="threat-model">Risks</Term>} pad>
          {data.threats.total === 0 ? (
            <span className="muted">No threat model.</span>
          ) : (
            <p style={{ margin: 0 }}>
              {data.threats.total} threats:{" "}
              {Object.entries(data.threats.by_disposition).map(([disposition, count]) => `${count} ${disposition}`).join(", ")}.
              {data.threats.inherited.length > 0 && (
                <>
                  {" "}Answers <Term id="standing-threat">standing threat</Term> {data.threats.inherited.join(", ")}.
                </>
              )}
            </p>
          )}
        </Panel>
      </div>

      <Panel title="Plan and progress" aside={`${data.plan.length} Tasks`}>
        {data.plan.length === 0 ? (
          <Empty>No Tasks yet. They are planned in Stage 3.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr><th>Task</th><th>Requirements</th><th>State</th></tr>
              </thead>
              <tbody>
                {data.plan.map((task) => (
                  <tr key={task.id}>
                    <td><span className="mono">{task.id}</span> {task.title}</td>
                    <td className="mono">{task.requirements.join(" ") || "—"}</td>
                    <td><TaskStatus status={task.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
