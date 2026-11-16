import { useEffect, useRef } from "react";
import { autocompletion, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";
import { syntaxHighlighting } from "@codemirror/language";
import { lintGutter, setDiagnostics, type Diagnostic as EditorDiagnostic } from "@codemirror/lint";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, hoverTooltip, keymap } from "@codemirror/view";
import { classHighlighter } from "@lezer/highlight";
import { basicSetup } from "codemirror";
import type { ArtifactCheck, Diagnostic, SchemaKey, SpecIdentifier } from "@/lib/contract";

export type EditorLanguage = "yaml" | "markdown" | "json" | "text";

/** Identifiers the runner allocates, as the editor recognises them in any file. */
const ID_AT = /(?:REQ-(?:F|NFR)|AC|SC|TM|TB|TH|F)-\d{3,}/g;
const ID_TYPED = /\b(?:REQ|AC|SC|TM|TB|TH)[-\w]*$/;

interface Props {
  value: string;
  language: EditorLanguage;
  label: string;
  readOnly?: boolean;
  onChange?(value: string): void;
  /** Problems on lines of this file; others are listed beside the editor, not drawn in it. */
  diagnostics?: Diagnostic[];
  identifiers?: SpecIdentifier[];
  keys?: SchemaKey[];
  /** Values for list keys, such as a Task's `affected_paths` from the codebase's modules. */
  suggestions?: ArtifactCheck["suggestions"];
  /** Move the cursor to this line and centre it. Changes to the same number re-reveal it. */
  reveal?: { line: number; nonce: number } | null;
  onOpenId?(identifier: SpecIdentifier): void;
  onCursorLine?(line: number): void;
  onSave?(): void;
}

function languageOf(language: EditorLanguage): Extension {
  // Prose wraps; YAML keeps one record per line so indentation stays readable.
  return language === "yaml" ? yaml() : language === "markdown" ? [markdown(), EditorView.lineWrapping] : [];
}

/**
 * CodeMirror 6 with the runner's knowledge wired in: its lint and validation drawn on the
 * lines they concern, completion for the identifiers the specification defines and the keys
 * the file's schema allows, and hover or Ctrl+click on an identifier to see or open it.
 * The editor is created once; later prop changes reach it as transactions, not re-renders.
 */
export function CodeEditor(props: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const editable = useRef(new Compartment());
  const language = useRef(new Compartment());
  const labelled = useRef(new Compartment());

  useEffect(() => {
    const complete = (context: CompletionContext): CompletionResult | null => {
      const { identifiers = [], keys = [], language: lang } = latest.current;
      const line = context.state.doc.lineAt(context.pos);
      const before = line.text.slice(0, context.pos - line.from);
      const typedId = context.matchBefore(ID_TYPED);
      if (typedId && identifiers.length) {
        return {
          from: typedId.from,
          options: identifiers.map((item) => ({ label: item.id, detail: item.label.slice(0, 70), type: "variable" })),
          validFor: /^[-\w]*$/,
        };
      }
      if (lang !== "yaml") return null;
      // A list item under a key the codebase can answer: modules for paths, test suites for checks.
      const item = before.match(/^(\s*)-\s+["']?([^"'#]*)$/);
      if (item) {
        const indent = item[1]!.length;
        let parent: string | null = null;
        for (let number = line.number - 1; number >= 1 && parent === null; number--) {
          const text = context.state.doc.line(number).text;
          const owner = text.match(/^(\s*)(?:-\s+)?([\w]+):(.*)$/);
          // The nearest key at or left of the item owns it; a key with a value on its line owns no list.
          if (owner && owner[1]!.length <= indent) parent = owner[3]!.trim() ? "" : owner[2]!;
          else if (text.trim() && text.search(/\S/) < indent) break;
        }
        const values = parent ? (latest.current.suggestions as Record<string, string[] | undefined> | undefined)?.[parent] : undefined;
        if (values?.length) {
          return {
            from: context.pos - item[2]!.length,
            options: values.map((value) => ({
              label: value,
              type: parent === "verification_commands" ? "function" : "file",
              detail: parent === "verification_commands" ? "test suite in this repo" : "module",
            })),
            validFor: /^[^"'#]*$/,
          };
        }
      }
      if (keys.length === 0) return null;
      const pair = before.match(/^\s*(?:-\s+)?([\w]+):\s*([-\w]*)$/);
      const known = pair && keys.find((key) => key.key === pair[1]);
      if (pair && known && known.values.length) {
        return {
          from: context.pos - pair[2]!.length,
          options: known.values.map((value) => ({ label: value, type: "enum", info: known.description || undefined })),
        };
      }
      const key = before.match(/^\s*(?:-\s+)?(\w*)$/);
      if (key && (key[1] || context.explicit)) {
        return {
          from: context.pos - key[1]!.length,
          options: keys.map((item) => ({
            label: item.key,
            apply: `${item.key}: `,
            type: "property",
            detail: item.values.length ? item.values.join(" | ") : undefined,
            info: item.description || undefined,
          })),
          validFor: /^\w*$/,
        };
      }
      return null;
    };

    const idAt = (state: EditorState, pos: number): { id: string; from: number; to: number } | null => {
      const line = state.doc.lineAt(pos);
      for (const match of line.text.matchAll(ID_AT)) {
        const from = line.from + match.index!;
        const to = from + match[0].length;
        if (pos >= from && pos <= to) return { id: match[0], from, to };
      }
      return null;
    };

    const hover = hoverTooltip((editor, pos) => {
      const found = idAt(editor.state, pos);
      const known = found && latest.current.identifiers?.find((item) => item.id === found.id);
      if (!found || !known) return null;
      return {
        pos: found.from,
        end: found.to,
        above: true,
        create: () => {
          const dom = document.createElement("div");
          dom.className = "cm-id-tip";
          const title = document.createElement("b");
          title.textContent = known.id;
          const where = document.createElement("span");
          where.textContent = ` ${known.file}:${known.line}`;
          const text = document.createElement("p");
          text.textContent = known.label || "No statement written yet.";
          const hint = document.createElement("small");
          hint.textContent = "Ctrl+click to open where it is written";
          dom.append(title, where, text, hint);
          return { dom };
        },
      };
    });

    const state = EditorState.create({
      doc: props.value,
      extensions: [
        basicSetup,
        syntaxHighlighting(classHighlighter),
        language.current.of(languageOf(props.language)),
        editable.current.of(EditorState.readOnly.of(!!props.readOnly)),
        EditorState.tabSize.of(2),
        autocompletion({ override: [complete], activateOnTyping: true }),
        lintGutter(),
        hover,
        labelled.current.of(EditorView.contentAttributes.of({ "aria-label": props.label })),
        keymap.of([
          {
            key: "Mod-s",
            preventDefault: true,
            run: () => {
              latest.current.onSave?.();
              return true;
            },
          },
        ]),
        EditorView.domEventHandlers({
          mousedown(event, editor) {
            if (!(event.ctrlKey || event.metaKey)) return false;
            const pos = editor.posAtCoords({ x: event.clientX, y: event.clientY });
            const found = pos == null ? null : idAt(editor.state, pos);
            const known = found && latest.current.identifiers?.find((item) => item.id === found.id);
            if (!known) return false;
            event.preventDefault();
            latest.current.onOpenId?.(known);
            return true;
          },
        }),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) latest.current.onChange?.(update.state.doc.toString());
          if (update.selectionSet || update.docChanged) {
            latest.current.onCursorLine?.(update.state.doc.lineAt(update.state.selection.main.head).number);
          }
        }),
      ],
    });
    view.current = new EditorView({ state, parent: host.current! });
    return () => {
      view.current?.destroy();
      view.current = null;
    };
    // The editor is built once; everything that changes later is applied below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A value from outside (a reload, a discard) replaces the document; typing never loops back.
  useEffect(() => {
    const editor = view.current;
    if (!editor || editor.state.doc.toString() === props.value) return;
    editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: props.value } });
  }, [props.value]);

  useEffect(() => {
    view.current?.dispatch({ effects: editable.current.reconfigure(EditorState.readOnly.of(!!props.readOnly)) });
  }, [props.readOnly]);

  useEffect(() => {
    view.current?.dispatch({ effects: language.current.reconfigure(languageOf(props.language)) });
  }, [props.language]);

  useEffect(() => {
    view.current?.dispatch({ effects: labelled.current.reconfigure(EditorView.contentAttributes.of({ "aria-label": props.label })) });
  }, [props.label]);

  useEffect(() => {
    const editor = view.current;
    if (!editor) return;
    const doc = editor.state.doc;
    const drawn: EditorDiagnostic[] = (props.diagnostics ?? [])
      .filter((item) => item.line != null && item.line >= 1)
      .map((item) => {
        const line = doc.line(Math.min(item.line!, doc.lines));
        const from = item.column ? Math.min(line.from + item.column - 1, line.to) : line.from;
        return {
          from,
          to: Math.max(from, line.to),
          severity: item.severity,
          source: item.code ? `${item.source} ${item.code}` : item.source,
          message: item.message,
        };
      });
    editor.dispatch(setDiagnostics(editor.state, drawn));
  }, [props.diagnostics, props.value]);

  useEffect(() => {
    const editor = view.current;
    if (!editor || !props.reveal) return;
    const line = editor.state.doc.line(Math.max(1, Math.min(props.reveal.line, editor.state.doc.lines)));
    editor.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: "center" }) });
  }, [props.reveal]);

  return <div className="cm-host" ref={host} />;
}
