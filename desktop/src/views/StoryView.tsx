import { useEffect, useMemo, useRef, useState } from "react";
import type { AuditEvent, Portfolio, StoryChapter, StoryEnforcement, StoryPayload } from "@/lib/contract";
import { plural, when } from "@/lib/format";
import { Icon, type IconName } from "@/components/Icon";
import { Empty, ErrorBox, Loading, Pill } from "@/components/ui";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";
import { describeEvent } from "./CockpitView";

/** What the runner put on the record, as an icon and a tone. Amber only for gate decisions. */
const ENFORCED: Record<string, { icon: IconName; tone: string; label: string }> = {
  refused: { icon: "alert", tone: "bad", label: "Refused" },
  gate_approved: { icon: "gate", tone: "gate", label: "Gate approved" },
  gate_reopened: { icon: "refresh", tone: "gate", label: "Gate reopened" },
  adversarial_review: { icon: "search", tone: "info", label: "Adversarial review" },
  inherited: { icon: "arrowRight", tone: "info", label: "Inherited control" },
  independent_review: { icon: "check", tone: "ok", label: "Independent review" },
  task_failed: { icon: "close", tone: "bad", label: "Attempt failed" },
};

const PROVENANCE: Record<string, { tone: "ok" | "warn" | "muted"; label: string }> = {
  verified_as_approved: { tone: "ok", label: "Verified identical to the approved version" },
  changed_since_approval: { tone: "warn", label: "Changed since approval" },
  current: { tone: "muted", label: "Current file" },
};

type TimelineEvent = AuditEvent & { stage: string; index: number };

function notesKey(intentId: string, chapter: string) {
  return `aisdlc.notes.${intentId}.${chapter}`;
}

function readNotes(intentId: string, chapter: string): string {
  try {
    return localStorage.getItem(notesKey(intentId, chapter)) ?? "";
  } catch {
    return "";
  }
}

function Enforcement({ item }: { item: StoryEnforcement }) {
  const kind = ENFORCED[item.kind] ?? { icon: "check" as IconName, tone: "info", label: "Recorded" };
  return (
    <li className="record-item">
      <span className={`record-ic ${kind.tone}`}>
        <Icon name={kind.icon} size={16} />
        <span className="sr-only">{kind.label}:</span>
      </span>
      <div>
        <div>{item.text}</div>
        {item.at && <div className="muted record-at">{when(item.at)}</div>}
        {item.reasons && item.reasons.length > 0 && (
          <ul className="reasons">
            {item.reasons.slice(0, 4).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

/** The span a chapter covers, said once and plainly. */
function lead(chapter: StoryChapter): string {
  if (!chapter.reached) return "This Intent has not reached this stage yet.";
  const events = plural(chapter.event_count, "runner event");
  if (!chapter.started_at) return `${events} on the record.`;
  const start = when(chapter.started_at);
  const end = chapter.ended_at ? when(chapter.ended_at) : start;
  return start === end ? `${events} on ${start}.` : `${events} from ${start} to ${end}.`;
}

/** Identifiers a chapter's refusals and reviews name, so the artifact can point at them. */
function namedIds(chapter: StoryChapter): string[] {
  const text = chapter.enforcement.flatMap((item) => [item.text, ...(item.reasons ?? [])]).join(" ");
  return [...new Set(text.match(/\b(?:REQ-(?:F|NFR)|AC|SC|TM|TB|F)-\d{3,}\b/g) ?? [])];
}

function Artifact({ chapter }: { chapter: StoryChapter }) {
  const hit = useRef<HTMLSpanElement>(null);
  const ids = namedIds(chapter);
  const artifact = chapter.artifact!;
  const provenance = PROVENANCE[artifact.provenance] ?? PROVENANCE.current!;
  const lines = artifact.content.split("\n");
  const first = lines.findIndex((line) => ids.some((id) => line.includes(`id: ${id}`)));
  useEffect(() => {
    hit.current?.scrollIntoView?.({ block: "center" });
  }, [chapter.id]);
  return (
    <figure className="artifact">
      <figcaption className="hd">
        <span className="artifact-path">
          <Icon name="file" size={15} />
          <span className="mono">{artifact.path}</span>
        </span>
        <Pill tone={provenance.tone}>
          {provenance.tone === "ok" && <Icon name="check" size={12} />}
          {provenance.label}
        </Pill>
      </figcaption>
      <p className="prov">{artifact.note}{artifact.truncated ? " Shown in part." : ""}</p>
      <pre className="code-view story-code" aria-label={`${artifact.path}${ids.length ? `, lines about ${ids.join(", ")} highlighted` : ""}`}>
        {lines.map((line, index) => {
          const named = ids.some((id) => line.includes(id));
          return (
            <span key={index} className={`code-line${named ? " hit" : ""}`} ref={index === first ? hit : undefined}>
              <span className="ln" aria-hidden="true">{index + 1}</span>
              {line}
              {"\n"}
            </span>
          );
        })}
      </pre>
    </figure>
  );
}

function BuildTable({ tasks }: { tasks: NonNullable<StoryChapter["tasks"]> }) {
  return (
    <figure className="artifact">
      <figcaption className="hd">
        <span className="artifact-path">
          <Icon name="check" size={15} /> Build evidence, {plural(tasks.length, "Task")}
        </span>
        <Pill tone="muted">Runner-captured</Pill>
      </figcaption>
      <p className="prov">Each attempt is pinned to a commit and reviewed by a different model family than the one that built it.</p>
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Task</th>
              <th>Evidence</th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.task}>
                <td className="mono">{task.task}</td>
                <td>
                  {task.passed == null ? <span className="muted">none</span> : task.passed ? <Pill tone="ok">passed</Pill> : <Pill tone="bad">failed</Pill>}{" "}
                  <span className="muted">attempt {task.attempt ?? "?"} at @{task.commit}</span>
                </td>
                <td>
                  {task.decision ? (
                    <>
                      <b>{task.decision}</b> <span className="muted">by {task.reviewer_model}; built by {task.implementer_model}</span>
                    </>
                  ) : (
                    <span className="muted">not reviewed</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function Chapter({ chapter, intentId, present }: { chapter: StoryChapter; intentId: string; present: boolean }) {
  const [notes, setNotes] = useState(() => readNotes(intentId, chapter.id));
  useEffect(() => setNotes(readNotes(intentId, chapter.id)), [intentId, chapter.id]);
  return (
    <div className="slide">
      <section className="claim" aria-labelledby={`claim-${chapter.id}`}>
        <span className="claim-stage">
          {chapter.title}
          {chapter.gate && (
            <span className="claim-gate">
              <Icon name="gate" size={11} /> {chapter.gate}
            </span>
          )}
        </span>
        <h2 id={`claim-${chapter.id}`}>{chapter.claim}</h2>
        <p className="lead">{lead(chapter)}</p>
        {chapter.enforcement.length > 0 ? (
          <div className="record">
            <h3>On the record</h3>
            <ul>
              {chapter.enforcement.map((item, index) => (
                <Enforcement key={`${item.kind}-${index}`} item={item} />
              ))}
            </ul>
          </div>
        ) : (
          chapter.reached && <p className="muted" style={{ margin: 0 }}>No refusals, reviews or gate decisions were recorded in this stage.</p>
        )}
        {!present && (
          <details className="notes" open={notes.length > 0}>
            <summary>Presenter notes</summary>
            <textarea
              id={`notes-${chapter.id}`}
              aria-label="Presenter notes, kept on this computer only"
              placeholder="Kept on this computer only"
              value={notes}
              rows={3}
              onChange={(e) => {
                setNotes(e.target.value);
                try {
                  localStorage.setItem(notesKey(intentId, chapter.id), e.target.value);
                } catch {
                  /* notes are optional */
                }
              }}
            />
          </details>
        )}
      </section>
      {chapter.artifact ? (
        <Artifact chapter={chapter} />
      ) : chapter.tasks && chapter.tasks.length > 0 ? (
        <BuildTable tasks={chapter.tasks} />
      ) : (
        <figure className="artifact">
          <Empty>{chapter.reached ? "Nothing was written in this stage's folder." : "Not reached yet."}</Empty>
        </figure>
      )}
    </div>
  );
}

/**
 * The audit log as one track, cut into the seven chapters. One Tab stop: arrow keys move
 * between events, Home and End jump to the ends, and the caption reads the event aloud.
 */
function Timeline({
  timeline,
  chapters,
  cursor,
  currentChapter,
  onPick,
}: {
  timeline: TimelineEvent[];
  chapters: StoryChapter[];
  cursor: number | null;
  currentChapter: string;
  onPick(index: number): void;
}) {
  const active = cursor ?? timeline.filter((event) => event.stage === currentChapter).at(-1)?.index ?? 0;
  const move = (index: number) => {
    const next = Math.max(0, Math.min(timeline.length - 1, index));
    onPick(next);
    requestAnimationFrame(() => document.getElementById(`evt-${next}`)?.focus());
  };
  return (
    <div
      className="timeline"
      role="group"
      aria-label={`Audit timeline, ${timeline.length} events`}
      onKeyDown={(event) => {
        const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (delta) {
          event.preventDefault();
          event.stopPropagation();
          move(active + delta);
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault();
          move(event.key === "Home" ? 0 : timeline.length - 1);
        }
      }}
    >
      {chapters.map((chapter) => {
        const events = timeline.filter((event) => event.stage === chapter.id);
        return (
          <div key={chapter.id} className={`tl-segment${chapter.id === currentChapter ? " current" : ""}${chapter.reached ? "" : " future"}`} style={{ flexGrow: Math.max(1, events.length) }}>
            <div className="tl-marks">
              {events.map((event) => {
                const gate = event.entity_type === "gate" && event.to === "approved";
                const bad = event.event === "advance_refused" || event.to === "retry" || event.to === "failed" || event.to === "blocked";
                return (
                  <button
                    key={event.index}
                    id={`evt-${event.index}`}
                    type="button"
                    tabIndex={event.index === active ? 0 : -1}
                    className={`tl-mark${gate ? " gate" : ""}${bad ? " bad" : ""}${event.index <= active ? " past" : ""}${event.index === cursor ? " cur" : ""}`}
                    aria-label={`Event ${event.index + 1} of ${timeline.length}: ${describeEvent(event)}`}
                    title={describeEvent(event)}
                    onClick={() => onPick(event.index)}
                  />
                );
              })}
            </div>
            <span className="tl-label">{chapter.title}</span>
          </div>
        );
      })}
    </div>
  );
}

export function StoryView({ intentId: requested }: { intentId?: string }) {
  const navigate = useStudio((s) => s.navigate);
  const portfolio = useProjection<Portfolio>("project.snapshot");
  const intents = portfolio.data?.intents ?? [];
  const fallback = intents.find((i) => i.status === "completed")?.id ?? intents[0]?.id;
  const intentId = requested ?? fallback;
  const story = useProjection<StoryPayload>("story.inspect", { intent_id: intentId ?? "" }, !!intentId);
  const [index, setIndex] = useState(0);
  const [cursor, setCursor] = useState<number | null>(null);
  const [present, setPresent] = useState(false);

  const chapters = story.data?.chapters ?? [];
  const reachedCount = chapters.filter((c) => c.reached).length;

  useEffect(() => {
    setIndex(0);
    setCursor(null);
  }, [intentId]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT" || target.tagName === "SELECT")) return;
      if (target?.closest(".timeline")) return;
      if (event.key === "ArrowRight") {
        setIndex((i) => Math.min(i + 1, Math.max(0, reachedCount - 1)));
        setCursor(null);
      }
      if (event.key === "ArrowLeft") {
        setIndex((i) => Math.max(i - 1, 0));
        setCursor(null);
      }
      if (event.key === "Escape") setPresent(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reachedCount]);

  const timeline = (story.data?.timeline ?? []) as TimelineEvent[];
  const chapterIndex = useMemo(() => new Map(chapters.map((c, i) => [c.id, i])), [chapters]);
  const current = chapters[index];
  const currentEvent = cursor != null ? timeline[cursor] : null;
  const next = chapters[index + 1];

  if (portfolio.loading && !portfolio.data) return <div className="page"><Loading height={420} /></div>;
  if (!intentId) return <div className="page"><Empty>No Intents to present yet. Start one, and its story fills in as the runner records it.</Empty></div>;
  if (story.error) return <div className="page"><ErrorBox message={story.error} retry={story.reload} /></div>;
  if (!story.data || !current) return <div className="page"><Loading height={420} /></div>;

  return (
    <div className={`story ${present ? "present" : ""}`}>
      <header className="story-top">
        <div className="story-title">
          <Icon name="story" size={18} />
          <h1>
            <span className="mono">{story.data.intent.id}</span> {story.data.intent.title}
          </h1>
          {story.data.complete ? <Pill tone="ok">Completed</Pill> : <Pill tone="info">In progress</Pill>}
        </div>
        <span style={{ flex: 1 }} />
        {!present && (
          <label className="story-pick">
            Replay
            <select id="story-intent" value={intentId} onChange={(e) => navigate({ view: "story", intentId: e.target.value })}>
              {intents.map((intent) => (
                <option key={intent.id} value={intent.id}>
                  {intent.id} {intent.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" className="btn" onClick={() => setPresent((p) => !p)}>
          {present ? "Stop presenting (Esc)" : "Present full screen"}
        </button>
      </header>

      <nav className="chapters" aria-label="Chapters">
        <ol>
          {chapters.map((chapter, i) => (
            <li key={chapter.id}>
              <button
                type="button"
                className={`${chapter.reached ? "reached" : ""}${i < index ? " past" : ""}`}
                aria-current={i === index ? "step" : undefined}
                aria-label={`${chapter.title}${chapter.gate ? `, gate ${chapter.gate}` : ""}${chapter.reached ? `, ${plural(chapter.event_count, "event")}` : ", not reached"}`}
                disabled={!chapter.reached}
                onClick={() => {
                  setIndex(i);
                  setCursor(null);
                }}
              >
                <span className="step-dot">{i < index ? <Icon name="check" size={12} /> : i + 1}</span>
                <span className="step-text">
                  <b>{chapter.title}</b>
                  <span>
                    {chapter.gate && <><Icon name="gate" size={9} className="gate-ic" /> {chapter.gate} </>}
                    {chapter.reached ? plural(chapter.event_count, "event") : "not reached"}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      </nav>

      <Chapter chapter={current} intentId={story.data.intent.id} present={present} />

      <footer className="scrub">
        <button type="button" className="btn" onClick={() => { setIndex((i) => Math.max(0, i - 1)); setCursor(null); }} disabled={index === 0}>
          Previous
        </button>
        <div className="scrub-track">
          <Timeline
            timeline={timeline}
            chapters={chapters}
            cursor={cursor}
            currentChapter={current.id}
            onPick={(i) => {
              setCursor(i);
              const target = chapterIndex.get(timeline[i]!.stage as StoryChapter["id"]);
              if (target != null) setIndex(target);
            }}
          />
          <div className="event-caption" aria-live="polite">
            {currentEvent ? `${when(currentEvent.at)}: ${describeEvent(currentEvent)}` : "Pick an event on the track, or use the arrow keys to move between chapters."}
          </div>
        </div>
        <button
          type="button"
          className="btn primary"
          onClick={() => { setIndex((i) => Math.min(reachedCount - 1, i + 1)); setCursor(null); }}
          disabled={index >= reachedCount - 1}
        >
          {next && index < reachedCount - 1 ? `Next: ${next.title}` : "Next"}
        </button>
      </footer>
    </div>
  );
}
