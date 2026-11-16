import { useRef, useState } from "react";
import type { ComposePayload, VerificationClass } from "@/lib/contract";
import { errorMessage, getTransport } from "@/lib/ipc";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useStudio } from "@/state/store";
import { useGuide } from "./Guide";
import { OperationDialog, type OperationRequest } from "./OperationDialog";
import { ClassGlyph } from "./ui";

/** The five EARS sentence shapes. The keyword leads the sentence; the rest is the slots. */
const PATTERNS = [
  { id: "always", name: "Always", shape: "The <system> shall <response>.", keyword: "" },
  { id: "event", name: "Event", shape: "When <trigger>, the <system> shall <response>.", keyword: "When" },
  { id: "state", name: "State", shape: "While <state>, the <system> shall <response>.", keyword: "While" },
  { id: "unwanted", name: "Unwanted", shape: "If <condition>, then the <system> shall <response>.", keyword: "If" },
  { id: "optional", name: "Feature", shape: "Where <feature>, the <system> shall <response>.", keyword: "Where" },
] as const;
const CLASS_TERM: Record<VerificationClass, string> = { D: "class-d", S: "class-s", J: "class-j", N: "class-n" };
const QUALITY = ["performance", "security", "privacy", "accessibility", "reliability", "availability", "usability", "compliance"];

export function sentence(pattern: (typeof PATTERNS)[number]["id"], trigger: string, system: string, response: string): string {
  const shape = PATTERNS.find((item) => item.id === pattern)!;
  const lead = shape.keyword ? `${shape.keyword} ${trigger.trim()}, ${pattern === "unwanted" ? "then " : ""}the` : "The";
  const text = `${lead} ${system.trim()} shall ${response.trim().replace(/\.$/, "")}.`;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Add a requirement without hand-writing YAML. Studio collects the parts; the runner
 * composes the new specification (allocating ids it never reuses) and the save goes through
 * the same preview as any edit: the diff plus the runner's own lint of the result. Studio
 * adds no lint rules of its own, so what it says is exactly what the runner will say.
 */
export function RequirementComposer({ intentId, onClose }: { intentId: string; onClose(changed: boolean): void }) {
  const guide = useGuide();
  const projectId = useStudio((s) => s.project?.projectId);
  const [kind, setKind] = useState<"functional" | "non_functional">("functional");
  const [pattern, setPattern] = useState<(typeof PATTERNS)[number]["id"]>("event");
  const [trigger, setTrigger] = useState("");
  const [system, setSystem] = useState("service");
  const [response, setResponse] = useState("");
  const [priority, setPriority] = useState("must");
  const [source, setSource] = useState("brief");
  const [category, setCategory] = useState("performance");
  const [measure, setMeasure] = useState({ metric: "", target: "", unit: "" });
  const [klass, setKlass] = useState<VerificationClass>("D");
  const [criterion, setCriterion] = useState({ statement: "", check: "", eval: "", dataset: "", metric: "", value: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [operation, setOperation] = useState<OperationRequest | null>(null);
  const root = useRef<HTMLDivElement>(null);
  useFocusTrap(root, !operation);

  const needsTrigger = pattern !== "always";
  const statement = sentence(pattern, trigger, system, response);
  const complete =
    !!response.trim() && !!system.trim() && (!needsTrigger || !!trigger.trim()) && !!criterion.statement.trim() &&
    (klass === "D" ? !!criterion.check.trim() : !!criterion.eval.trim() && !!criterion.dataset.trim() && !!criterion.metric.trim());

  async function review() {
    if (!projectId) return;
    setBusy(true);
    setError(null);
    const requirement: Record<string, unknown> = { priority, sources: [source.trim() || "brief"], statement };
    if (kind === "non_functional") {
      requirement.category = category;
      if (measure.metric.trim()) {
        requirement.measure = { metric: measure.metric.trim(), target: Number(measure.target) || measure.target, unit: measure.unit.trim() };
      }
    }
    const item: Record<string, unknown> = { verification_class: klass, statement: criterion.statement.trim() };
    if (klass === "D") item.check = criterion.check.trim();
    else {
      Object.assign(item, {
        eval: criterion.eval.trim(),
        dataset: criterion.dataset.trim(),
        metric: criterion.metric.trim(),
        threshold: klass === "N" ? { operator: "==", value: 0 } : { operator: ">=", value: Number(criterion.value) || 0 },
        minimum_sample_size: 3,
        ...(klass === "N" ? {} : { tolerance_band: 0.03 }),
      });
    }
    try {
      const envelope = await getTransport().bridgeRequest<ComposePayload>("authoring.compose", {
        project_id: projectId,
        intent_id: intentId,
        kind,
        requirement,
        criteria: [item],
      });
      if (envelope.error) throw new Error(envelope.error.message);
      const composed = envelope.payload;
      setOperation({
        name: "artifact_write",
        title: `Add ${composed.ids.requirement} to ${intentId}`,
        args: { intent_id: intentId, path: composed.path, content: composed.content, expected_sha: composed.expected_sha },
        explanation: `The runner gives the requirement id ${composed.ids.requirement} and its criterion ${composed.ids.criteria.join(
          ", ",
        )}, and adds them to the specification. Your comments and ordering are kept.`,
      });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (operation) {
    return <OperationDialog request={operation} onClose={(changed) => (changed ? onClose(true) : setOperation(null))} />;
  }

  const term = (id: string) => guide?.terms.find((item) => item.id === id)?.definition;
  return (
    <div className="overlay" role="presentation" onKeyDown={(event) => event.key === "Escape" && onClose(false)}>
      <div className="dialog wide" role="dialog" aria-modal="true" aria-labelledby="composer-title" ref={root} tabIndex={-1}>
        <div>
          <span className="eyebrow">{intentId} · 02-specification/requirements.yaml</span>
          <h2 id="composer-title">Add a requirement</h2>
        </div>

        <fieldset className="choices">
          <legend>What kind</legend>
          <label><input type="radio" name="kind" checked={kind === "functional"} onChange={() => setKind("functional")} /> What the system does</label>
          <label><input type="radio" name="kind" checked={kind === "non_functional"} onChange={() => setKind("non_functional")} /> How well it does it (quality)</label>
        </fieldset>

        <fieldset className="patterns" aria-describedby="ears-help">
          <legend>1 · Sentence shape</legend>
          <span id="ears-help" className="muted">{term("ears")}</span>
          <div className="pattern-grid">
            {PATTERNS.map((item) => (
              <label key={item.id} className={`pattern${pattern === item.id ? " on" : ""}`}>
                <input type="radio" name="pattern" checked={pattern === item.id} onChange={() => setPattern(item.id)} />
                <b>{item.name}</b>
                <span className="mono">{item.shape}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="fields">
          <legend>2 · Fill it in</legend>
          {needsTrigger && (
            <label>
              {PATTERNS.find((item) => item.id === pattern)!.keyword}
              <input value={trigger} onChange={(event) => setTrigger(event.target.value)} placeholder="the learner's locale is right-to-left" />
            </label>
          )}
          <label>
            the
            <input value={system} onChange={(event) => setSystem(event.target.value)} />
          </label>
          <label>
            shall
            <input value={response} onChange={(event) => setResponse(event.target.value)} placeholder="mark each status with its text direction" />
          </label>
          <label>
            Priority
            <select value={priority} onChange={(event) => setPriority(event.target.value)}>
              <option value="must">must</option>
              <option value="should">should</option>
              <option value="could">could</option>
              <option value="wont">won't (this time)</option>
            </select>
          </label>
          <label>
            Came from
            <input value={source} onChange={(event) => setSource(event.target.value)} placeholder="brief: Problem" />
          </label>
          {kind === "non_functional" && (
            <>
              <label>
                Category
                <select value={category} onChange={(event) => setCategory(event.target.value)}>
                  {QUALITY.map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
              </label>
              <label>
                Measure
                <span className="row" style={{ gap: 6 }}>
                  <input aria-label="Metric" placeholder="p95 latency" value={measure.metric} onChange={(e) => setMeasure({ ...measure, metric: e.target.value })} />
                  <input aria-label="Target" placeholder="300" value={measure.target} onChange={(e) => setMeasure({ ...measure, target: e.target.value })} style={{ width: 90 }} />
                  <input aria-label="Unit" placeholder="ms" value={measure.unit} onChange={(e) => setMeasure({ ...measure, unit: e.target.value })} style={{ width: 70 }} />
                </span>
              </label>
            </>
          )}
        </fieldset>

        <div className="sentence" aria-live="polite">
          <span className="muted">The requirement will read:</span>
          <br />
          {statement}
        </div>

        <fieldset className="patterns">
          <legend>3 · How will we know it works?</legend>
          <div className="pattern-grid four">
            {(["D", "S", "J", "N"] as VerificationClass[]).map((value) => (
              <label key={value} className={`pattern${klass === value ? " on" : ""}`}>
                <input type="radio" name="class" checked={klass === value} onChange={() => setKlass(value)} />
                <b><ClassGlyph value={value} /> {guide?.terms.find((item) => item.id === CLASS_TERM[value])?.term ?? `Class ${value}`}</b>
                <span>{term(CLASS_TERM[value])}</span>
              </label>
            ))}
          </div>
          <label>
            Criterion
            <input value={criterion.statement} onChange={(e) => setCriterion({ ...criterion, statement: e.target.value })} placeholder="RTL statuses carry dir=rtl." />
          </label>
          {klass === "D" ? (
            <label>
              Command
              <input className="mono" value={criterion.check} onChange={(e) => setCriterion({ ...criterion, check: e.target.value })} placeholder="python -m pytest tests/i18n -k rtl" />
            </label>
          ) : (
            <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
              <input aria-label="Evaluation id" placeholder="EVAL-004" value={criterion.eval} onChange={(e) => setCriterion({ ...criterion, eval: e.target.value })} style={{ width: 110 }} />
              <input aria-label="Dataset" placeholder="regression/tenant-isolation" value={criterion.dataset} onChange={(e) => setCriterion({ ...criterion, dataset: e.target.value })} />
              <input aria-label="Metric" placeholder={klass === "N" ? "attack_success" : "accuracy"} value={criterion.metric} onChange={(e) => setCriterion({ ...criterion, metric: e.target.value })} />
              {klass !== "N" && (
                <input aria-label="Threshold" placeholder="0.9" value={criterion.value} onChange={(e) => setCriterion({ ...criterion, value: e.target.value })} style={{ width: 80 }} />
              )}
              <span className="muted" style={{ fontSize: 12 }}>
                {klass === "N" ? "Passes only with zero attacker successes." : "Three runs; the median must reach the threshold."}
              </span>
            </div>
          )}
        </fieldset>

        {error && <div className="error-box">{error}</div>}
        <div className="row">
          <span className="muted" style={{ fontSize: 12.5 }}>Next: the exact change, and what the runner's checks say about it.</span>
          <span style={{ flex: 1 }} />
          <button type="button" className="btn" onClick={() => onClose(false)}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={!complete || busy} onClick={() => void review()}>
            {busy ? "Composing…" : "Review the change"}
          </button>
        </div>
      </div>
    </div>
  );
}
