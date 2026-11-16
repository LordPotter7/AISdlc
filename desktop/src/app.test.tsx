/**
 * UI tests over envelopes recorded from the real bridge (src/fixtures/demo.json). What the
 * tests see is what the runner concluded about the generated demo repository.
 */
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import App from "./App";
import demo from "./fixtures/demo.json";
import { ReplayTransport, setTransport } from "./lib/ipc";
import { useStudio } from "./state/store";

function reset() {
  useStudio.setState({ route: { view: "projects" }, history: [], registry: [], project: null, error: null, opening: false, refreshNonce: 0,
    paletteOpen: false, shortcutsOpen: false, newIntentOpen: false, viewer: null, toasts: [] });
}

beforeEach(() => {
  reset();
  setTransport(new ReplayTransport(demo, 0));
});

afterEach(() => {
  cleanup();
  setTransport(null);
});

async function openCockpit() {
  render(<App />);
  await screen.findByRole("heading", { name: "Cockpit" });
}

describe("recorded contract", () => {
  it("every recorded envelope speaks protocol 1.0 without an error", () => {
    const envelopes: unknown[] = [demo.hello, demo.open, demo.snapshot, demo.events, demo.diagnostics];
    for (const intent of Object.values(demo.intents)) {
      envelopes.push(intent.intent, intent.spec, intent.trace, intent.story, intent.events, ...Object.values(intent.gates));
    }
    for (const envelope of envelopes as { protocol_version: string; error: unknown }[]) {
      expect(envelope.protocol_version).toBe("1.0");
      expect(envelope.error).toBeNull();
    }
  });
});

describe("cockpit", () => {
  it("leads with what is waiting on a person, oldest first", async () => {
    await openCockpit();
    const waiting = screen.getByRole("heading", { name: "Waiting on a person" }).closest("section")!;
    const titles = within(waiting).getAllByRole("button", { pressed: false }).concat(within(waiting).queryAllByRole("button", { pressed: true }));
    expect(titles.map((b) => b.textContent).join(" ")).toMatch(/Order search/);
    expect(screen.getByText("2 gates")).toBeInTheDocument();
  });

  it("says so when the open folder is not run by AISDLC", async () => {
    await openCockpit();
    expect(screen.queryByRole("alert")).toBeNull();
    const problem = "This folder has no .ai-sdlc/config.yaml. Run `ai-sdlc init` (or /aisdlc:init) first.";
    act(() => {
      const project = useStudio.getState().project!;
      useStudio.setState({ project: { ...project, context: { ...project.context, problems: [problem] } } });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("no .ai-sdlc/config.yaml");
  });

  it("counts one contract conflict once", async () => {
    await openCockpit();
    const kpi = screen.getByText("Contract contention").closest(".kpi")!;
    expect(within(kpi as HTMLElement).getByText("1")).toBeInTheDocument();
  });

  it("prices approving the reopened Gate 2", async () => {
    await openCockpit();
    await screen.findByText(/Price of approving/);
    const price = screen.getByText("Tasks lose evidence").closest("div")!;
    expect(within(price).getByText("1")).toBeInTheDocument();
    expect(screen.getByText(/I002-P00-T002/)).toBeInTheDocument();
  });
});

describe("operations in the browser preview", () => {
  it("previews the exact command and refuses to pretend it ran", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(await screen.findByRole("button", { name: /Approve G2 as/ }));
    await user.type(screen.getByLabelText(/Your name/), "priya.product");
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await user.click(await screen.findByRole("button", { name: "Confirm and run" }));
    const dialog = screen.getByRole("dialog", { name: /Approve G2/ });
    expect(await within(dialog).findByText(/Operations run only in the desktop app/)).toBeInTheDocument();
    expect(screen.queryByText("The runner completed the operation.")).not.toBeInTheDocument();
  });
});

describe("story mode", () => {
  it("replays the recorded refusal and proves the spec is the approved one", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getAllByRole("button", { name: "Story Mode" })[0]!);
    await user.click(await screen.findByRole("button", { name: /^Specify, gate G2/ }));
    expect(await screen.findByText(/Runner refused to advance to specified/)).toBeInTheDocument();
    expect(screen.getByText(/SQ001/)).toBeInTheDocument();
    expect(screen.getByText("Verified identical to the approved version")).toBeInTheDocument();
    // Presenter notes stay folded away until asked for, and leave the screen while presenting.
    expect(screen.getByText("Presenter notes").closest("details")).not.toHaveAttribute("open");
    await user.click(screen.getByRole("button", { name: "Present full screen" }));
    expect(screen.queryByText("Presenter notes")).not.toBeInTheDocument();
  });

  it("gives the audit timeline a single tab stop", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getAllByRole("button", { name: "Story Mode" })[0]!);
    const track = await screen.findByRole("group", { name: /Audit timeline/ });
    const marks = within(track).getAllByRole("button");
    expect(marks.length).toBeGreaterThan(1);
    expect(marks.filter((mark) => mark.tabIndex === 0)).toHaveLength(1);
    await user.click(marks[0]!);
    expect(screen.getByText(/^.+: .+/, { selector: ".event-caption" })).toBeInTheDocument();
  });

  it("moves between chapters with the keyboard", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getAllByRole("button", { name: "Story Mode" })[0]!);
    await screen.findByRole("button", { name: /Register/ });
    await act(async () => {
      await user.keyboard("{ArrowRight}{ArrowRight}");
    });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Specify, gate G2/ })).toHaveAttribute("aria-current", "step"));
  });
});

describe("traceability", () => {
  it("opens on the chains that need attention, each with one verdict", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: /I002\s*Order search/ }));
    await user.click(await screen.findByRole("tab", { name: "Traceability" }));
    expect(await screen.findByRole("button", { name: /Needs attention/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Since G2 was approved: added REQ-F-003; changed REQ-F-002/)).toBeInTheDocument();
    // Added after G2 with no Task: the missing link outranks the change.
    await user.click(screen.getByRole("button", { name: /REQ-F-003/ }));
    const row = screen.getByRole("button", { name: /REQ-F-003/, pressed: true }).closest("tr")!;
    expect(within(row).getByText("Gap")).toBeInTheDocument();
    expect(within(row).getByText("no Task")).toBeInTheDocument();
    const detail = screen.getByRole("complementary", { name: "Selected chain" });
    expect(within(detail).getByText(/A link in the chain is missing/)).toBeInTheDocument();
    // The search narrows the table; the map shows the same links with lane headings.
    await user.click(screen.getByRole("button", { name: /^All/ }));
    await user.type(screen.getByRole("searchbox", { name: /Find a requirement/ }), "REQ-F-002");
    expect(screen.queryByRole("button", { name: /REQ-F-003/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Map" }));
    expect(screen.getByText("Evidence and review")).toBeInTheDocument();
  });
});

describe("learning the lifecycle", () => {
  it("shows the pipeline with every Intent on the stage it is working in", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: "How AISDLC works" }));
    const metro = await screen.findByRole("list", { name: /The lifecycle/ });
    const stop = (name: string) => within(metro).getByRole("button", { name: new RegExp(name) }).closest("li")!;
    // I003 is building; I004 has its brief framed and waits on G1; I001 is complete.
    expect(within(stop("Build")).getByRole("button", { name: "I003" })).toBeInTheDocument();
    expect(within(stop("Frame")).getByRole("button", { name: "I004" })).toBeInTheDocument();
    expect(within(stop("Close")).getByRole("button", { name: "I001" })).toBeInTheDocument();
    await user.click(within(metro).getByRole("button", { name: /Plan/ }));
    expect(await screen.findByText("04-build/<Ixxx-Pnn>/phase.yaml")).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: /Search the glossary/ }), "walking");
    expect(screen.getByText(/thinnest slice that proves the approach/)).toBeInTheDocument();
  });

  it("opens a requirement at its line, read-only, and says the gate locks it", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: /I001\s*Customer export API/ }));
    await user.click(await screen.findByRole("tab", { name: "Specification" }));
    const link = (await screen.findAllByRole("button", { name: /^requirements\.yaml:\d+$/ }))[0]!;
    const line = Number(link.textContent!.split(":")[1]);
    await user.click(link);
    const drawer = await screen.findByRole("dialog", { name: /requirements\.yaml/ });
    expect(await within(drawer).findByText(/locked by G2/)).toBeInTheDocument();
    expect(within(drawer).getByText(/ai-sdlc gate reopen I001 G2/)).toBeInTheDocument();
    expect(within(drawer).getByRole("textbox", { name: /requirements\.yaml, read-only/ })).toHaveTextContent("REQ-F-001");
    await waitFor(() => expect(within(drawer).getByText(`Ln ${line}`, { selector: ".pane-status span" })).toBeInTheDocument());
    // The runner's lint is on the record even while reading, and a problem takes you to its line.
    const problems = within(drawer).getByRole("region", { name: "Problems" });
    const problem = await within(problems).findByRole("button", { name: /REQ-F-002/ });
    const at = Number(problem.textContent!.match(/Ln (\d+)/)![1]);
    await user.click(problem);
    await waitFor(() => expect(within(drawer).getByText(`Ln ${at}`, { selector: ".pane-status span" })).toBeInTheDocument());
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /requirements\.yaml/ })).toBeNull());
  });

  it("explains jargon to keyboard users, not only on hover", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: /I001\s*Customer export API/ }));
    const heldOut = await screen.findByText("Held-out");
    expect(heldOut).toHaveAttribute("tabindex", "0");
    const tip = document.getElementById(heldOut.getAttribute("aria-describedby")!)!;
    expect(tip).toHaveAttribute("role", "tooltip");
    expect(tip.textContent).toMatch(/implementer does not see/);
  });
});

describe("authoring through the runner", () => {
  /** The recorded demo, plus the two answers only a live runner gives: compose, and a draft preview. */
  class AuthoringTransport extends ReplayTransport {
    requests: { op: string; params: Record<string, unknown> }[] = [];
    override async bridgeRequest<P>(op: Parameters<ReplayTransport["bridgeRequest"]>[0], params: Record<string, unknown>) {
      this.requests.push({ op, params });
      const base = JSON.parse(JSON.stringify(demo.hello));
      if (op === "authoring.compose") {
        return { ...base, kind: "authoring", payload: { path: "02-specification/requirements.yaml", content: "intent_id: I002\n", expected_sha: "a".repeat(64), ids: { requirement: "REQ-F-004", criteria: ["AC-005"] } } as P };
      }
      if (op === "operation.preview" && params.name === "artifact_write") {
        return {
          ...base,
          kind: "operation",
          payload: {
            name: "artifact_write", title: "Save a file you author", writes: true, effect: "The runner writes this one file.",
            command: "ai-sdlc artifact write I002 02-specification/requirements.yaml --from - --expected-sha aaaa --actor studio",
            may_change: [], base_hashes: {}, token: "t", expires_in_seconds: 600,
            draft: {
              diff: "--- a/02-specification/requirements.yaml\n+++ b/02-specification/requirements.yaml\n@@ -1 +1,2 @@\n+  - id: REQ-F-004\n",
              changed: true, refusal: null,
              checks: { validation_errors: [], lint: { passed: true, counts: { error: 0, warning: 1, info: 0 }, findings: [{ rule: "SQ002", severity: "warning", location: "REQ-F-004", message: "ambiguous term(s) 'fast'", source: { file: "02-specification/requirements.yaml", line: 30 } }] } },
            },
          } as P,
        };
      }
      return super.bridgeRequest<P>(op, params);
    }
  }

  it("composes a requirement from parts and shows the runner's diff and lint before saving", async () => {
    const transport = new AuthoringTransport(demo, 0);
    setTransport(transport);
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: /I002\s*Order search/ }));
    await user.click(await screen.findByRole("tab", { name: "Specification" }));
    await user.click(await screen.findByRole("button", { name: "+ Requirement" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a requirement" });
    const review = within(dialog).getByRole("button", { name: "Review the change" });
    expect(review).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText(/locale is right-to-left/), "support searches by order number");
    await user.type(within(dialog).getByPlaceholderText(/mark each status/), "return that order first");
    expect(within(dialog).getByText(/When support searches by order number, the service shall return that order first\./)).toBeInTheDocument();
    await user.type(within(dialog).getByPlaceholderText(/RTL statuses/), "Order-number search ranks it first.");
    await user.type(within(dialog).getByPlaceholderText(/pytest tests\/i18n/), "pytest -k order_number");
    await user.click(review);
    const compose = transport.requests.find((request) => request.op === "authoring.compose")!;
    expect(compose.params).toMatchObject({ intent_id: "I002", kind: "functional" });
    expect((compose.params.criteria as { check: string }[])[0]!.check).toBe("pytest -k order_number");
    const save = await screen.findByRole("dialog", { name: /Add REQ-F-004 to I002/ });
    const added = [...save.querySelectorAll(".diff-line.add")].map((line) => line.textContent);
    expect(added.map((line) => line?.trimEnd())).toContain("+  - id: REQ-F-004");
    expect(within(save).getByText(/ambiguous term/)).toBeInTheDocument();
    expect(within(save).getByRole("button", { name: "Save through the runner" })).toBeEnabled();
  });

  it("offers Edit only where the runner would accept it, and a gate reopen where it would not", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: /I004\s*Refund notifications/ }));
    await user.click(await screen.findByRole("button", { name: "brief.md" }));
    const brief = await screen.findByRole("dialog", { name: /brief\.md/ });
    await user.click(await within(brief).findByRole("button", { name: "Edit" }));
    expect(within(brief).getByRole("textbox", { name: /Edit 01-brief\/brief\.md/ })).toBeInTheDocument();
    expect(within(brief).getByRole("button", { name: "Review and save" })).toBeDisabled(); // nothing changed yet
    await user.click(within(brief).getByRole("button", { name: "Close" }));

    await user.click(screen.getByRole("button", { name: /I003\s*Tenant billing ledger/ }));
    await user.click(await screen.findByRole("tab", { name: "Specification" }));
    expect(await screen.findByText(/Locked by G2 approval/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Requirement" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Propose a change…" }));
    const spec = await screen.findByRole("dialog", { name: /requirements\.yaml/ });
    expect(within(spec).queryByRole("button", { name: "Edit" })).toBeNull();
    await user.click(await within(spec).findByRole("button", { name: "Propose a change…" }));
    expect(await screen.findByRole("dialog", { name: /Reopen G2 for I003/ })).toBeInTheDocument();
    expect(screen.getByLabelText(/Reason/)).toBeInTheDocument();
  });

  it("starts an Intent with the mode explained and model behaviour forcing full", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: "Start an Intent" }));
    const dialog = await screen.findByRole("dialog", { name: /Start an Intent/ });
    await user.click(within(dialog).getByRole("radio", { name: /Bug fix/ }));
    expect(within(dialog).getByRole("radio", { name: /Compact/ })).toBeChecked();
    await user.click(within(dialog).getByRole("checkbox", { name: /produced by an AI model/ }));
    expect(within(dialog).getByRole("radio", { name: /Compact/ })).toBeDisabled();
    expect(within(dialog).getByRole("radio", { name: /Full/ })).toBeChecked();
    expect(within(dialog).getByRole("button", { name: "Review" })).toBeDisabled(); // no title yet
    await user.type(within(dialog).getByRole("textbox", { name: /What change/ }), "Summaries of long chats");
    await user.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(await screen.findByText(/ai-sdlc intent-new/)).toBeInTheDocument(); // the browser preview's stand-in command
  });
});

describe("pipeline profiles", () => {
  it("shows the fixed constitution, each profile against standard, and every waiver", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: "Pipeline" }));
    expect(await screen.findByRole("heading", { name: "Pipeline", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /The constitution/ })).toHaveTextContent(/five gates each approved by a person/);
    const table = screen.getAllByRole("table")[0]!;
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers[1]).toMatch(/^standard/);
    expect(headers.some((text) => /^regulated.*Selected by security/.test(text ?? ""))).toBe(true);
    const lintRow = within(table).getByRole("rowheader", { name: "Lint blocks G2 on" }).closest("tr")!;
    expect(within(lintRow).getByText(/loosened, waived by sam\.lead/)).toBeInTheDocument();
    expect(within(lintRow).getByText("stricter")).toBeInTheDocument();
    const gateRow = within(table).getByRole("rowheader", { name: "Gate approvals" }).closest("tr")!;
    expect(within(gateRow).getByText(/G2: 2 different people from lee\.architect/)).toBeInTheDocument();
    expect(screen.getByText(/Spikes are thrown away/)).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Move I002 to a stricter profile" }), "regulated");
    const dialog = await screen.findByRole("dialog", { name: /Move I002 to the regulated profile/ });
    expect(within(dialog).getByText(/no move back to a weaker profile/)).toBeInTheDocument();
  });

  it("tells a new Intent which profile its type runs under and offers only its modes", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: "Start an Intent" }));
    const dialog = await screen.findByRole("dialog", { name: /Start an Intent/ });
    await user.click(within(dialog).getByRole("radio", { name: /Security/ }));
    expect(await within(dialog).findByText("regulated")).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: /Compact/ })).toBeDisabled();
    expect(within(dialog).getByRole("radio", { name: /Full/ })).toBeChecked();
    await user.click(within(dialog).getByRole("radio", { name: /Feature/ }));
    expect(within(dialog).getByText("standard")).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: /Compact/ })).toBeEnabled();
  });
});

describe("many Intents in one repository", () => {
  it("ranks by priority, shows programme order, and records waivers and priorities as decisions", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: "Portfolio" }));
    expect(await screen.findByRole("heading", { name: "Portfolio", level: 1 })).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByRole("combobox", { name: "Priority of I003" })).toHaveValue("high");
    expect(within(rows[0]!).getByText(/Finance closes the quarter on the ledger/)).toBeInTheDocument();
    expect(within(rows.at(-1)!).getByRole("combobox", { name: "Priority of I004" })).toHaveValue("low");

    // Programme order: the ledger before search, and search waits on it.
    const program = screen.getByRole("complementary", { name: "Programmes and overlaps" });
    const steps = within(program).getAllByRole("listitem").map((item) => item.textContent);
    expect(steps.findIndex((text) => text!.startsWith("I003"))).toBeLessThan(steps.findIndex((text) => text!.startsWith("I002")));
    await user.click(screen.getByRole("button", { name: /Waiting on a predecessor/ }));
    const waiting = screen.getAllByRole("row").slice(1);
    expect(waiting).toHaveLength(1);
    await user.click(within(waiting[0]!).getByRole("button", { name: "Waive…" }));
    const dialog = await screen.findByRole("dialog", { name: /Let I002 build before I003/ });
    expect(within(dialog).getByLabelText(/Reason/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await user.click(screen.getByRole("button", { name: /^All/ }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Priority of I004" }), "high");
    expect(await screen.findByRole("dialog", { name: /Set I004 to high priority/ })).toBeInTheDocument();
  });
});

describe("adopting an existing codebase", () => {
  it("shows adoption progress, and the modules more than one open Intent is changing", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: "Codebase" }));
    expect(await screen.findByRole("heading", { name: "Codebase", level: 1 })).toBeInTheDocument();
    const adopt = screen.getByRole("region", { name: "Adopt this repository" });
    expect(within(adopt).getByText("Record what the system is")).toHaveTextContent(/next/);
    expect(within(adopt).getByText("/aisdlc:understand repo")).toBeInTheDocument();
    expect(within(adopt).getByText("Run AISDLC in this repository")).toHaveTextContent(/done/);

    // The contended view leads: src/ is changed by two open Intents.
    expect(screen.getByRole("button", { name: /Changed by 2\+ open Intents/ })).toHaveAttribute("aria-pressed", "true");
    const row = screen.getByText("src/").closest("tr")!;
    expect(within(row).getByRole("button", { name: /I002/ })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /I003/ })).toBeInTheDocument();
    expect(within(row).getByText(/open Intents/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^All/ }));
    expect(screen.getByText(".ai-sdlc/").closest("tr")).toHaveTextContent("lifecycle");

    // From the map to the Intent, which says where it changes the code and who else is there.
    await user.click(within(screen.getByText("src/").closest("tr")!).getByRole("button", { name: /I002/ }));
    const footprint = (await screen.findByText("Where this Intent changes the code")).closest("section")!;
    expect(within(footprint).getByRole("button", { name: "src/" })).toBeInTheDocument();
    expect(within(footprint).getByRole("button", { name: "I003" })).toBeInTheDocument();
    expect(within(footprint).getByText(/Coordinate the overlap/)).toBeInTheDocument();
  });
});

describe("the workbench", () => {
  it("lists an Intent's files by stage and edits a brief beside its rendered preview", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: /I002\s*Order search/ }));
    await user.click(await screen.findByRole("tab", { name: "Files" }));
    const tree = await screen.findByRole("navigation", { name: "Intent files" });
    expect(within(tree).getByText("Specify")).toBeInTheDocument();
    expect(within(tree).getByRole("button", { name: /requirements\.yaml/ })).toHaveAttribute("title", expect.stringMatching(/you author this file/));

    await user.click(within(tree).getByRole("button", { name: /brief\.md/ }));
    // Markdown opens rendered, with an outline of its headings.
    expect(await screen.findByRole("heading", { name: /Intent Brief: I002/ })).toBeInTheDocument();
    const outline = screen.getByRole("navigation", { name: "Outline" });
    expect(within(outline).getByRole("button", { name: "Success measures" })).toBeInTheDocument();
    expect(await screen.findByText("None in this file")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("button", { name: "Split" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("textbox", { name: "Edit 01-brief/brief.md" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Intent Brief: I002/ })).toBeInTheDocument(); // the live preview
    expect(screen.getByRole("button", { name: "Review and save" })).toBeDisabled(); // nothing changed yet
  });

  it("outlines a specification by its records and offers its schema's keys", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: /I002\s*Order search/ }));
    await user.click(await screen.findByRole("tab", { name: "Files" }));
    const tree = await screen.findByRole("navigation", { name: "Intent files" });
    await user.click(within(tree).getByRole("button", { name: /requirements\.yaml/ }));
    const outline = await screen.findByRole("navigation", { name: "Outline" });
    expect(await within(outline).findByRole("button", { name: /REQ-F-001/ })).toBeInTheDocument();
    expect(within(outline).getByRole("button", { name: "functional_requirements" })).toBeInTheDocument();
    await user.click(within(outline).getByRole("button", { name: /REQ-F-002/ }));
    const line = demo.intents.I002.checks["02-specification/requirements.yaml"].payload.identifiers.find((i) => i.id === "REQ-F-002")!.line;
    await waitFor(() => expect(screen.getByText(`Ln ${line}`, { selector: ".pane-status span" })).toBeInTheDocument());
  });
});

describe("trace and decide", () => {
  it("collects everything waiting on a person, and co-approval asks for a name and a reason", async () => {
    const user = userEvent.setup();
    await openCockpit();
    const nav = screen.getByRole("button", { name: /Needs you/ });
    await waitFor(() => expect(nav).toHaveTextContent(/\d/));
    await user.click(nav);
    await screen.findByRole("heading", { name: "Needs you" });
    const items = screen.getAllByRole("listitem").filter((item) => item.classList.contains("inbox-item"));
    expect(items[0]).toHaveTextContent(/G2 waits for a person's decision|G1 waits/);
    const decision = items.find((item) => /both change contract:orders#listOrders/.test(item.textContent ?? ""))!;
    await user.click(within(decision).getByRole("button", { name: "Co-approve…" }));
    const dialog = await screen.findByRole("dialog", { name: /Co-approve contract:orders#listOrders/ });
    expect(within(dialog).getByLabelText(/Your name, as the approver on record/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Reason/)).toBeInTheDocument();
  });

  it("puts what we're building on one page, with coverage and the links that may no longer hold", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: /I002\s*Order search/ }));
    await user.click(await screen.findByRole("tab", { name: "What we're building" }));
    expect(await screen.findByRole("heading", { name: "The problem" })).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "with an acceptance criterion" })).toBeInTheDocument();
    const moved = screen.getByRole("heading", { name: "What may no longer hold" }).closest("section")!;
    expect(within(moved).getByText(/REQ-F-002 changed since G2 was approved/)).toBeInTheDocument();
    expect(within(moved).getByText(/approve G2 again/)).toBeInTheDocument();
    expect(within(moved).getByText(/REQ-F-003/)).toBeInTheDocument(); // added after G2, no Task yet
  });
});

describe("enterprise shell", () => {
  it("jumps anywhere from the command palette with the keyboard", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.keyboard("{Control>}k{/Control}");
    const palette = await screen.findByRole("dialog", { name: "Search or jump to" });
    const input = within(palette).getByRole("combobox");
    await user.type(input, "I003 spec");
    const first = within(palette).getAllByRole("option")[0]!;
    expect(first).toHaveAttribute("aria-selected", "true");
    expect(first).toHaveTextContent("I003 Specification");
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("tab", { name: "Specification", selected: true })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Search or jump to" })).toBeNull();
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByText("Specification")).toHaveAttribute("aria-current", "page");
  });

  it("has keyboard shortcuts, a sheet that lists them, and arrow keys between tabs", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.keyboard("?");
    expect(await screen.findByRole("dialog", { name: "Keyboard shortcuts" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).toBeNull());
    await user.keyboard("gl");
    expect(await screen.findByRole("heading", { name: "How an idea becomes merged code" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /I001\s*Customer export API/ }));
    const overview = await screen.findByRole("tab", { name: "Overview", selected: true });
    overview.focus();
    await user.keyboard("{ArrowRight}");
    expect(await screen.findByRole("tab", { name: "What we're building", selected: true })).toBeInTheDocument();
  });

  it("says each Intent's stage in words beside the micro-rail", async () => {
    await openCockpit();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("button", { name: /I004.*working on Frame, waiting on G1/ })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /I001.*completed/ })).toBeInTheDocument();
  });

  it("echoes a refused operation as a toast without taking focus", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(await screen.findByRole("button", { name: /Approve G2 as/ }));
    await user.type(screen.getByLabelText(/Your name/), "priya.product");
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await user.click(await screen.findByRole("button", { name: "Confirm and run" }));
    const status = screen.getAllByRole("status").find((region) => region.classList.contains("toaster"))!;
    expect(await within(status).findByText(/not run|refused/)).toBeInTheDocument();
  });

  it("switches density from settings and keeps it on the document", async () => {
    const user = userEvent.setup();
    await openCockpit();
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("radio", { name: "Compact" }));
    expect(document.documentElement.dataset.density).toBe("compact");
    await user.click(screen.getByRole("radio", { name: "Comfortable" }));
    expect(document.documentElement.dataset.density).toBe("comfortable");
  });
});
