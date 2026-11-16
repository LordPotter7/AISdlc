import { useMemo, useState } from "react";
import type { AdoptionStep, CodebaseModule, CodebasePayload } from "@/lib/contract";
import { plural, when } from "@/lib/format";
import { Icon } from "@/components/Icon";
import { Empty, ErrorBox, Loading, Panel, Pill, type Tone } from "@/components/ui";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

type Filter = "code" | "changed" | "contended" | "all";

const UNDERSTANDING: Record<string, { tone: Tone; label: string }> = {
  fresh: { tone: "ok", label: "fresh" },
  drifting: { tone: "warn", label: "drifting" },
  stale: { tone: "bad", label: "stale" },
  absent: { tone: "muted", label: "not recorded" },
  invalid: { tone: "bad", label: "invalid" },
};

/** Copy text, and say so; where the clipboard is unavailable, say how to copy by hand. */
function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const toast = useStudio((s) => s.toast);
  return (
    <button
      type="button"
      className="btn sm"
      aria-label={`${label}: ${text.length > 60 ? `${text.slice(0, 60)}…` : text}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast({ tone: "ok", title: "Copied", detail: text.length > 90 ? `${text.slice(0, 90)}…` : text });
        } catch {
          toast({ tone: "info", title: "Copy it by hand", detail: "The clipboard is not available here; select the text instead." });
        }
      }}
    >
      <Icon name="copy" size={13} /> {label}
    </button>
  );
}

function Adoption({ steps, complete }: { steps: AdoptionStep[]; complete: boolean }) {
  const setOverlay = useStudio((s) => s.setOverlay);
  const [open, setOpen] = useState(!complete);
  const next = steps.find((step) => !step.done)?.id;
  return (
    <section className={`adopt ${complete ? "complete" : ""}`} aria-labelledby="adopt-title">
      <header>
        <h2 id="adopt-title">{complete ? "Adoption complete" : "Adopt this repository"}</h2>
        <span className="muted">
          {complete
            ? "The runner manages it, its understanding and baseline are recorded, and work is under way."
            : `${steps.filter((step) => step.done).length} of ${steps.length} done. Each step is a runner command; the app shows where things stand and hands off the rest.`}
        </span>
        {complete && (
          <button type="button" className="btn sm" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? "Hide steps" : "Show steps"}
          </button>
        )}
      </header>
      {open && (
        <ol className="adopt-steps">
          {steps.map((step, index) => (
            <li key={step.id} className={`${step.done ? "done" : ""}${step.id === next ? " next" : ""}`}>
              <span className="adopt-dot" aria-hidden="true">{step.done ? <Icon name="check" size={13} /> : index + 1}</span>
              <div className="adopt-text">
                <b>
                  {step.title}
                  <span className="sr-only">{step.done ? ", done" : step.id === next ? ", next" : ", to do"}</span>
                </b>
                {step.state && step.id === "understand" && (
                  <Pill tone={UNDERSTANDING[step.state]?.tone ?? "muted"}>{UNDERSTANDING[step.state]?.label ?? step.state}</Pill>
                )}
                <p>{step.detail}</p>
                {(!step.done || step.state === "drifting") && (
                  <div className="adopt-do">
                    {step.id === "intent" ? (
                      <button type="button" className="btn sm primary" onClick={() => setOverlay("newIntent", true)}>
                        Start an Intent
                      </button>
                    ) : (
                      <>
                        <span>In Claude Code</span>
                        <code>{step.handoff}</code>
                        <CopyButton text={step.handoff} />
                        <span>or the runner</span>
                        <code>{step.command}</code>
                        <CopyButton text={step.command} />
                      </>
                    )}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function LanguageBar({ languages }: { languages: CodebasePayload["inventory"]["languages"] }) {
  const code = languages.filter((item) => item.code);
  const total = code.reduce((sum, item) => sum + item.files, 0) || 1;
  const top = code.slice(0, 4);
  const rest = total - top.reduce((sum, item) => sum + item.files, 0);
  return (
    <div className="lang">
      <div className="lang-bar" aria-hidden="true">
        {top.map((item, index) => (
          <i key={item.language} className={`l${index}`} style={{ flexGrow: item.files }} />
        ))}
        {rest > 0 && <i className="lrest" style={{ flexGrow: rest }} />}
      </div>
      <ul className="lang-legend">
        {top.map((item, index) => (
          <li key={item.language}>
            <i className={`l${index}`} aria-hidden="true" /> {item.language} <span className="muted">{item.files / total < 0.01 ? "<1" : Math.round((item.files / total) * 100)}%</span>
          </li>
        ))}
        {code.length === 0 && <li className="muted">No source files recognised</li>}
      </ul>
    </div>
  );
}

function ModuleRow({ module }: { module: CodebaseModule }) {
  const navigate = useStudio((s) => s.navigate);
  return (
    <tr className={module.contended ? "contended" : undefined}>
      <td>
        <span className="mono module-path">{module.path}/</span>
        {module.kind !== "code" && <span className="module-kind">{module.kind}</span>}
      </td>
      <td>{module.languages.join(", ") || <span className="muted">—</span>}</td>
      <td className="num">{module.files}</td>
      <td className="num">{module.test_files || <span className="muted">0</span>}</td>
      <td>{module.owners.length ? <span className="mono">{module.owners.join(" ")}</span> : <span className="muted">unknown</span>}</td>
      <td>
        <div className="chips">
          {module.intents.length === 0 && <span className="muted">—</span>}
          {module.intents.map((item) => (
            <button
              key={item.intent}
              type="button"
              className={`chip-id intent-chip ${item.active ? "active" : ""}`}
              title={`${item.intent} ${item.title ?? ""}: ${item.tasks.join(", ")} change ${item.paths.join(", ")}`}
              onClick={() => navigate({ view: "intent", intentId: item.intent, tab: "overview" })}
            >
              <span className={`dot ${item.active ? "info" : "ok"}`} aria-hidden="true" />
              {item.intent}
            </button>
          ))}
          {module.contended && (
            <Pill tone="warn" title="More than one open Intent declares paths here. Coordinate them, or finish one first.">
              {module.intents.filter((item) => item.active).length} open Intents
            </Pill>
          )}
        </div>
      </td>
    </tr>
  );
}

/**
 * The repository as a codebase: what git tracks at HEAD (languages, modules, owners, tests,
 * CI, entry points), how far adoption has come, and which Intents change which modules.
 * Everything is recomputed from the commit and never stored; nothing here binds until a
 * person copies it into the runner's files.
 */
export function CodebaseView() {
  const { data, error, loading, reload } = useProjection<CodebasePayload>("codebase.inspect");
  const [filter, setFilter] = useState<Filter | null>(null);
  const [query, setQuery] = useState("");
  const modules = useMemo(() => data?.modules ?? [], [data]);

  if (loading && !data) return <div className="page"><Loading height={420} /></div>;
  if (error) return <div className="page"><ErrorBox message={error} retry={reload} /></div>;
  if (!data) return null;

  const { inventory } = data;
  const counts: Record<Filter, number> = {
    code: modules.filter((m) => m.kind === "code" || m.kind === "tests").length,
    changed: modules.filter((m) => m.intents.length > 0).length,
    contended: modules.filter((m) => m.contended).length,
    all: modules.length,
  };
  const active: Filter = filter ?? (counts.contended ? "contended" : "code");
  const needle = query.trim().toLowerCase();
  const shown = modules.filter((m) => {
    const kept =
      active === "all" ? true
      : active === "code" ? m.kind === "code" || m.kind === "tests"
      : active === "changed" ? m.intents.length > 0
      : m.contended;
    const text = `${m.path} ${m.languages.join(" ")} ${m.owners.join(" ")} ${m.intents.map((i) => i.intent).join(" ")}`.toLowerCase();
    return kept && (!needle || text.includes(needle));
  });
  const probes =
    "probes:\n" +
    inventory.proposed_probes
      .map((probe) => `  - name: ${probe.name}\n    kind: ${probe.kind}\n    command: ${JSON.stringify(probe.command)}\n    timeout_seconds: ${probe.timeout_seconds}`)
      .join("\n") +
    "\n";
  const testFiles = inventory.test_suites.reduce((sum, suite) => sum + (suite.test_files ?? 0), 0);
  const understanding = UNDERSTANDING[data.understanding.status] ?? UNDERSTANDING.absent!;

  return (
    <div className="page codebase">
      <header className="page-head">
        <div>
          <h1>Codebase</h1>
          <span className="sub">
            What this repository contains, read from the files git tracks at{" "}
            <span className="mono">@{inventory.commit?.slice(0, 7) ?? "no commit"}</span>. Recomputed for every commit and never
            stored; suggestions here bind nothing until a person copies them into the runner's files.
          </span>
        </div>
      </header>

      <Adoption steps={data.adoption.steps} complete={data.adoption.complete} />

      <div className="cb-summary">
        <div className="proof-tile">
          <span className="label">Tracked files</span>
          <span className="value num">{inventory.tracked_files.toLocaleString()}</span>
          <span className="detail">{plural(modules.length, "module")}, {inventory.root_files} at the root</span>
        </div>
        <div className="proof-tile wide">
          <span className="label">Written in</span>
          <LanguageBar languages={inventory.languages} />
        </div>
        <div className="proof-tile">
          <span className="label">Test suites</span>
          <span className="value num">{inventory.test_suites.length}</span>
          <span className="detail">{testFiles ? `${testFiles.toLocaleString()} test files` : "no test files recognised"}</span>
        </div>
        <div className="proof-tile">
          <span className="label">Owners</span>
          <span className="value">{inventory.owners.file ? plural(inventory.owners.rules, "rule") : "Unknown"}</span>
          <span className="detail mono">{inventory.owners.file ?? "no CODEOWNERS file"}</span>
        </div>
        <div className="proof-tile">
          <span className="label">CI</span>
          <span className="value">{inventory.ci.length ? [...new Set(inventory.ci.map((c) => c.system))].join(", ") : "None found"}</span>
          <span className="detail">{plural(inventory.ci.length, "pipeline file")}</span>
        </div>
      </div>
      {inventory.notes.length > 0 && (
        <ul className="cb-notes">
          {inventory.notes.map((note) => (
            <li key={note}><Icon name="alert" size={13} /> {note}</li>
          ))}
        </ul>
      )}

      <div className="proof-body">
        <div className="proof-main">
          <div className="proof-bar-row">
            <span className="filter-chips" role="group" aria-label="Show modules">
              {(["contended", "changed", "code", "all"] as Filter[]).map((value) => (
                <button key={value} type="button" className="filter-chip" aria-pressed={active === value} onClick={() => setFilter(value)}>
                  {value === "contended" ? "Changed by 2+ open Intents" : value === "changed" ? "Changed by an Intent" : value === "code" ? "Code and tests" : "All"}
                  <span className="n">{counts[value]}</span>
                </button>
              ))}
            </span>
            <input
              type="search"
              className="proof-search"
              aria-label="Find a module, language, owner or Intent"
              placeholder="Find a module, owner or Intent"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          {shown.length === 0 ? (
            <Empty>{active === "contended" ? "No module is changed by more than one open Intent." : "No module matches."}</Empty>
          ) : (
            <div className="table-wrap">
              <table className="grid modules">
                <thead>
                  <tr>
                    <th>Module</th>
                    <th>Written in</th>
                    <th className="num">Files</th>
                    <th className="num">Tests</th>
                    <th>Owners</th>
                    <th>Intents changing it</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((module) => (
                    <ModuleRow key={module.path} module={module} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data.unmapped.length > 0 && (
            <p className="muted" style={{ margin: 0 }}>
              {plural(data.unmapped.length, "declared path")} outside every module (at the root):{" "}
              <span className="mono">{[...new Set(data.unmapped.map((item) => item.path))].slice(0, 6).join(", ")}</span>
            </p>
          )}
        </div>

        <aside className="proof-side" aria-label="Tests, baselines and understanding">
          <Panel title="Tests and proposed probes" aside={plural(inventory.test_suites.length, "suite")}>
            {inventory.test_suites.length === 0 ? (
              <Empty>No test suite recognised. A baseline can still pin build or lint commands.</Empty>
            ) : (
              <div className="panel-pad suites">
                {inventory.test_suites.map((suite) => (
                  <div key={`${suite.framework}-${suite.root}`} className="suite">
                    <div className="row" style={{ gap: 6 }}>
                      <b>{suite.framework}</b>
                      <span className="mono muted">{suite.root}/</span>
                      {suite.test_files != null && <span className="muted">{plural(suite.test_files, "test file")}</span>}
                    </div>
                    <code className="suite-cmd">{suite.command}</code>
                    <span className="muted suite-why">Inferred from {suite.evidence.slice(0, 2).join(", ")}</span>
                  </div>
                ))}
                <p className="muted" style={{ margin: 0 }}>
                  These are proposals. Copy the ones you accept into a probes file and capture a baseline; the runner records
                  each exit code as a fact, never as a verdict.
                </p>
                <div className="row" style={{ gap: 6 }}>
                  <CopyButton text={probes} label="Copy as probes.yaml" />
                </div>
              </div>
            )}
          </Panel>
          <Panel title="Baselines" aside={data.baselines.length ? plural(data.baselines.length, "baseline") : undefined}>
            {data.baselines.length === 0 ? (
              <Empty>None yet. A baseline pins what the system does today, so the first Intents can promise no regression.</Empty>
            ) : (
              <ul className="plain panel-pad">
                {data.baselines.map((baseline) => (
                  <li key={baseline.id}>
                    <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                      <b className="mono">{baseline.id}</b> {baseline.label}
                      <span className="muted">at @{baseline.commit?.slice(0, 7)}, {when(baseline.captured_at)}</span>
                    </div>
                    <ul className="probe-list">
                      {baseline.probes.map((probe) => (
                        <li key={probe.name}>
                          <Pill tone={probe.exit_code === 0 ? "ok" : "warn"}>exit {probe.exit_code ?? "?"}</Pill>{" "}
                          <span className="mono">{probe.name}</span>
                          {probe.value != null && <span className="muted"> value {probe.value}</span>}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Understanding" aside={<Pill tone={understanding.tone}>{understanding.label}</Pill>} pad>
            {data.understanding.status === "absent" ? (
              <p className="muted" style={{ margin: 0 }}>
                Not recorded. <code>/aisdlc:understand repo</code> drafts it from this scan with every claim anchored to code.
              </p>
            ) : (
              <div className="plain" style={{ display: "grid", gap: 6, fontSize: 13 }}>
                <span>
                  From the {data.understanding.source ?? "repo"}, pinned at <span className="mono">@{data.understanding.pinned_commit?.slice(0, 7)}</span>
                  {data.understanding.recorded_at ? `, ${when(data.understanding.recorded_at)}` : ""}.
                </span>
                <span className="muted">
                  {plural(data.understanding.anchored_paths ?? 0, "anchored path")}
                  {data.understanding.changed_paths?.length ? `, ${data.understanding.changed_paths.length} changed since` : ", none changed since"}.
                </span>
              </div>
            )}
          </Panel>
          {inventory.entry_points.length > 0 && (
            <Panel title="Entry points" aside={inventory.entry_points.length}>
              <ul className="plain panel-pad entry-list">
                {inventory.entry_points.slice(0, 12).map((item) => (
                  <li key={`${item.kind}-${item.path}-${item.detail ?? ""}`}>
                    <span className="mono">{item.detail ?? item.path}</span> <span className="muted">{item.kind}</span>
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

/** Where one Intent changes the code: its modules, their owners, and other open Intents there. */
export function IntentFootprint({ intentId }: { intentId: string }) {
  const navigate = useStudio((s) => s.navigate);
  const { data } = useProjection<CodebasePayload>("codebase.inspect");
  const footprint = data?.footprint[intentId];
  if (!data) return null;
  return (
    <Panel title="Where this Intent changes the code" aside={footprint ? plural(footprint.modules.length, "module") : undefined} pad>
      {!footprint ? (
        <p className="muted" style={{ margin: 0 }}>No Task declares affected paths yet. Planning writes them into each task.yaml.</p>
      ) : (
        <div className="footprint">
          <div className="chips">
            {footprint.modules.map((path) => (
              <button key={path} type="button" className="chip-id" onClick={() => navigate({ view: "codebase" })} title="Open the codebase map">
                {path}/
              </button>
            ))}
          </div>
          <span className="muted">
            Owners to involve: {footprint.owners.length ? <span className="mono">{footprint.owners.join(" ")}</span> : "unknown (no CODEOWNERS)"}
          </span>
          {footprint.shared_with.length > 0 && (
            <div className="footprint-shared">
              <Icon name="alert" size={14} />
              <span>
                Also changed by open{" "}
                {footprint.shared_with.map((id, index) => (
                  <span key={id}>
                    {index ? ", " : ""}
                    <button type="button" className="link-button mono" onClick={() => navigate({ view: "intent", intentId: id, tab: "overview" })}>
                      {id}
                    </button>
                  </span>
                ))}
                . Coordinate the overlap, or finish one first.
              </span>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

