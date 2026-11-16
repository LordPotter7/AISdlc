import { useMemo, useState } from "react";
import type { GuideStage, LearnPayload } from "@/lib/contract";
import { useGuide } from "@/components/Guide";
import { Empty, Loading, Panel, Pill } from "@/components/ui";
import { useStudio } from "@/state/store";

const SEVERITY_TONE = { error: "bad", warning: "warn", info: "muted" } as const;

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy-cmd">
      <span className="mono">{command}</span>
      <button
        type="button"
        className="btn sm"
        onClick={() => {
          void navigator.clipboard?.writeText(command).then(() => setCopied(true), () => undefined);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied" : "Copy for Claude Code"}
      </button>
    </div>
  );
}

function Metro({ guide, selected, onSelect }: { guide: LearnPayload; selected: number; onSelect(index: number): void }) {
  const navigate = useStudio((s) => s.navigate);
  return (
    <ol className="metro" aria-label="The lifecycle, left to right">
      {guide.stages.map((stage, index) => {
        const riders = guide.intents.filter((item) =>
          stage.id === "close" ? item.status === "completed" : item.working_stage === stage.id,
        );
        return (
          <li key={stage.id} className={`stop${index === selected ? " on" : ""}`}>
            <button type="button" className="node" aria-pressed={index === selected} onClick={() => onSelect(index)}>
              <span className="num">{index}</span>
              <span className="name">{stage.title}</span>
              <span className="who">{stage.owner}</span>
            </button>
            {stage.gate && (
              <span className="gatemark" title={`${stage.gate}: ${guide.gates[stage.gate] ?? "a person decides"}`}>
                <b>{stage.gate}</b>
              </span>
            )}
            <span className="riders">
              {riders.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`rider${item.waiting_on ? " waiting" : ""}`}
                  title={`${item.title}${item.waiting_on ? ` · waiting on ${item.waiting_on.gate}` : ""}`}
                  onClick={() => navigate({ view: "intent", intentId: item.id, tab: "overview" })}
                >
                  {item.id}
                </button>
              ))}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function StageDetail({ stage, question }: { stage: GuideStage; question?: string }) {
  const openFile = useStudio((s) => s.openFile);
  return (
    <div className="grid-3">
      <Panel title={`Stage ${stage.folder.slice(0, 2)} · ${stage.title}`} pad>
        <p style={{ margin: "0 0 10px", fontSize: 15 }}>{stage.what}</p>
        <dl className="lane">
          <dt>Your part</dt>
          <dd>{stage.your_part}</dd>
          {stage.gate && (
            <>
              <dt>Gate {stage.gate}</dt>
              <dd>{question} The runner will not move past it until a person approves.</dd>
            </>
          )}
        </dl>
      </Panel>
      <Panel title="Files you write" aside={<span className="mono">{stage.folder}/</span>} pad>
        {stage.authored.length === 0 ? (
          <span className="muted">Nothing: the runner and the agent produce this stage's records.</span>
        ) : (
          <ul className="plain">
            {stage.authored.map((item) => (
              <li key={item.file}>
                <span className="mono">{item.file}</span>
                {item.note && <span className="muted"> · {item.note}</span>}
                <div style={{ display: "flex", gap: 6, marginTop: 3 }}>
                  {item.template && (
                    <button type="button" className="btn sm" onClick={() => openFile({ scope: "template", path: item.template! })}>
                      Template
                    </button>
                  )}
                  {item.schema && (
                    <button type="button" className="btn sm" onClick={() => openFile({ scope: "schema", path: item.schema! })}>
                      Schema
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel title="Do it" pad>
        <CopyCommand command={stage.command} />
        <p className="muted" style={{ fontSize: 12.5, marginBottom: 0 }}>
          Run it in Claude Code. The agent writes the files; the runner checks them and records every step, and Studio shows the
          result here within a few seconds.
        </p>
      </Panel>
    </div>
  );
}

function StartHere({ guide }: { guide: LearnPayload }) {
  const navigate = useStudio((s) => s.navigate);
  const finished = guide.intents.find((item) => item.status === "completed");
  const waiting = guide.intents.find((item) => item.waiting_on);
  const steps = [
    { done: true, title: "Open a repository run by AISDLC", detail: "Studio reads what the runner records in .ai-sdlc/ and delivery/." },
    {
      done: guide.facts.intents > 0,
      title: "Register an Intent",
      detail: guide.facts.intents > 0 ? `${guide.facts.intents} registered in this repository.` : "Run /aisdlc:start in Claude Code.",
    },
    {
      done: guide.facts.completed > 0,
      title: "Read one finished Intent end to end",
      detail: finished ? `${finished.id} ${finished.title}: replay it in Story Mode.` : "None finished yet.",
      action: finished ? () => navigate({ view: "story", intentId: finished.id }) : undefined,
    },
    {
      done: false,
      title: "Follow one requirement to its evidence",
      detail: "Traceability shows requirement → criterion → Task → evidence. Click a file:line to see where it is written.",
      action: finished ? () => navigate({ view: "intent", intentId: finished.id, tab: "traceability" }) : undefined,
    },
    {
      done: guide.facts.approved_gates > 0,
      title: "Approve a gate you understand",
      detail: waiting ? `${waiting.id} is waiting on ${waiting.waiting_on!.gate}. Read its packet first.` : "Nothing is waiting right now.",
      action: waiting ? () => navigate({ view: "intent", intentId: waiting.id, tab: "overview" }) : undefined,
    },
  ];
  return (
    <Panel title="Start here" aside={`${steps.filter((step) => step.done).length} of ${steps.length} done in this repository`} pad>
      <ol className="checklist">
        {steps.map((step) => (
          <li key={step.title}>
            <span className={`chk${step.done ? " done" : ""}`} aria-label={step.done ? "done" : "not yet"} role="img" />
            <div>
              <b>{step.title}</b>
              <div className="muted">
                {step.detail}{" "}
                {step.action && (
                  <button type="button" className="btn sm" onClick={step.action}>
                    Show me
                  </button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

function Glossary({ guide }: { guide: LearnPayload }) {
  const [query, setQuery] = useState("");
  const terms = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return guide.terms.filter((item) => !needle || `${item.term} ${item.definition}`.toLowerCase().includes(needle));
  }, [guide.terms, query]);
  return (
    <Panel title="Glossary" aside={`${guide.terms.length} terms · dotted words everywhere in Studio explain themselves`} pad>
      <input
        type="search"
        className="input"
        placeholder="Search terms"
        aria-label="Search the glossary"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <dl className="glossary">
        {terms.map((item) => (
          <div key={item.id}>
            <dt>{item.term}</dt>
            <dd>{item.definition}</dd>
          </div>
        ))}
      </dl>
      {terms.length === 0 && <Empty>No term matches “{query}”.</Empty>}
    </Panel>
  );
}

export function LearnView() {
  const guide = useGuide();
  const [selected, setSelected] = useState(2);
  if (!guide) return <div className="page"><Loading height={420} /></div>;
  const stage = guide.stages[Math.min(selected, guide.stages.length - 1)];
  if (!stage) return <div className="page"><Empty>The framework guide is not available from this runner.</Empty></div>;
  return (
    <div className="page" style={{ display: "grid", gap: 16 }}>
      <header className="page-head">
        <div>
          <h1>How an idea becomes merged code</h1>
          <span className="sub">
            Every change is an Intent. It rides this line left to right; at each amber gate a person decides. Between gates, you and the
            agent write files and the runner checks them. Click a stop, or an Intent riding it.
          </span>
        </div>
      </header>
      <Metro guide={guide} selected={selected} onSelect={setSelected} />
      <StageDetail stage={stage} question={stage.gate ? guide.gates[stage.gate] : undefined} />
      <div className="grid-2">
        <StartHere guide={guide} />
        <Panel title="Who writes what" pad>
          <table className="grid">
            <thead>
              <tr><th>Who</th><th>Writes</th><th>Never</th></tr>
            </thead>
            <tbody>
              <tr><td><b>You</b></td><td>brief, specification, plan files</td><td>runner state, evidence</td></tr>
              <tr><td><b>Agent</b> (Claude Code)</td><td>code, tests, drafts of your files</td><td>a gate decision</td></tr>
              <tr><td><b>Reviewer agent</b></td><td>reviews, from a different model family</td><td>what it reviews</td></tr>
              <tr><td><b>Runner</b></td><td>ids, state, gates, evidence, audit log</td><td>a guess: it refuses instead</td></tr>
              <tr><td><b>Approver</b> (a person)</td><td>gate decisions G1–G5</td><td>—</td></tr>
            </tbody>
          </table>
        </Panel>
      </div>
      <div className="grid-2">
        <Glossary guide={guide} />
        <Panel title="Specification lint rules" aside="what each SQ code means and how to fix it">
          <div className="table-wrap">
            <table className="grid">
              <tbody>
                {guide.lint_rules.map((rule) => (
                  <tr key={rule.code}>
                    <td className="mono" style={{ whiteSpace: "nowrap" }}>{rule.code}</td>
                    <td><Pill tone={SEVERITY_TONE[rule.severity]}>{rule.severity}</Pill></td>
                    <td>
                      <b>{rule.title}.</b> {rule.explanation} <span className="muted">Fix: {rule.fix}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </div>
  );
}
