import { useState } from "react";
import type { IntentPriority, PortfolioIntent, PortfolioPayload } from "@/lib/contract";
import { plural } from "@/lib/format";
import { Icon } from "@/components/Icon";
import { OperationDialog, type OperationRequest } from "@/components/OperationDialog";
import { Empty, ErrorBox, IntentStatus, Loading, Panel, Pill } from "@/components/ui";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

type Filter = "open" | "blocked" | "all";
const PRIORITY: Record<IntentPriority, { label: string }> = {
  high: { label: "High" },
  normal: { label: "Normal" },
  low: { label: "Low" },
};

/** Members of a programme in the order they may build: predecessors first. */
function ordered(members: string[], edges: PortfolioPayload["programs"][number]["edges"]): string[] {
  const placed: string[] = [];
  const remaining = new Set(members);
  while (remaining.size) {
    const next = [...remaining].find((id) => edges.every((edge) => edge.to !== id || !remaining.has(edge.from)));
    const pick = next ?? [...remaining][0]!; // a cycle is refused by the runner; never loop here
    placed.push(pick);
    remaining.delete(pick);
  }
  return placed;
}

function Meter({ proven, total }: { proven: number; total: number }) {
  if (!total) return <span className="muted">no requirements yet</span>;
  return (
    <span className="meter" aria-label={`${proven} of ${total} requirements proven`}>
      <span className="meter-bar" aria-hidden="true">
        <i style={{ width: `${(proven / total) * 100}%` }} />
      </span>
      <span className="num">{proven}/{total}</span>
    </span>
  );
}

/**
 * Every Intent in the repository, highest priority first: what stage it is in, what it waits
 * on, how much is proven, and which other open Intents change the same code. Programmes show
 * their order beside it. Setting a priority and waiving a dependency are decisions the runner
 * records with a name and a reason; everything else here is read.
 */
export function PortfolioView() {
  const navigate = useStudio((s) => s.navigate);
  const refresh = useStudio((s) => s.refresh);
  const { data, error, loading, reload } = useProjection<PortfolioPayload>("portfolio.inspect");
  const [filter, setFilter] = useState<Filter>("open");
  const [program, setProgram] = useState<string>("");
  const [operation, setOperation] = useState<OperationRequest | null>(null);

  if (loading && !data) return <div className="page"><Loading height={420} /></div>;
  if (error) return <div className="page"><ErrorBox message={error} retry={reload} /></div>;
  if (!data) return null;

  const byId = new Map(data.intents.map((item) => [item.id, item]));
  const programOf = (id: string) => data.programs.find((item) => item.members.includes(id));
  const counts: Record<Filter, number> = {
    open: data.intents.filter((item) => item.active).length,
    blocked: data.intents.filter((item) => item.blocked_by.length).length,
    all: data.intents.length,
  };
  const shown = data.intents.filter(
    (item) =>
      (filter === "all" || (filter === "open" ? item.active : item.blocked_by.length > 0)) &&
      (!program || item.program === program),
  );

  const setPriority = (intent: PortfolioIntent, priority: IntentPriority) =>
    setOperation({
      name: "intent_priority",
      title: `Set ${intent.id} to ${PRIORITY[priority].label.toLowerCase()} priority`,
      args: { intent_id: intent.id, priority },
      identityField: "actor",
      reasonField: true,
      explanation:
        "Records the new priority in the audit log with your name and reason. It orders the portfolio for everyone; " +
        "it changes no gate and no transition.",
    });
  const waive = (intent: PortfolioIntent, predecessor: string) => {
    const owner = programOf(intent.id);
    if (!owner) return;
    setOperation({
      name: "program_waive",
      title: `Let ${intent.id} build before ${predecessor}`,
      args: { program: owner.id, intent_id: intent.id, predecessor },
      identityField: "actor",
      reasonField: true,
      explanation:
        `${owner.id} says ${intent.id} builds on ${predecessor}'s result. Waiving records that it may enter Build first, ` +
        "kept in the programme registry and the audit log with your name and reason.",
    });
  };

  const building = data.wip.building.length;
  return (
    <div className="page portfolio">
      <header className="page-head">
        <div>
          <h1>Portfolio</h1>
          <span className="sub">
            Every Intent in this repository, highest priority first: what it waits on, how much is proven, and where
            open Intents change the same code.
          </span>
        </div>
      </header>

      <div className="proof-summary">
        <div className={`proof-tile ${data.wip.full ? "warn" : ""}`}>
          <span className="label">In Build</span>
          <span className="value num">{data.wip.limit != null ? `${building} / ${data.wip.limit}` : building}</span>
          <span className="detail">
            {data.wip.limit != null ? (data.wip.full ? "at the limit; finish one to start another" : "limit set in scale.max_building") : "no build limit set"}
          </span>
        </div>
        <div className="proof-tile">
          <span className="label">Ready to build</span>
          <span className="value num">{data.wip.ready.length}</span>
          <span className="detail">{data.wip.ready.join(", ") || "none planned"}</span>
        </div>
        <div className={`proof-tile ${counts.blocked ? "warn" : ""}`}>
          <span className="label">Waiting on a predecessor</span>
          <span className="value num">{counts.blocked}</span>
          <span className="detail">programme order, until it completes or is waived</span>
        </div>
        <div className="proof-tile ok">
          <span className="label">Proven across the portfolio</span>
          <span className="value num">{data.proof.proven} / {data.proof.requirements}</span>
          <span className="detail">in-scope requirements, {plural(data.proof.intents, "Intent")}</span>
        </div>
        <div className="proof-bar" aria-hidden="true">
          <i className="proven" style={{ flexGrow: data.proof.proven }} />
          <i className="planned" style={{ flexGrow: Math.max(0, data.proof.requirements - data.proof.proven) }} />
        </div>
      </div>

      <div className="proof-body">
        <div className="proof-main">
          <div className="proof-bar-row">
            <span className="filter-chips" role="group" aria-label="Show Intents">
              {(["open", "blocked", "all"] as Filter[]).map((value) => (
                <button key={value} type="button" className="filter-chip" aria-pressed={filter === value} onClick={() => setFilter(value)}>
                  {value === "open" ? "Open" : value === "blocked" ? "Waiting on a predecessor" : "All"}
                  <span className="n">{counts[value]}</span>
                </button>
              ))}
            </span>
            {data.programs.length > 0 && (
              <label className="story-pick">
                Programme
                <select value={program} onChange={(event) => setProgram(event.target.value)}>
                  <option value="">Any</option>
                  {data.programs.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id} {item.title}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {shown.length === 0 ? (
            <Empty>{filter === "blocked" ? "No Intent waits on a programme predecessor." : "No Intent matches."}</Empty>
          ) : (
            <div className="table-wrap">
              <table className="grid ranked">
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th>Intent</th>
                    <th>Stage</th>
                    <th>Waits on</th>
                    <th>Proven</th>
                    <th>Shares code with</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((intent) => {
                    const current: IntentPriority = intent.priority?.priority ?? "normal";
                    return (
                      <tr key={intent.id} className={intent.active ? undefined : "closed"}>
                        <td>
                          {intent.active ? (
                            <select
                              className={`priority-select ${current}`}
                              aria-label={`Priority of ${intent.id}`}
                              value={current}
                              title={intent.priority ? `Set by ${intent.priority.by}: ${intent.priority.reason}` : "Never set"}
                              onChange={(event) => setPriority(intent, event.target.value as IntentPriority)}
                            >
                              {(Object.keys(PRIORITY) as IntentPriority[]).map((value) => (
                                <option key={value} value={value}>
                                  {PRIORITY[value].label}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td>
                          <button type="button" className="chain-open" onClick={() => navigate({ view: "intent", intentId: intent.id, tab: "overview" })}>
                            <span className="mono">{intent.id}</span>
                            <span className="chain-text">{intent.title}</span>
                          </button>
                          {intent.priority?.reason && <span className="why muted">“{intent.priority.reason}”, {intent.priority.by}</span>}
                        </td>
                        <td>
                          <IntentStatus status={intent.status} />
                          {intent.program && <span className="mono muted prog">{intent.program}</span>}
                        </td>
                        <td>
                          {intent.blocked_by.length === 0 ? (
                            <span className="muted">—</span>
                          ) : (
                            <div className="waits">
                              {intent.blocked_by.map((id) => (
                                <span key={id} className="wait">
                                  <Pill tone="warn">
                                    {id} {byId.get(id)?.status ?? "not started"}
                                  </Pill>
                                  <button type="button" className="btn sm" onClick={() => waive(intent, id)}>
                                    Waive…
                                  </button>
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                        <td>
                          <Meter proven={intent.proof.proven} total={intent.proof.requirements} />
                        </td>
                        <td>
                          {intent.shared_with.length === 0 ? (
                            <span className="muted">—</span>
                          ) : (
                            <div className="chips">
                              {intent.shared_with.map((id) => (
                                <button key={id} type="button" className="chip-id intent-chip active" onClick={() => navigate({ view: "intent", intentId: id, tab: "overview" })}>
                                  {id}
                                </button>
                              ))}
                              <button type="button" className="link-button" onClick={() => navigate({ view: "codebase" })}>
                                {plural(intent.modules.length, "module")}
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <aside className="proof-side" aria-label="Programmes and overlaps">
          {data.programs.length === 0 ? (
            <Panel title="Programmes" pad>
              <p className="muted" style={{ margin: 0 }}>
                None yet. A programme orders Intents that build on each other:{" "}
                <code>ai-sdlc program new --title "…" --owner you</code>, then <code>program add PRG001 I002 --requires I001</code>.
              </p>
            </Panel>
          ) : (
            data.programs.map((item) => (
              <Panel key={item.id} title={<><span className="mono">{item.id}</span> {item.title}</>} aside={`${item.closed}/${item.members.length} done`}>
                <ol className="program-steps">
                  {ordered(item.members, item.edges).map((id) => {
                    const member = byId.get(id);
                    const incoming = item.edges.filter((edge) => edge.to === id);
                    return (
                      <li key={id} className={member?.status === "completed" ? "done" : member?.blocked_by.length ? "blocked" : ""}>
                        <span className="step-mark" aria-hidden="true">
                          {member?.status === "completed" ? <Icon name="check" size={12} /> : null}
                        </span>
                        <div>
                          <button type="button" className="link-button mono" onClick={() => navigate({ view: "intent", intentId: id, tab: "overview" })}>
                            {id}
                          </button>{" "}
                          <span>{member?.title}</span>
                          <div className="muted step-state">
                            <IntentStatus status={member?.status ?? null} />
                            {incoming.map((edge) => (
                              <span key={edge.from} className="edge">
                                after {edge.from}:{" "}
                                {edge.satisfied ? "done" : edge.waived ? `waived by ${edge.waived_by} (“${edge.waiver_reason}”)` : "waiting"}
                              </span>
                            ))}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ol>
                <div className="panel-pad program-foot muted">
                  Owner <span className="mono">{item.owner}</span>. <Meter proven={item.proof.proven} total={item.proof.requirements} />
                </div>
              </Panel>
            ))
          )}
          {data.overlaps.length > 0 && (
            <Panel title="Code changed by more than one open Intent" aside={plural(data.overlaps.length, "module")}>
              <ul className="plain panel-pad">
                {data.overlaps.map((item) => (
                  <li key={item.module}>
                    <span className="mono">{item.module}/</span> <span className="muted">{item.intents.join(", ")}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </aside>
      </div>

      {operation && (
        <OperationDialog
          request={operation}
          onClose={(changed) => {
            setOperation(null);
            reload(); // a cancelled priority change puts the select back
            if (changed) void refresh();
          }}
        />
      )}
    </div>
  );
}
