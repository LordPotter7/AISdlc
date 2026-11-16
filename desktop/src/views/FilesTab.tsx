import { useMemo, useState } from "react";
import type { IntentDetail } from "@/lib/contract";
import { ArtifactPane } from "@/components/ArtifactPane";
import { Icon } from "@/components/Icon";
import { Empty } from "@/components/ui";
import { useStudio } from "@/state/store";

/** The files a person writes, as the runner's `AUTHORED` allow-set names them. */
const AUTHORED = [
  /^01-brief\/brief\.md$/,
  /^02-specification\/(requirements|threat-model)\.ya?ml$/,
  /^02-specification\/eval-design\.md$/,
  /^02-specification\/contracts\/.+\.(ya?ml|json)$/,
  /^03-planning\/master-plan\.md$/,
  /(^|\/)phase\.ya?ml$/,
  /(^|\/)task\.ya?ml$/,
  /^05-verification\/findings\.ya?ml$/,
];
const isAuthored = (path: string) => AUTHORED.some((pattern) => pattern.test(path));
const isEvidence = (path: string) => path.includes("/evidence/");
/** Long Task paths keep the part that tells them apart: `I003-P00/tasks/I003-P00-T001/task.yaml` → `P00-T001/task.yaml`. */
const shortName = (path: string) => path.replace(/^[^/]+\/tasks\/I\d+-(P\d+-T\d+)\//, "$1/");

/**
 * Every file of one Intent, by stage, beside the workbench. Files you author open ready to
 * edit where the runner allows it; runner-owned records open read-only. Unsaved changes
 * keep you on the file until you save or discard them.
 */
export function FilesTab({ intent }: { intent: IntentDetail }) {
  const toast = useStudio((s) => s.toast);
  const [filter, setFilter] = useState("");
  const [dirty, setDirty] = useState(false);
  const stages = useMemo(
    () =>
      intent.rail.map((stage) => {
        const paths = intent.artifacts[stage.id] ?? [];
        return { stage, files: paths.filter((path) => !isEvidence(path)), evidence: paths.filter(isEvidence).length };
      }),
    [intent],
  );
  const initial = useMemo(() => {
    const working = stages.find((entry) => entry.stage.state === "current") ?? stages.find((entry) => entry.files.length);
    const preferred = working?.files.find(isAuthored) ?? working?.files[0];
    return preferred ?? stages.flatMap((entry) => entry.files)[0] ?? null;
  }, [stages]);
  const [selected, setSelected] = useState<{ path: string; line: number | null } | null>(null);
  const current = selected ?? (initial ? { path: initial, line: null } : null);

  const pick = (path: string, line: number | null = null) => {
    if (dirty && path !== current?.path) {
      toast({ tone: "info", title: "Save or discard your changes first", detail: `${current?.path} has changes that are not saved.` });
      return;
    }
    setSelected({ path, line });
  };

  const needle = filter.trim().toLowerCase();
  if (!current) return <Empty>This Intent has no files yet. The brief is the first one, written in Frame.</Empty>;
  return (
    <div className="files">
      <nav className="file-tree" aria-label="Intent files">
        <input
          type="search"
          className="proof-search"
          aria-label="Filter files"
          placeholder="Filter files"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        {stages.map(({ stage, files, evidence }) => {
          const visible = files.filter((path) => !needle || path.toLowerCase().includes(needle));
          if (needle && visible.length === 0) return null;
          return (
            <section key={stage.id} className={`tree-stage ${stage.state}`}>
              <h3 title={stage.folder}>
                <span>{stage.title}</span>
                {stage.gate && (
                  <span className="tree-gate" title={`Gate ${stage.gate}: ${stage.gate_record?.status ?? "not decided"}`}>
                    <Icon name="gate" size={10} /> {stage.gate}
                  </span>
                )}
              </h3>
              {visible.length === 0 ? (
                <p className="muted tree-empty">nothing yet</p>
              ) : (
                <ul>
                  {visible.map((path) => (
                    <li key={path}>
                      <button
                        type="button"
                        className="tree-file"
                        aria-current={path === current.path ? "true" : undefined}
                        onClick={() => pick(path)}
                        title={`${path}: ${isAuthored(path) ? "you author this file" : "the runner writes this file"}`}
                      >
                        <Icon name={isAuthored(path) ? "file" : "lock"} size={13} />
                        <span className="mono">{shortName(path.replace(`${stage.folder}/`, ""))}</span>
                        {path === current.path && dirty && <span className="dirty" aria-label="unsaved changes">●</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {evidence > 0 && !needle && <p className="muted tree-empty">+ {evidence} evidence files the runner captured</p>}
            </section>
          );
        })}
        <p className="tree-legend muted">
          <Icon name="file" size={12} /> you author <Icon name="lock" size={12} /> runner-owned
        </p>
      </nav>
      <div className="files-pane">
        <ArtifactPane
          key={current.path}
          target={{ scope: "intent", intentId: intent.id, path: current.path, line: current.line }}
          layout="page"
          onOpen={(path, line) => pick(path, line)}
          onDirty={setDirty}
        />
      </div>
    </div>
  );
}
