import { useMemo, useState } from "react";
import { Background, Controls, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { TraceGap, TraceLane, TraceNode, TracePayload } from "@/lib/contract";
import { GAP_LABEL, plural } from "@/lib/format";
import { Icon } from "@/components/Icon";
import { Term } from "@/components/Guide";
import { ClassGlyph, Empty, ErrorBox, Loading, Panel, Pill, TaskStatus, type Tone } from "@/components/ui";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

/** How far one requirement's chain of proof reaches, in the order a reviewer cares about. */
type Verdict = "gap" | "stale" | "changed" | "proven" | "progress" | "planned" | "deferred";
const VERDICT: Record<Verdict, { label: string; tone: Tone; explain: string }> = {
  gap: { label: "Gap", tone: "bad", explain: "A link in the chain is missing. Nothing can prove this requirement until it is added." },
  stale: {
    label: "Stale evidence",
    tone: "warn",
    explain: "Evidence exists but proves an older version of the requirement. Verify the Task again to renew it.",
  },
  changed: {
    label: "Changed since G2",
    tone: "info",
    explain: "The requirement changed after G2 was approved. Check the plan still covers it, then approve G2 again.",
  },
  proven: { label: "Proven", tone: "ok", explain: "Every Task is verified, with passing evidence reviewed by a different model family." },
  progress: { label: "In progress", tone: "info", explain: "Tasks exist and some are still being built or verified." },
  planned: { label: "Planned", tone: "muted", explain: "Tasks are planned but not built yet." },
  deferred: { label: "Deferred", tone: "muted", explain: "Marked won't for this Intent: shown for the record, never counted." },
};
const ATTENTION: Verdict[] = ["gap", "stale", "changed"];
const DONE = new Set(["verified", "merged"]);
const LANES: { id: TraceLane; label: string }[] = [
  { id: "success", label: "Success measures" },
  { id: "requirement", label: "Requirements" },
  { id: "criterion", label: "Criteria" },
  { id: "task", label: "Tasks" },
  { id: "evidence", label: "Evidence and review" },
];
const LANE_X = 225;
const ROW_Y = 92;

interface Chain {
  requirement: TraceNode;
  success: TraceNode[];
  criteria: TraceNode[];
  tasks: TraceNode[];
  evidence: Map<string, TraceNode | undefined>;
  gaps: TraceGap[];
  verdict: Verdict;
}

function buildChains(trace: TracePayload): Chain[] {
  const byId = new Map(trace.nodes.map((node) => [node.id, node]));
  const from = (id: string, kind: string) => trace.edges.filter((e) => e.from === id && e.kind === kind).map((e) => byId.get(e.to)!).filter(Boolean);
  const to = (id: string, kind: string) => trace.edges.filter((e) => e.to === id && e.kind === kind).map((e) => byId.get(e.from)!).filter(Boolean);
  return trace.nodes
    .filter((node) => node.lane === "requirement")
    .map((requirement) => {
      const criteria = from(requirement.id, "verified_by");
      const tasks = [...new Map(criteria.flatMap((c) => from(c.id, "implemented_by")).map((t) => [t.id, t])).values()];
      const evidence = new Map(tasks.map((task) => [task.id, byId.get(`${task.id}#evidence`)]));
      const ids = new Set([requirement.id, ...criteria.map((c) => c.id), ...tasks.map((t) => t.id)]);
      const gaps = trace.gaps.filter((gap) => ids.has(gap.id));
      const stale = tasks.some((task) => evidence.get(task.id)?.stale || task.stale_requirements?.includes(requirement.id));
      const verdict: Verdict = requirement.deferred
        ? "deferred"
        : gaps.some((gap) => gap.kind !== "evidence_stale")
          ? "gap"
          : stale || gaps.length
            ? "stale"
            : requirement.changed || requirement.added
              ? "changed"
              : tasks.length && tasks.every((task) => DONE.has(task.status ?? "") && evidence.get(task.id)?.passed)
                ? "proven"
                : tasks.some((task) => task.status && !["pending", "ready", "not registered"].includes(task.status))
                  ? "progress"
                  : "planned";
      return { requirement, success: to(requirement.id, "demonstrated_by"), criteria, tasks, evidence, gaps, verdict };
    });
}

function VerdictPill({ verdict }: { verdict: Verdict }) {
  const item = VERDICT[verdict];
  return (
    <Pill tone={item.tone} title={item.explain}>
      {verdict === "proven" && <Icon name="check" size={12} />}
      {verdict === "gap" && <Icon name="alert" size={12} />}
      {item.label}
    </Pill>
  );
}

function Summary({ chains }: { chains: Chain[] }) {
  const scoped = chains.filter((chain) => chain.verdict !== "deferred");
  const count = (verdicts: Verdict[]) => scoped.filter((chain) => verdicts.includes(chain.verdict)).length;
  const proven = count(["proven"]);
  const tiles = [
    { label: "Requirements in scope", value: `${scoped.length}`, detail: `${chains.length - scoped.length} deferred`, tone: "" },
    { label: "Proven end to end", value: `${proven} / ${scoped.length}`, detail: "verified, passing, independently reviewed", tone: "ok" },
    { label: "Need attention", value: `${count(ATTENTION)}`, detail: "gaps, stale evidence, changes since G2", tone: count(ATTENTION) ? "warn" : "" },
    { label: "In progress or planned", value: `${count(["progress", "planned"])}`, detail: "on their way to proof", tone: "" },
  ];
  return (
    <div className="proof-summary">
      {tiles.map((tile) => (
        <div key={tile.label} className={`proof-tile ${tile.tone}`}>
          <span className="label">{tile.label}</span>
          <span className="value num">{tile.value}</span>
          <span className="detail">{tile.detail}</span>
        </div>
      ))}
      <div className="proof-bar" aria-hidden="true">
        {(["proven", "progress", "planned", "changed", "stale", "gap"] as Verdict[]).map((verdict) => {
          const n = scoped.filter((chain) => chain.verdict === verdict).length;
          return n ? <i key={verdict} className={verdict} style={{ flexGrow: n }} title={`${VERDICT[verdict].label}: ${n}`} /> : null;
        })}
      </div>
    </div>
  );
}

function Chains({ chains, selected, onSelect }: { chains: Chain[]; selected: string | null; onSelect(id: string): void }) {
  if (chains.length === 0) return <Empty>No requirement matches this filter.</Empty>;
  return (
    <div className="table-wrap">
      <table className="grid chains">
        <thead>
          <tr>
            <th>Requirement</th>
            <th>Proved by</th>
            <th>Built by</th>
            <th>Evidence</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {chains.map((chain) => (
            <tr key={chain.requirement.id} className={`chain-row ${chain.verdict}${selected === chain.requirement.id ? " selected" : ""}`}>
              <td>
                <button type="button" className="chain-open" aria-pressed={selected === chain.requirement.id} onClick={() => onSelect(chain.requirement.id)}>
                  <span className="mono">{chain.requirement.id}</span>
                  <span className="chain-text">{chain.requirement.label}</span>
                </button>
              </td>
              <td>
                <div className="chips">
                  {chain.criteria.length === 0 ? (
                    <span className="missing">no criterion</span>
                  ) : (
                    chain.criteria.map((criterion) => (
                      <span key={criterion.id} className="chip-id" title={criterion.label}>
                        <ClassGlyph value={criterion.verification_class} /> {criterion.id}
                      </span>
                    ))
                  )}
                </div>
              </td>
              <td>
                <div className="chips">
                  {chain.tasks.length === 0 ? (
                    <span className={chain.verdict === "deferred" ? "muted" : "missing"}>{chain.verdict === "deferred" ? "not planned" : "no Task"}</span>
                  ) : (
                    chain.tasks.map((task) => (
                      <span key={task.id} className="chip-id" title={`${task.label} · ${task.status ?? "not registered"}`}>
                        <span className={`dot ${DONE.has(task.status ?? "") ? "ok" : task.status === "retry" || task.status === "blocked" ? "bad" : "info"}`} />
                        {task.id.split("-").slice(1).join("-")}
                      </span>
                    ))
                  )}
                </div>
              </td>
              <td>
                <div className="chips">
                  {chain.tasks.length === 0 ? (
                    <span className="muted">—</span>
                  ) : (
                    chain.tasks.map((task) => {
                      const evidence = chain.evidence.get(task.id);
                      if (!evidence) return <span key={task.id} className="muted">none yet</span>;
                      return (
                        <span key={task.id} className={`chip-id ${evidence.stale ? "stale" : evidence.passed ? "passed" : "failed"}`} title={`@${evidence.commit}, review ${evidence.review_decision ?? "pending"}`}>
                          <Icon name={evidence.passed ? "check" : "close"} size={12} />
                          {evidence.review_decision ? evidence.review_decision : "unreviewed"}
                        </span>
                      );
                    })
                  )}
                </div>
              </td>
              <td>
                <VerdictPill verdict={chain.verdict} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface TraceNodeData extends Record<string, unknown> {
  node: TraceNode;
  gap: boolean;
  selected: boolean;
}

function TraceCard({ data }: NodeProps<Node<TraceNodeData>>) {
  const { node, gap, selected } = data;
  const classes = ["trace-node"];
  if (gap) classes.push("gap");
  if (node.stale || (node.stale_requirements?.length ?? 0) > 0) classes.push("stale");
  if (node.deferred) classes.push("deferred");
  if (node.changed || node.added) classes.push("changed");
  if (selected) classes.push("selected");
  return (
    <div className={classes.join(" ")}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <span className="id">
        {node.verification_class && <ClassGlyph value={node.verification_class} />}
        {node.lane === "evidence" ? (node.passed ? "Passed" : "Failed") : node.id}
        {node.status && <TaskStatus status={node.status} />}
      </span>
      <span className="label">
        {node.lane === "evidence"
          ? node.review_decision
            ? `Review ${node.review_decision} by ${node.reviewer_model}`
            : "No review yet"
          : node.label}
      </span>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { trace: TraceCard };

/** The same links as a lane map at a readable, fixed zoom; pan to move around. */
function MapView({ trace, visible, selected, onSelect }: { trace: TracePayload; visible: Set<string>; selected: string | null; onSelect(id: string): void }) {
  const gapIds = useMemo(() => new Set(trace.gaps.map((g) => g.id)), [trace]);
  const { nodes, edges, height } = useMemo(() => {
    const rows: Record<string, number> = {};
    const shown = trace.nodes.filter((n) => visible.has(n.id));
    const flowNodes: Node<TraceNodeData>[] = shown.map((node) => {
      const lane = LANES.findIndex((l) => l.id === node.lane);
      const row = (rows[node.lane] = (rows[node.lane] ?? -1) + 1);
      return {
        id: node.id,
        type: "trace",
        position: { x: lane * LANE_X, y: row * ROW_Y },
        data: { node, gap: gapIds.has(node.id), selected: node.id === selected },
        draggable: false,
      };
    });
    const staleIds = new Set(shown.filter((n) => n.stale || n.stale_requirements?.length).map((n) => n.id));
    const ids = new Set(shown.map((n) => n.id));
    const flowEdges: Edge[] = trace.edges
      .filter((e) => ids.has(e.from) && ids.has(e.to))
      .map((e) => ({
        id: `${e.from}->${e.to}`,
        source: e.from,
        target: e.to,
        style: { stroke: staleIds.has(e.to) || staleIds.has(e.from) ? "var(--warn)" : "var(--border)", strokeWidth: 1.6 },
      }));
    const tallest = Math.max(1, ...Object.values(rows).map((r) => r + 1));
    return { nodes: flowNodes, edges: flowEdges, height: Math.min(720, Math.max(360, tallest * ROW_Y + 60)) };
  }, [trace, visible, gapIds, selected]);

  if (nodes.length === 0) return <Empty>Nothing to show with this filter.</Empty>;
  return (
    <div className="trace-map">
      <div className="lane-heads" aria-hidden="true">
        {LANES.map((lane) => (
          <span key={lane.id}>{lane.label}</span>
        ))}
      </div>
      <div className="trace-canvas" style={{ height }} role="img" aria-label={`Traceability map with ${nodes.length} items. The Chains view lists the same links as a table.`}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          defaultViewport={{ x: 16, y: 16, zoom: 1 }}
          minZoom={0.5}
          maxZoom={1.4}
          nodesConnectable={false}
          onNodeClick={(_, node) => onSelect(node.id)}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={20} size={1} color="var(--hairline)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}

function Detail({ chain, intentId }: { chain: Chain; intentId: string }) {
  const navigate = useStudio((s) => s.navigate);
  const verdict = VERDICT[chain.verdict];
  return (
    <Panel title={<span className="mono">{chain.requirement.id}</span>} aside={<VerdictPill verdict={chain.verdict} />} pad>
      <div className="chain-detail">
        <p className="statement">{chain.requirement.label}</p>
        <p className={`verdict-note ${verdict.tone}`}>{verdict.explain}</p>
        {chain.gaps.length > 0 && (
          <ul className="plain">
            {chain.gaps.map((gap) => (
              <li key={`${gap.id}-${gap.kind}`}>
                <span className="dot bad" /> <span className="mono">{gap.id}</span> {GAP_LABEL[gap.kind] ?? gap.kind}: {gap.message}
              </li>
            ))}
          </ul>
        )}
        <ol className="chain-steps">
          <li>
            <span className="step-label">Demonstrates</span>
            {chain.success.length ? chain.success.map((item) => <span key={item.id}><span className="mono">{item.id}</span> {item.label}</span>) : <span className="muted">no success measure</span>}
          </li>
          <li>
            <span className="step-label">Proved by</span>
            {chain.criteria.map((criterion) => (
              <span key={criterion.id}>
                <ClassGlyph value={criterion.verification_class} /> <span className="mono">{criterion.id}</span> {criterion.label}
              </span>
            ))}
          </li>
          <li>
            <span className="step-label">Built by</span>
            {chain.tasks.length ? (
              chain.tasks.map((task) => {
                const evidence = chain.evidence.get(task.id);
                return (
                  <span key={task.id}>
                    <span className="mono">{task.id}</span> {task.label} {task.status && <TaskStatus status={task.status} />}
                    {evidence && (
                      <span className="muted">
                        {" "}
                        {evidence.passed ? "passed" : "failed"} at @{evidence.commit}
                        {evidence.review_decision ? `, ${evidence.review_decision} by ${evidence.reviewer_model}` : ", not reviewed"}
                        {task.implementer_model ? ` (implemented by ${task.implementer_model})` : ""}
                      </span>
                    )}
                  </span>
                );
              })
            ) : (
              <span className="muted">no Task yet</span>
            )}
          </li>
        </ol>
        <button type="button" className="btn sm" onClick={() => navigate({ view: "intent", intentId, tab: "specification" })}>
          <Icon name="file" size={14} /> Open in the specification
        </button>
      </div>
    </Panel>
  );
}

type Mode = "chains" | "map";
type Filter = "all" | "attention" | "proven" | "deferred";

export function TraceabilityTab({ intentId }: { intentId: string }) {
  const { data, error, loading, reload } = useProjection<TracePayload>("trace.inspect", { intent_id: intentId });
  const [mode, setMode] = useState<Mode>("chains");
  const [filter, setFilter] = useState<Filter | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const chains = useMemo(() => (data?.present ? buildChains(data) : []), [data]);

  if (loading && !data) return <Loading height={420} />;
  if (error) return <ErrorBox message={error} retry={reload} />;
  if (!data) return null;
  if (!data.present) return <Empty>Traceability starts once requirements exist. They are written in Stage 2.</Empty>;

  const attention = chains.filter((chain) => ATTENTION.includes(chain.verdict));
  const active: Filter = filter ?? (attention.length ? "attention" : "all");
  const counts: Record<Filter, number> = {
    all: chains.length,
    attention: attention.length,
    proven: chains.filter((c) => c.verdict === "proven").length,
    deferred: chains.filter((c) => c.verdict === "deferred").length,
  };
  const needle = query.trim().toLowerCase();
  const shown = chains.filter((chain) => {
    const kept =
      active === "all" || (active === "attention" ? ATTENTION.includes(chain.verdict) : active === "proven" ? chain.verdict === "proven" : chain.verdict === "deferred");
    const text = `${chain.requirement.id} ${chain.requirement.label} ${chain.criteria.map((c) => c.id).join(" ")} ${chain.tasks.map((t) => t.id).join(" ")}`.toLowerCase();
    return kept && (!needle || text.includes(needle));
  });
  const focus = chains.find((chain) => chain.requirement.id === selected) ?? shown[0] ?? null;
  const visible = new Set<string>();
  for (const chain of shown) {
    [chain.requirement, ...chain.success, ...chain.criteria, ...chain.tasks].forEach((node) => visible.add(node.id));
    chain.evidence.forEach((node) => node && visible.add(node.id));
  }

  return (
    <div className="proof">
      <Summary chains={chains} />
      <div className="proof-bar-row">
        <span className="seg" role="group" aria-label="View">
          <button type="button" aria-pressed={mode === "chains"} onClick={() => setMode("chains")}>Chains</button>
          <button type="button" aria-pressed={mode === "map"} onClick={() => setMode("map")}>Map</button>
        </span>
        <span className="filter-chips" role="group" aria-label="Show">
          {(["attention", "all", "proven", "deferred"] as Filter[]).map((value) => (
            <button key={value} type="button" className="filter-chip" aria-pressed={active === value} onClick={() => setFilter(value)}>
              {value === "attention" ? "Needs attention" : value === "all" ? "All" : value === "proven" ? "Proven" : "Deferred"}
              <span className="n">{counts[value]}</span>
            </button>
          ))}
        </span>
        <input
          type="search"
          className="proof-search"
          aria-label="Find a requirement, criterion or Task"
          placeholder="Find REQ, AC or Task"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <span className="legend">
          <Term id="verification-class">
            <ClassGlyph value="D" /> <ClassGlyph value="S" /> <ClassGlyph value="J" /> <ClassGlyph value="N" />
          </Term>
        </span>
      </div>

      {data.spec_diff && (data.spec_diff.requirements.added.length + data.spec_diff.requirements.changed.length > 0) && (
        <div className="note info">
          Since G2 was approved: {data.spec_diff.requirements.added.length ? `added ${data.spec_diff.requirements.added.join(", ")}` : ""}
          {data.spec_diff.requirements.added.length && data.spec_diff.requirements.changed.length ? "; " : ""}
          {data.spec_diff.requirements.changed.length ? `changed ${data.spec_diff.requirements.changed.join(", ")}` : ""}.
        </div>
      )}

      <div className={`proof-body ${mode}`}>
        <div className="proof-main">
          {counts.attention === 0 && active === "attention" ? (
            <Empty>Nothing needs attention. Every requirement traces to a criterion, a Task, and current evidence.</Empty>
          ) : mode === "chains" ? (
            <Chains chains={shown} selected={focus?.requirement.id ?? null} onSelect={setSelected} />
          ) : (
            <MapView
              trace={data}
              visible={visible}
              selected={selected}
              onSelect={(id) => {
                const owner = chains.find((chain) => chain.requirement.id === id || chain.criteria.some((c) => c.id === id) || chain.tasks.some((t) => t.id === id));
                setSelected(owner?.requirement.id ?? id);
              }}
            />
          )}
        </div>
        <aside className="proof-side" aria-label="Selected chain">
          {focus ? <Detail chain={focus} intentId={intentId} /> : <Empty>Select a requirement to see its chain.</Empty>}
          {data.gaps.length > 0 && (
            <Panel title="All gaps" aside={plural(data.gaps.length, "gap")}>
              <ul className="plain panel-pad">
                {data.gaps.map((gap) => (
                  <li key={`${gap.id}-${gap.kind}`}>
                    <span className="dot bad" /> <span className="mono">{gap.id}</span> {GAP_LABEL[gap.kind] ?? gap.kind}
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </aside>
      </div>
    </div>
  );
}
