import { useRef, useState } from "react";
import type { OperationResult, PipelinePayload } from "@/lib/contract";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";
import { Term } from "./Guide";
import { Icon } from "./Icon";
import { OperationDialog, type OperationRequest } from "./OperationDialog";

const KINDS = [
  { id: "feature", label: "Feature", hint: "New behaviour a user or system can see." },
  { id: "bugfix", label: "Bug fix", hint: "Behaviour that is wrong today." },
  { id: "refactor", label: "Refactor", hint: "Same behaviour, better structure." },
  { id: "security", label: "Security", hint: "Closes a threat; needs an adversarial (Class N) check." },
  { id: "infrastructure", label: "Infrastructure", hint: "Build, packaging, deployment plumbing." },
  { id: "investigation_or_poc", label: "Investigation", hint: "A spike to learn something before committing." },
] as const;
const DISCOVERY = [
  { id: "new", reason: "new code; there is nothing existing to map" },
  { id: "mapped", reason: "the code it touches is already mapped in the project understanding" },
  { id: "small", reason: "small, well-understood change" },
] as const;

/** Full mode when the change carries risk the compact path cannot check; compact otherwise. */
export function recommendMode(kind: string, modelBehaviour: boolean): "compact" | "full" {
  return modelBehaviour || kind === "security" || kind === "feature" ? "full" : "compact";
}

/**
 * Start an Intent. The runner allocates the id and the folder; Studio only collects what
 * `ai-sdlc new` needs, explains the choices, and then opens the brief to write.
 */
export function NewIntentDialog({ onClose }: { onClose(): void }) {
  const navigate = useStudio((s) => s.navigate);
  const openFile = useStudio((s) => s.openFile);
  const refresh = useStudio((s) => s.refresh);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<string>("feature");
  const [model, setModel] = useState(false);
  const [mode, setMode] = useState<"compact" | "full" | null>(null);
  const [discovery, setDiscovery] = useState<string>("small");
  const [custom, setCustom] = useState("");
  const [operation, setOperation] = useState<OperationRequest | null>(null);
  const root = useRef<HTMLDivElement>(null);
  useFocusTrap(root, !operation);
  const pipeline = useProjection<PipelinePayload>("pipeline.inspect");
  const profileName = pipeline.data ? pipeline.data.by_type[kind] ?? pipeline.data.default : null;
  const profile = profileName ? pipeline.data?.profiles[profileName] : undefined;
  const allowed = (value: "compact" | "full") => (!model || value === "full") && (!profile || profile.modes.includes(value));
  const preferred = model ? "full" : mode ?? recommendMode(kind, model);
  const chosen = allowed(preferred) ? preferred : preferred === "full" ? "compact" : "full";
  const reason = discovery === "custom" ? custom.trim() : DISCOVERY.find((item) => item.id === discovery)?.reason ?? "";
  const valid = title.trim().length >= 3 && title.trim().length <= 120 && reason.length > 0 && !/[\r\n]/.test(title + reason);

  if (operation) {
    return (
      <OperationDialog
        request={operation}
        onResult={(result: OperationResult) => {
          const created = (result.output as { intent_id?: string } | null)?.intent_id;
          if (!result.succeeded || !created) return;
          void refresh();
          navigate({ view: "intent", intentId: created, tab: "overview" });
          openFile({ scope: "intent", intentId: created, path: "01-brief/brief.md", create: { template: "brief-template.md" } });
        }}
        onClose={() => onClose()}
      />
    );
  }

  return (
    <div className="overlay" role="presentation" onKeyDown={(event) => event.key === "Escape" && onClose()}>
      <div className="dialog wide" role="dialog" aria-modal="true" aria-labelledby="new-intent-title" ref={root} tabIndex={-1}>
        <div>
          <span className="eyebrow">Stage 0 · Register</span>
          <h2 id="new-intent-title">Start an <Term id="intent">Intent</Term></h2>
        </div>
        <label>
          What change do you want to make?
          <input autoFocus value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} placeholder="Status helper logs template format errors" />
        </label>

        <fieldset className="patterns">
          <legend>Kind</legend>
          <div className="pattern-grid three">
            {KINDS.map((item) => (
              <label key={item.id} className={`pattern${kind === item.id ? " on" : ""}`}>
                <input type="radio" name="kind" checked={kind === item.id} onChange={() => setKind(item.id)} />
                <b>{item.label}</b>
                <span>{item.hint}</span>
              </label>
            ))}
          </div>
          <label className="check">
            <input type="checkbox" checked={model} onChange={(event) => setModel(event.target.checked)} /> Part of the result is produced by an AI model (
            <Term id="model-behaviour">model behaviour</Term>)
          </label>
        </fieldset>

        <fieldset className="patterns">
          <legend>How much ceremony</legend>
          <div className="pattern-grid">
            {(["compact", "full"] as const).map((value) => (
              <label key={value} className={`pattern${chosen === value ? " on" : ""}${!allowed(value) ? " disabled" : ""}`}>
                <input type="radio" name="mode" disabled={!allowed(value)} checked={chosen === value} onChange={() => setMode(value)} />
                <b>
                  <Term id={value === "compact" ? "compact-mode" : "full-mode"}>{value === "compact" ? "Compact" : "Full"}</Term>
                  {recommendMode(kind, model) === value ? " · recommended" : ""}
                </b>
                <span>
                  {value === "compact"
                    ? "Spec and plan approved together (G2-G3). Only deterministic tests. About 3 files to write."
                    : "Brief, threat model when risky, evaluations, phased plan. About 8 files to write."}
                </span>
              </label>
            ))}
          </div>
          {model && <span className="muted">Model behaviour needs full mode: the runner refuses compact.</span>}
          {profile && profileName && (
            <div className="profile-hint">
              <Icon name="pipeline" size={14} />
              <span>
                {kind.replace(/_/g, " ")} work runs the <b className="mono">{profileName}</b> profile
                {profile.description ? `: ${profile.description}` : "."}
                {profile.modes.length === 1 && ` Only ${profile.modes[0]} mode.`}
                {profile.loosened.length > 0 && (
                  <span className="muted"> It relaxes {profile.loosened.map((item) => item.rule).join(", ")} by waiver.</span>
                )}
              </span>
            </div>
          )}
        </fieldset>

        <fieldset className="patterns">
          <legend>
            <Term id="discovery">Discovery</Term>: why is or isn't the existing code mapped first?
          </legend>
          {DISCOVERY.map((item) => (
            <label key={item.id} className="check">
              <input type="radio" name="discovery" checked={discovery === item.id} onChange={() => setDiscovery(item.id)} /> {item.reason}
            </label>
          ))}
          <label className="check">
            <input type="radio" name="discovery" checked={discovery === "custom"} onChange={() => setDiscovery("custom")} /> Something else:
            <input value={custom} maxLength={500} onChange={(event) => { setDiscovery("custom"); setCustom(event.target.value); }} style={{ flex: 1 }} />
          </label>
        </fieldset>

        <div className="row">
          <span className="muted" style={{ fontSize: 12.5 }}>The runner allocates the id. Next you write the brief.</span>
          <span style={{ flex: 1 }} />
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={!valid}
            onClick={() =>
              setOperation({
                name: "intent_new",
                title: "Start an Intent",
                args: { title: title.trim(), intent_type: kind, mode: chosen, discovery_reason: reason, contains_model_behavior: model },
                explanation: "The runner allocates the next Intent id and creates its folder. Nothing else changes.",
              })
            }
          >
            Review
          </button>
        </div>
      </div>
    </div>
  );
}
