import { useState } from "react";
import type { InboxItem, InboxPayload } from "@/lib/contract";
import { ago } from "@/lib/format";
import { Term } from "@/components/Guide";
import { OperationDialog, type OperationRequest } from "@/components/OperationDialog";
import { Empty, ErrorBox, Loading, Panel, Pill, type Tone } from "@/components/ui";
import { useStudio } from "@/state/store";
import { useProjection } from "@/state/useProjection";

const KIND: Record<InboxItem["kind"], { label: string; tone: Tone; term?: string }> = {
  gate: { label: "Gate", tone: "gate", term: "gate" },
  contention: { label: "Decision", tone: "warn", term: "contention" },
  blocked: { label: "Blocked Task", tone: "bad" },
  finding: { label: "Finding", tone: "bad", term: "finding" },
  lint: { label: "Spec lint", tone: "bad", term: "spec-lint" },
  suspect: { label: "Suspect links", tone: "warn", term: "traceability" },
};

/**
 * Everything waiting on a person, in one list: gates to decide, shared resources to
 * co-approve or serialise, Tasks that exhausted their retries, serious findings, specs the
 * linter blocks, and links that may no longer hold. Each item says what clears it.
 */
export function InboxView() {
  const { data, error, loading, reload } = useProjection<InboxPayload>("inbox.inspect");
  const navigate = useStudio((s) => s.navigate);
  const openFile = useStudio((s) => s.openFile);
  const refresh = useStudio((s) => s.refresh);
  const [operation, setOperation] = useState<OperationRequest | null>(null);

  if (loading && !data) return <div className="page"><Loading height={320} /></div>;
  if (error) return <div className="page"><ErrorBox message={error} retry={reload} /></div>;
  if (!data) return null;

  const coapprove = (item: InboxItem) =>
    setOperation({
      name: "coapprove_resource",
      title: `Co-approve ${item.resource}`,
      args: { resource: item.resource, intents: [...(item.intents ?? [])].sort() },
      identityField: "approver",
      reasonField: true,
      explanation:
        `Records that ${item.intents?.join(" and ")} may both change ${item.resource} at the same time, with your name and ` +
        "reason. The alternative is to serialise them: finish one before the other moves past planning.",
    });

  const actions = (item: InboxItem) => {
    const intent = () => navigate({ view: "intent", intentId: item.intent, tab: "overview" });
    switch (item.kind) {
      case "gate":
        return <button type="button" className="btn sm gate" onClick={intent}>Read the packet</button>;
      case "contention":
        return (
          <>
            {item.intents?.map((id) => (
              <button key={id} type="button" className="btn sm" onClick={() => navigate({ view: "intent", intentId: id!, tab: "specification" })}>
                {id} contract
              </button>
            ))}
            <button type="button" className="btn sm gate" onClick={() => coapprove(item)}>Co-approve…</button>
          </>
        );
      case "finding":
        return (
          <button type="button" className="btn sm" onClick={() => openFile({ scope: "intent", intentId: item.intent, path: "05-verification/findings.yaml" })}>
            Open findings
          </button>
        );
      case "suspect":
        return <button type="button" className="btn sm" onClick={() => navigate({ view: "intent", intentId: item.intent, tab: "summary" })}>See what moved</button>;
      case "lint":
        return <button type="button" className="btn sm" onClick={() => navigate({ view: "intent", intentId: item.intent, tab: "specification" })}>Open the spec</button>;
      default:
        return <button type="button" className="btn sm" onClick={intent}>Open {item.intent}</button>;
    }
  };

  return (
    <div className="page" style={{ display: "grid", gap: 16 }}>
      <header className="page-head">
        <div>
          <h1>Needs you</h1>
          <span className="sub">Decisions only a person can make, and problems that stop the runner, oldest first within each kind.</span>
        </div>
      </header>
      <Panel title="Waiting" aside={`${data.items.length} item${data.items.length === 1 ? "" : "s"}`}>
        {data.items.length === 0 ? (
          <Empty>Nothing waits on a person. The next moves belong to the team and the agent.</Empty>
        ) : (
          <ul className="inbox">
            {data.items.map((item, index) => {
              const kind = KIND[item.kind];
              return (
                <li key={`${item.kind}-${item.intent}-${index}`} className="inbox-item">
                  <div className="inbox-head">
                    <Pill tone={kind.tone}>{kind.term ? <Term id={kind.term}>{kind.label}</Term> : kind.label}</Pill>
                    {item.gate && <Pill tone="gate">{item.gate}</Pill>}
                    <span className="mono">{item.intent}</span>
                    <span>{item.title}</span>
                    {item.since && <span className="muted" style={{ marginLeft: "auto" }}>waiting {ago(item.since)}</span>}
                  </div>
                  <div className="ink2">{item.summary}</div>
                  <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>{actions(item)}</div>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
      {operation && (
        <OperationDialog
          request={operation}
          onClose={(changed) => {
            setOperation(null);
            if (changed) {
              reload();
              void refresh();
            }
          }}
        />
      )}
    </div>
  );
}
