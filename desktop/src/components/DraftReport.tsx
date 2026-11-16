import type { DraftReport as Draft, LintFinding } from "@/lib/contract";
import { Pill } from "./ui";

const TONE = { error: "bad", warning: "warn", info: "muted" } as const;

/** A unified diff, one line per row, added and removed lines marked by sign and colour. */
export function DiffView({ diff }: { diff: string }) {
  const lines = diff.split("\n").filter((line, index, all) => !(index === all.length - 1 && line === ""));
  return (
    <pre className="diff-view" aria-label="Changes">
      {lines.map((line, index) => {
        const kind = line.startsWith("+++") || line.startsWith("---") ? "file"
          : line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
        return (
          <span key={index} className={`diff-line ${kind}`}>
            {line}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

function Finding({ finding }: { finding: LintFinding }) {
  return (
    <li>
      <Pill tone={TONE[finding.severity] ?? "muted"}>{finding.severity}</Pill>{" "}
      <span className="mono">{finding.rule} {finding.location}</span> {finding.message}
      {finding.source?.line ? <span className="muted mono"> · line {finding.source.line}</span> : null}
    </li>
  );
}

/**
 * What saving would do, before it is done: the diff against the file on disk, and the
 * runner's own lint and validation run over the proposed content. Studio adds no rules of
 * its own here, so what this says is exactly what `artifact write` will report.
 */
export function DraftReportView({ draft }: { draft: Draft }) {
  const lint = draft.checks?.lint;
  const errors = draft.checks?.validation_errors ?? [];
  return (
    <div className="draft">
      {draft.refusal && (
        <div className="error-box" role="alert">
          The runner will refuse this: {draft.refusal}
        </div>
      )}
      <div>
        <span className="eyebrow">What changes</span>
        {draft.changed ? <DiffView diff={draft.diff} /> : <p className="muted">Nothing: the content is identical to the file.</p>}
      </div>
      {draft.checks && (
        <div>
          <span className="eyebrow">What the runner's checks say about the new content</span>
          {lint ? (
            <p style={{ margin: "4px 0" }}>
              <Pill tone={lint.passed ? "ok" : "bad"}>{lint.passed ? "spec lint passes" : "spec lint blocks G2"}</Pill>{" "}
              <span className="muted">
                {lint.counts.error ?? 0} errors · {lint.counts.warning ?? 0} warnings · {lint.counts.info ?? 0} notes
              </span>
            </p>
          ) : null}
          {(lint?.findings.length ?? 0) > 0 && (
            <ul className="draft-findings">
              {lint!.findings.map((finding, index) => (
                <Finding key={`${finding.rule}-${finding.location}-${index}`} finding={finding} />
              ))}
            </ul>
          )}
          {errors.length > 0 && (
            <>
              <span className="muted" style={{ fontSize: 12.5 }}>
                Validation (informational until you advance the Intent):
              </span>
              <ul className="draft-findings">
                {errors.map((message) => (
                  <li key={message}>
                    <span className="dot warn" /> {message}
                  </li>
                ))}
              </ul>
            </>
          )}
          {!lint && errors.length === 0 && <p className="muted" style={{ margin: "4px 0" }}>No findings.</p>}
        </div>
      )}
    </div>
  );
}
