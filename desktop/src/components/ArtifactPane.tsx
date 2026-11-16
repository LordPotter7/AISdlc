import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ArtifactCheck, ArtifactPayload, Diagnostic, SpecIdentifier } from "@/lib/contract";
import { plural } from "@/lib/format";
import { useStudio, type ViewerTarget } from "@/state/store";
import { useProjection } from "@/state/useProjection";
import { CodeEditor, type EditorLanguage } from "./CodeEditor";
import { Icon } from "./Icon";
import { MarkdownView } from "./MarkdownView";
import { OperationDialog, type OperationRequest } from "./OperationDialog";
import { ErrorBox, Loading, Pill } from "./ui";

const OWNER_NOTE: Record<ArtifactPayload["owner"], string> = {
  you: "You write this file, here or in your editor or through Claude Code.",
  runner: "The runner writes this file. It is shown here and never edited by hand.",
  framework: "A framework template or schema. Copy it into your Intent; never edit the original.",
};

type Mode = "source" | "split" | "preview";

function languageOf(path: string): EditorLanguage {
  if (/\.ya?ml$/i.test(path)) return "yaml";
  if (/\.md$/i.test(path)) return "markdown";
  if (/\.json$/i.test(path)) return "json";
  return "text";
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

interface OutlineItem {
  line: number;
  depth: number;
  text: string;
  id?: string;
}

/** Headings for Markdown (outside code fences); top-level keys and identified records for YAML. */
function outlineOf(text: string, language: EditorLanguage, identifiers: SpecIdentifier[], path: string): OutlineItem[] {
  const lines = text.split("\n");
  if (language === "markdown") {
    const items: OutlineItem[] = [];
    let fenced = false;
    lines.forEach((line, index) => {
      if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
      const heading = !fenced && line.match(/^(#{1,4})\s+(.+?)\s*#*\s*$/);
      if (heading) items.push({ line: index + 1, depth: heading[1]!.length, text: heading[2]! });
    });
    return items;
  }
  if (language !== "yaml") return [];
  const items: OutlineItem[] = [];
  lines.forEach((line, index) => {
    const key = line.match(/^([A-Za-z_][\w-]*):/);
    if (key) items.push({ line: index + 1, depth: 1, text: key[1]! });
  });
  for (const item of identifiers) {
    if (item.file === path) items.push({ line: item.line, depth: 2, text: item.label, id: item.id });
  }
  return items.sort((a, b) => a.line - b.line || a.depth - b.depth);
}

function Problems({
  check,
  checking,
  path,
  onReveal,
  onOpen,
}: {
  check: ArtifactCheck | null;
  checking: boolean;
  path: string;
  onReveal(line: number): void;
  onOpen(path: string, line: number | null): void;
}) {
  const all = check?.diagnostics ?? [];
  const here = all.filter((item) => item.file === path);
  const elsewhere = all.filter((item) => item.file !== path);
  const count = (severity: Diagnostic["severity"]) => here.filter((item) => item.severity === severity).length;
  const row = (item: Diagnostic, index: number) => {
    const local = item.file === path;
    return (
      <li key={`${item.message}-${index}`}>
        <button
          type="button"
          className={`problem ${item.severity}`}
          onClick={() => (local ? item.line && onReveal(item.line) : item.file && onOpen(item.file, item.line))}
          disabled={local ? !item.line : !item.file}
        >
          <span className={`sev ${item.severity}`} aria-hidden="true">
            <Icon name={item.severity === "info" ? "check" : "alert"} size={13} />
          </span>
          <span className="sr-only">{item.severity}:</span>
          <span className="where mono">{local ? (item.line ? `Ln ${item.line}` : "whole file") : (item.file ?? "Intent")}</span>
          <span className="msg">{item.message}</span>
          {item.code && <span className="code mono">{item.code}</span>}
        </button>
      </li>
    );
  };
  return (
    <section className="problems" aria-label="Problems">
      <header>
        <b>Problems</b>
        {check ? (
          here.length === 0 ? (
            <span className="ok-text"><Icon name="check" size={13} /> None in this file</span>
          ) : (
            <span className="muted">
              {plural(count("error"), "error")}, {plural(count("warning"), "warning")}
              {count("info") ? `, ${count("info")} info` : ""}
            </span>
          )
        ) : null}
        <span className="muted problems-state" aria-live="polite">{checking ? "Checking with the runner…" : check ? "Checked by the runner" : ""}</span>
      </header>
      {here.length > 0 && <ul aria-label="In this file">{here.map(row)}</ul>}
      {elsewhere.length > 0 && (
        <details className="elsewhere">
          <summary>{plural(elsewhere.length, "more problem")} elsewhere in this Intent</summary>
          <ul>{elsewhere.map(row)}</ul>
        </details>
      )}
    </section>
  );
}

/**
 * One file in the workbench: read it, edit it where the runner allows, see the runner's
 * checks as you type, and save through the runner. Markdown can be read rendered, edited
 * as source, or both side by side. Used by the file drawer and by an Intent's Files tab.
 */
export function ArtifactPane({
  target,
  layout,
  onClose,
  onOpen,
  onDirty,
  onBusy,
}: {
  target: ViewerTarget;
  layout: "drawer" | "page";
  onClose?(): void;
  /** Open another file of the same Intent (from a problem, a link, or an identifier). */
  onOpen(path: string, line: number | null): void;
  onDirty?(dirty: boolean): void;
  onBusy?(busy: boolean): void;
}) {
  const refresh = useStudio((s) => s.refresh);
  const toast = useStudio((s) => s.toast);
  const creating = !!target.create;
  const file = useProjection<ArtifactPayload>(
    "artifact.read",
    { scope: target.scope, intent_id: target.intentId, path: target.path },
    !creating,
  );
  const template = useProjection<ArtifactPayload>("artifact.read", { scope: "template", path: target.create?.template ?? "" }, creating);
  const data = file.data;
  const language = languageOf(target.path);
  const [draft, setDraft] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>(language === "markdown" ? "preview" : "source");
  const [reveal, setReveal] = useState<{ line: number; nonce: number } | null>(null);
  const [cursor, setCursor] = useState(1);
  const [operation, setOperation] = useState<OperationRequest | null>(null);
  const headings = useRef<HTMLElement[]>([]);
  const editing = draft !== null;
  const dirty = editing && (creating || draft !== data?.text);

  useEffect(() => {
    setDraft(null);
    setOperation(null);
    setMode(languageOf(target.path) === "markdown" ? "preview" : "source");
  }, [target.path, target.intentId, target.scope]);
  useEffect(() => onDirty?.(dirty), [dirty, onDirty]);
  useEffect(() => onBusy?.(!!operation), [operation, onBusy]);
  useEffect(() => {
    if (creating && template.data && draft === null) {
      setDraft(template.data.text);
      setMode("source");
    }
  }, [creating, template.data, draft]);
  useEffect(() => {
    if (data && target.line) setReveal({ line: target.line, nonce: Date.now() });
  }, [data, target.line]);

  // The runner checks what is on screen: the draft while editing, the file while reading.
  const checkable = target.scope === "intent" && !!target.intentId && (creating || data?.owner === "you");
  const content = useDebounced(draft ?? data?.text ?? null, 450);
  const check = useProjection<ArtifactCheck>(
    "artifact.check",
    { intent_id: target.intentId, path: target.path, content },
    checkable && content !== null,
  );
  const identifiers = check.data?.identifiers ?? [];
  const onLine = useMemo(() => (check.data?.diagnostics ?? []).filter((d) => d.file === target.path), [check.data, target.path]);
  const shown = draft ?? data?.text ?? "";
  const outline = useMemo(() => outlineOf(shown, language, identifiers, target.path), [shown, language, identifiers, target.path]);

  const revealLine = useCallback(
    (line: number) => {
      if (mode === "preview") setMode(editing ? "split" : "source");
      setReveal({ line, nonce: Date.now() });
    },
    [mode, editing],
  );
  const openId = (item: SpecIdentifier) => (item.file === target.path ? revealLine(item.line) : open(item.file, item.line));
  const open = (path: string, line: number | null) => {
    if (dirty) {
      toast({ tone: "info", title: "Save or discard your changes first", detail: `${target.path} has changes that are not saved.` });
      return;
    }
    onOpen(path, line);
  };

  const save = () => {
    if (!target.intentId || draft === null || !dirty) return;
    setOperation({
      name: "artifact_write",
      title: `${creating ? "Create" : "Save"} ${target.path}`,
      args: { intent_id: target.intentId, path: target.path, content: draft, expected_sha: creating ? "new" : data?.sha256 },
      explanation:
        "The runner writes this file and records it in the audit log. Below is exactly what changes and what the runner's " +
        "checks say about the new content.",
    });
  };
  const reopen = () => {
    if (!target.intentId || !data?.locked) return;
    setOperation({
      name: "gate_reopen",
      title: `Reopen ${data.locked.gate} for ${target.intentId}`,
      args: { intent_id: target.intentId, gate: data.locked.gate },
      identityField: "actor",
      reasonField: true,
      explanation:
        `Reopening ${data.locked.gate} unlocks the specification for change. It is recorded with your name and reason; ` +
        "evidence that depends on changed requirements stops counting until the gate is approved again, after a fresh " +
        "red-team review.",
    });
  };
  const startEditing = () => {
    if (!data) return;
    setDraft(data.text);
    if (language === "markdown") setMode(layout === "page" ? "split" : "source");
  };

  const loading = creating ? !template.data && !template.error : file.loading && !data;
  const error = creating ? template.error : file.error;
  const note = editing
    ? creating
      ? `Starting from ${target.create!.template}. Replace every {{PLACEHOLDER}}; the runner checks the result as you type and again before it writes.`
      : "Editing a copy. The runner checks it as you type; nothing changes until you review the diff and save."
    : data?.locked
      ? null
      : data?.produced_by
        ? `${OWNER_NOTE.runner} It is ${data.produced_by}.`
        : data && !data.editable && data.edit_refusal && data.owner === "you"
          ? data.edit_refusal
          : data
            ? OWNER_NOTE[data.owner]
            : null;

  return (
    <div className={`pane pane-${layout}`}>
      <header className="pane-head">
        <div className="pane-title">
          <span className="pane-kicker">
            {target.scope === "intent"
              ? `${target.intentId}, ${editing ? (creating ? "new file" : "editing") : data?.editable ? "you author this" : "read-only"}`
              : `Framework ${target.scope}, read-only`}
          </span>
          <h2 id={layout === "drawer" ? "viewer-title" : undefined} className="mono">
            {target.path}
            {target.line && !editing ? <span className="muted">:{target.line}</span> : null}
          </h2>
        </div>
        <div className="pane-actions">
          {language === "markdown" && (
            <span className="seg" role="group" aria-label="Show">
              {(["source", "split", "preview"] as Mode[]).map((value) => (
                <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)}>
                  {value === "source" ? "Source" : value === "split" ? "Split" : "Preview"}
                </button>
              ))}
            </span>
          )}
          {!editing && data?.editable && (
            <button type="button" className="btn sm primary" onClick={startEditing}>
              Edit
            </button>
          )}
          {!editing && data?.locked && (
            <button type="button" className="btn sm gate" onClick={reopen}>
              Propose a change…
            </button>
          )}
          {editing && (
            <>
              <button type="button" className="btn sm" onClick={() => (creating ? onClose?.() : setDraft(null))}>
                Discard
              </button>
              <button type="button" className="btn sm primary" onClick={save} disabled={!dirty} title="Ctrl+S">
                Review and save
              </button>
            </>
          )}
          {onClose && (
            <button type="button" className="btn sm" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </header>

      {data && !editing && (
        <div className="drawer-meta">
          <Pill tone={data.owner === "runner" ? "info" : data.owner === "framework" ? "muted" : "ok"}>
            {data.owner === "you" ? "you write this" : data.owner === "runner" ? "runner-owned" : "framework"}
          </Pill>
          {data.locked && (
            <Pill tone="gate">
              <Icon name="lock" size={12} /> locked by {data.locked.gate}
            </Pill>
          )}
          {data.truncated && <Pill tone="warn">first 256 KiB shown</Pill>}
          <span className="muted">
            {plural(data.lines, "line")}, {data.bytes.toLocaleString()} bytes, sha256 {data.sha256.slice(0, 12)}
          </span>
        </div>
      )}
      {data?.locked && !editing ? (
        <div className="drawer-note">
          Approved at {data.locked.gate}
          {data.locked.approver ? ` by ${data.locked.approver}` : ""}. Work is being built against it, so it is locked. “Propose a
          change” reopens the gate on the record: <span className="mono">{data.locked.command}</span>
        </div>
      ) : note ? (
        <div className="drawer-note">{note}</div>
      ) : null}

      {loading ? (
        <div className="panel-pad"><Loading height={300} /></div>
      ) : error ? (
        <div className="panel-pad"><ErrorBox message={error} retry={creating ? template.reload : file.reload} /></div>
      ) : (
        <div className={`pane-body ${checkable ? "with-problems" : ""}`}>
          {layout === "page" && outline.length > 0 && (
            <nav className="outline" aria-label="Outline">
              <b>Outline</b>
              <ul>
                {outline.map((item) => (
                  <li key={`${item.line}-${item.text}`} style={{ paddingLeft: (item.depth - 1) * 12 }}>
                    <button
                      type="button"
                      className={item.line <= cursor && (outline.find((next) => next.line > item.line)?.line ?? Infinity) > cursor ? "here" : ""}
                      onClick={() => {
                        if (mode === "preview" && language === "markdown") {
                          const index = outline.filter((o) => o.depth <= 4).indexOf(item);
                          headings.current[index]?.scrollIntoView?.({ block: "start" });
                        } else revealLine(item.line);
                      }}
                    >
                      {item.id ? <span className="mono">{item.id}</span> : null} {item.id ? item.text.slice(0, 48) : item.text}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          )}
          <div className={`pane-views mode-${language === "markdown" ? mode : "source"}`}>
            {(language !== "markdown" || mode !== "preview") && (
              <CodeEditor
                value={shown}
                language={language}
                label={editing ? `Edit ${target.path}` : `${target.path}, read-only`}
                readOnly={!editing}
                onChange={editing ? setDraft : undefined}
                diagnostics={onLine}
                identifiers={identifiers}
                keys={check.data?.keys ?? []}
                suggestions={check.data?.suggestions}
                reveal={reveal}
                onOpenId={openId}
                onCursorLine={setCursor}
                onSave={save}
              />
            )}
            {language === "markdown" && mode !== "source" && (
              <div className="md-scroll">
                <MarkdownView
                  text={shown}
                  path={target.path}
                  identifiers={identifiers}
                  onOpenId={openId}
                  onOpenPath={(path) => open(path, null)}
                  headingRef={(found) => (headings.current = found)}
                />
              </div>
            )}
          </div>
          {checkable && (
            <Problems check={check.data} checking={check.loading} path={target.path} onReveal={revealLine} onOpen={open} />
          )}
          <footer className="pane-status">
            <span>{language === "text" ? "Plain text" : language.toUpperCase()}</span>
            {(language !== "markdown" || mode !== "preview") && <span>Ln {cursor}</span>}
            {editing && <span>{dirty ? "Unsaved changes" : "No changes yet"}</span>}
            <span className="muted">Ctrl+Space completes IDs and keys, Ctrl+click opens an ID, Ctrl+F finds</span>
          </footer>
        </div>
      )}

      {operation && (
        <OperationDialog
          request={operation}
          onClose={(changed) => {
            setOperation(null);
            if (!changed) return;
            if (creating) {
              onOpen(target.path, null);
            } else {
              setDraft(null);
              file.reload();
            }
            void refresh();
          }}
        />
      )}
    </div>
  );
}
