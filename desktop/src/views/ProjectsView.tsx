import { useStudio } from "@/state/store";
import { getTransport } from "@/lib/ipc";
import { ago } from "@/lib/format";
import { Panel, Pill } from "@/components/ui";

export function ProjectsView() {
  const registry = useStudio((s) => s.registry);
  const project = useStudio((s) => s.project);
  const opening = useStudio((s) => s.opening);
  const open = useStudio((s) => s.open);
  const addRepository = useStudio((s) => s.addRepository);
  const replay = getTransport().mode === "replay";

  return (
    <div className="page projects">
      <header className="page-head">
        <div>
          <h1>Open a repository run by AISDLC</h1>
          <span className="sub">Studio shows what the runner records, and changes it only through the runner.</span>
        </div>
        <div className="actions">
          <button type="button" className="btn primary" onClick={() => void addRepository()} disabled={replay || opening}>
            Add repository…
          </button>
        </div>
      </header>

      {replay && (
        <div className="note info">
          Browser preview. You are looking at envelopes recorded from the real runner over the generated demo repository
          (<span className="mono">tools/make_demo_repo.py</span>). Open the desktop app to work with your own repositories.
        </div>
      )}

      <Panel title="Registered" aside={`${registry.length}`}>
        {registry.length === 0 ? (
          <div className="hero-empty">
            <h2>No repositories yet</h2>
            <p className="ink2" style={{ margin: 0 }}>
              Add a folder that has run <span className="mono">ai-sdlc init</span>. Studio shows its Intents, gates, traceability, and history,
              and follows changes live while you work in Claude Code.
            </p>
            <div>
              <button type="button" className="btn primary" onClick={() => void addRepository()} disabled={replay}>
                Add repository…
              </button>
            </div>
          </div>
        ) : (
          registry.map((entry) => (
            <div className="repo" key={entry.id}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <b>{entry.label}</b> {entry.missing && <Pill tone="bad">folder missing</Pill>}{" "}
                {project?.registryId === entry.id && <Pill tone="ok">open</Pill>}
                <div className="path">{entry.root}</div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {entry.last_opened ? `opened ${ago(entry.last_opened)}` : "never opened"}
                </div>
              </div>
              <button type="button" className="btn" disabled={opening || entry.missing} onClick={() => void open(entry)}>
                {opening ? "Opening…" : "Open"}
              </button>
            </div>
          ))
        )}
      </Panel>
    </div>
  );
}
