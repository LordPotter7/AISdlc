import type { RailStage } from "@/lib/contract";
import { when } from "@/lib/format";
import { GateTag } from "./ui";

/** The seven lifecycle stages and five human gates, left to right. */
export function StageRail({ rail }: { rail: RailStage[] }) {
  return (
    <ol className="rail" aria-label="Lifecycle stages" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {rail.map((stage) => (
        <li key={stage.id} className={`stage ${stage.state}`} aria-current={stage.state === "current" ? "step" : undefined}>
          <span className="state">
            {stage.state === "done" ? "✓ done" : stage.state === "current" ? "● working here" : "○ ahead"}
          </span>
          <b>{stage.title}</b>
          {stage.gate ? (
            <span>
              <GateTag gate={stage.gate} record={stage.gate_record} />
              {stage.gate_record?.status === "approved" && stage.gate_record.at && (
                <span className="muted"> · {when(stage.gate_record.at)}</span>
              )}
            </span>
          ) : (
            <span className="muted">{stage.id === "register" ? "runner allocates id" : "lessons promoted"}</span>
          )}
        </li>
      ))}
    </ol>
  );
}
