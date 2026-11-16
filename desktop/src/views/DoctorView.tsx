import type { DiagnosticsPayload } from "@/lib/contract";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";
import { ErrorBox, Loading, Panel, Pill } from "@/components/ui";

const TONE: Record<string, "ok" | "warn" | "bad" | "muted"> = { ok: "ok", warn: "warn", fail: "bad" };

export function DoctorView() {
  const bridge = useStudio((s) => s.bridge);
  const { data, error, loading, reload } = useProjection<DiagnosticsPayload>("diagnostics.inspect");
  if (loading && !data) return <div className="page"><Loading height={320} /></div>;
  if (error) return <div className="page"><ErrorBox message={error} retry={reload} /></div>;
  if (!data) return null;
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Doctor</h1>
          <span className="sub">The same checks as <span className="mono">ai-sdlc doctor</span>, for the open repository.</span>
        </div>
        <div className="actions">
          <button type="button" className="btn" onClick={reload}>Run again</button>
        </div>
      </header>
      <div className="grid-3">
        <Panel title="Bridge" pad>
          <div className="list">
            <div className="item"><Pill tone={bridge.state === "current" ? "ok" : "warn"}>{bridge.state}</Pill> {bridge.detail}</div>
            <div className="item">Python {data.python.version} <span className="mono muted">{bridge.python_source ?? ""}</span></div>
            {bridge.repo && <div className="item mono muted">{bridge.repo}</div>}
          </div>
        </Panel>
        <Panel title="Runner" pad>
          <div className="list">
            <div className="item">Version <span className="mono">{data.context.runner.version}</span></div>
            <div className="item">
              {data.version_consistency.length === 0 ? <Pill tone="ok">declared versions agree</Pill> : <Pill tone="warn">{data.version_consistency.join("; ")}</Pill>}
            </div>
          </div>
        </Panel>
        <Panel title="Repository" pad>
          <div className="list">
            <div className="item mono" style={{ wordBreak: "break-all" }}>{data.context.root}</div>
            <div className="item">Branch <span className="mono">{data.context.git.branch ?? "—"}</span> at <span className="mono">{data.context.git.head ?? "—"}</span></div>
            {data.context.problems.map((problem) => (
              <div className="item" key={problem}><span className="dot bad" /> {problem}</div>
            ))}
          </div>
        </Panel>
      </div>
      <Panel title="Checks" aside={<Pill tone={TONE[data.doctor.status] ?? "muted"}>{data.doctor.status}</Pill>}>
        <div className="table-wrap">
          <table className="grid">
            <tbody>
              {data.doctor.checks.map((check) => (
                <tr key={check.check}>
                  <td style={{ width: 200 }}><b>{check.check}</b></td>
                  <td style={{ width: 80 }}><Pill tone={TONE[check.status] ?? "muted"}>{check.status}</Pill></td>
                  <td className="ink2">{check.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
