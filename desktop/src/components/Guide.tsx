import { createContext, useContext, useId, type ReactNode } from "react";
import type { LearnPayload, LintRule, SourceRef } from "@/lib/contract";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

/**
 * The framework explained in its own words (`.ai-sdlc/framework-guide.yaml`), fetched once
 * per project generation and shared: every jargon word in the app can explain itself
 * without each view asking the bridge again.
 */
const GuideContext = createContext<LearnPayload | null>(null);

export function GuideProvider({ children }: { children: ReactNode }) {
  const project = useStudio((s) => s.project);
  const { data } = useProjection<LearnPayload>("learn.inspect", {}, !!project);
  return <GuideContext.Provider value={data}>{children}</GuideContext.Provider>;
}

export function useGuide(): LearnPayload | null {
  return useContext(GuideContext);
}

export function useLintRule(code: string): LintRule | undefined {
  return useGuide()?.lint_rules.find((rule) => rule.code === code);
}

/**
 * A word with its definition one hover or one Tab away. Keyboard users get the same
 * text as pointer users: the definition is a real tooltip tied by `aria-describedby`.
 */
export function Term({ id, children }: { id: string; children?: ReactNode }) {
  const guide = useGuide();
  const tip = useId();
  const term = guide?.terms.find((item) => item.id === id);
  if (!term) return <>{children}</>;
  return (
    <span className="term" tabIndex={0} aria-describedby={tip}>
      {children ?? term.term}
      <span role="tooltip" id={tip} className="term-tip">
        <b>{term.term}</b> {term.definition}
      </span>
    </span>
  );
}

/** `requirements.yaml:21`: opens the read-only viewer on that line. */
export function SourceLink({ intentId, source }: { intentId: string; source: SourceRef | null | undefined }) {
  const openFile = useStudio((s) => s.openFile);
  if (!source) return null;
  const name = source.file.split("/").pop();
  const label = source.line ? `${name}:${source.line}` : name;
  return (
    <button
      type="button"
      className="source-link"
      title={`Open ${source.file}${source.line ? ` at line ${source.line}` : ""}`}
      onClick={() => openFile({ scope: "intent", intentId, path: source.file, line: source.line })}
    >
      {label}
    </button>
  );
}
