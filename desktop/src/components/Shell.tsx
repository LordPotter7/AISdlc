import { useEffect, useMemo, useRef, useState } from "react";
import type { InboxPayload, IntentSummary, Portfolio, StageId } from "@/lib/contract";
import { ago } from "@/lib/format";
import { getTransport } from "@/lib/ipc";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useStudio, type IntentTab, type Theme } from "@/state/store";
import { useProjection } from "@/state/useProjection";
import { Icon, type IconName } from "./Icon";

const STAGES: { id: StageId; title: string }[] = [
  { id: "register", title: "Register" },
  { id: "frame", title: "Frame" },
  { id: "specify", title: "Specify" },
  { id: "plan", title: "Plan" },
  { id: "build", title: "Build" },
  { id: "converge", title: "Converge" },
  { id: "close", title: "Close" },
];
const TAB_LABEL: Record<IntentTab, string> = {
  overview: "Overview",
  summary: "What we're building",
  specification: "Specification",
  files: "Files",
  traceability: "Traceability",
  activity: "Activity",
};
const VIEW_LABEL: Record<string, string> = {
  projects: "Repositories",
  learn: "How AISDLC works",
  inbox: "Needs you",
  cockpit: "Cockpit",
  portfolio: "Portfolio",
  codebase: "Codebase",
  story: "Story Mode",
  doctor: "Doctor",
  pipeline: "Pipeline",
};
const BRIDGE_TONE: Record<string, string> = { current: "ok", connecting: "info", stale: "warn" };

/**
 * An Intent's place in the lifecycle as seven ticks: filled for done, amber diamond when a
 * person must decide, outlined ahead. The sentence beside it says the same thing in words.
 */
export function MicroRail({ intent }: { intent: Pick<IntentSummary, "status" | "working_stage" | "waiting_on"> }) {
  const index = intent.status === "completed" ? STAGES.length : STAGES.findIndex((stage) => stage.id === intent.working_stage);
  const current = STAGES[index];
  const words =
    intent.status === "completed"
      ? "completed"
      : intent.status === "aborted"
        ? "aborted"
        : current
          ? `working on ${current.title}${intent.waiting_on ? `, waiting on ${intent.waiting_on.gate}` : ""}`
          : "stage unknown";
  return (
    <>
      <span className="micro-rail" aria-hidden="true">
        {STAGES.map((stage, at) => (
          <i
            key={stage.id}
            className={at < index ? "done" : at === index ? (intent.waiting_on ? "gate" : "now") : undefined}
          />
        ))}
      </span>
      <span className="sr-only">, {words}</span>
    </>
  );
}

function NavItem({
  icon,
  label,
  current,
  count,
  tone,
  onClick,
  collapsed,
}: {
  icon: IconName;
  label: string;
  current: boolean;
  count?: number;
  tone?: "gate";
  onClick(): void;
  collapsed: boolean;
}) {
  return (
    <button type="button" className="nav-item" aria-current={current ? "page" : undefined} onClick={onClick} title={collapsed ? label : undefined}>
      <Icon name={icon} size={17} />
      <span className="nav-label">{label}</span>
      {count ? <span className={`count${tone ? ` ${tone}` : ""}`}>{count}</span> : null}
    </button>
  );
}

export function Sidebar() {
  const route = useStudio((s) => s.route);
  const navigate = useStudio((s) => s.navigate);
  const project = useStudio((s) => s.project);
  const collapsed = useStudio((s) => s.sidebarCollapsed);
  const toggle = useStudio((s) => s.toggleSidebar);
  const setOverlay = useStudio((s) => s.setOverlay);
  const portfolio = useProjection<Portfolio>("project.snapshot", {}, !!project);
  const inbox = useProjection<InboxPayload>("inbox.inspect", {}, !!project);
  const [filter, setFilter] = useState("");
  const intents = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (portfolio.data?.intents ?? []).filter((intent) => !needle || `${intent.id} ${intent.title}`.toLowerCase().includes(needle));
  }, [portfolio.data, filter]);
  const is = (view: string) => route.view === view;
  const waiting = inbox.data?.items.length ?? 0;

  return (
    <nav className={`nav${collapsed ? " collapsed" : ""}`} aria-label="Main">
      {project ? (
        <>
          <NavItem icon="inbox" label="Needs you" current={is("inbox")} count={waiting} tone="gate" onClick={() => navigate({ view: "inbox" })} collapsed={collapsed} />
          <NavItem icon="cockpit" label="Cockpit" current={is("cockpit")} onClick={() => navigate({ view: "cockpit" })} collapsed={collapsed} />
          <NavItem icon="portfolio" label="Portfolio" current={is("portfolio")} onClick={() => navigate({ view: "portfolio" })} collapsed={collapsed} />
          <NavItem icon="codebase" label="Codebase" current={is("codebase")} onClick={() => navigate({ view: "codebase" })} collapsed={collapsed} />
          <NavItem icon="learn" label="How AISDLC works" current={is("learn")} onClick={() => navigate({ view: "learn" })} collapsed={collapsed} />
          <NavItem icon="story" label="Story Mode" current={is("story")} onClick={() => navigate({ view: "story" })} collapsed={collapsed} />

          <div className="nav-section">
            <span className="grp" id="nav-intents">Intents</span>
            <button type="button" className="icon-btn" aria-label="Start an Intent" title="Start an Intent (Shift+N)" onClick={() => setOverlay("newIntent", true)}>
              <Icon name="plus" size={16} />
            </button>
          </div>
          {!collapsed && (portfolio.data?.intents.length ?? 0) > 4 && (
            <input
              className="nav-filter"
              type="search"
              aria-label="Filter Intents"
              placeholder="Filter"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          )}
          <div className="nav-intents" role="group" aria-labelledby="nav-intents">
            {intents.map((intent) => (
              <button
                key={intent.id}
                type="button"
                className="nav-intent"
                aria-current={route.view === "intent" && route.intentId === intent.id ? "page" : undefined}
                title={collapsed ? `${intent.id} ${intent.title}` : intent.title}
                onClick={() =>
                  navigate({ view: "intent", intentId: intent.id, tab: route.view === "intent" ? route.tab ?? "overview" : "overview" })
                }
              >
                <span className="intent-id">{intent.id}</span>
                <span className="intent-title">{intent.title}</span>
                <MicroRail intent={intent} />
              </button>
            ))}
            {filter && intents.length === 0 && <span className="nav-empty">No Intent matches “{filter}”.</span>}
          </div>

          <div className="nav-section">
            <span className="grp">Runner</span>
          </div>
          <NavItem icon="pipeline" label="Pipeline" current={is("pipeline")} onClick={() => navigate({ view: "pipeline" })} collapsed={collapsed} />
          <NavItem icon="doctor" label="Doctor" current={is("doctor")} onClick={() => navigate({ view: "doctor" })} collapsed={collapsed} />
        </>
      ) : null}
      <NavItem icon="repos" label="Repositories" current={is("projects")} onClick={() => navigate({ view: "projects" })} collapsed={collapsed} />
      <span style={{ flex: 1 }} />
      <button type="button" className="nav-item nav-toggle" onClick={toggle} aria-label={collapsed ? "Show sidebar labels" : "Hide sidebar labels"} title="Toggle sidebar ([)">
        <Icon name={collapsed ? "expand" : "collapse"} size={17} />
        <span className="nav-label">Hide labels</span>
      </button>
    </nav>
  );
}

function SettingsMenu() {
  const [open, setOpen] = useState(false);
  const theme = useStudio((s) => s.theme);
  const setTheme = useStudio((s) => s.setTheme);
  const density = useStudio((s) => s.density);
  const setDensity = useStudio((s) => s.setDensity);
  const setOverlay = useStudio((s) => s.setOverlay);
  const panel = useRef<HTMLDivElement>(null);
  const anchor = useRef<HTMLDivElement>(null);
  useFocusTrap(panel, open, () => setOpen(false));
  useEffect(() => {
    if (!open) return;
    // Close on a click anywhere outside the menu; never on pointer drift.
    const onDown = (event: MouseEvent) => !anchor.current?.contains(event.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  return (
    <div className="menu-anchor" ref={anchor}>
      <button type="button" className="icon-btn" aria-label="Settings" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)}>
        <Icon name="settings" size={17} />
      </button>
      {open && (
        <div className="menu" role="dialog" aria-label="Settings" ref={panel} tabIndex={-1}>
          <fieldset>
            <legend>Theme</legend>
            {(["dark", "light", "system"] as Theme[]).map((value) => (
              <label key={value} className="menu-choice">
                <input type="radio" name="theme" checked={theme === value} onChange={() => setTheme(value)} />
                {value === "system" ? "Match Windows" : value === "dark" ? "Dark" : "Light"}
              </label>
            ))}
          </fieldset>
          <fieldset>
            <legend>Spacing</legend>
            {(["comfortable", "compact"] as const).map((value) => (
              <label key={value} className="menu-choice">
                <input type="radio" name="density" checked={density === value} onChange={() => setDensity(value)} />
                {value === "compact" ? "Compact" : "Comfortable"}
              </label>
            ))}
          </fieldset>
          <button
            type="button"
            className="btn sm"
            onClick={() => {
              setOpen(false);
              setOverlay("shortcuts", true);
            }}
          >
            <Icon name="keyboard" size={15} /> Keyboard shortcuts
          </button>
        </div>
      )}
    </div>
  );
}

export function TopBar() {
  const project = useStudio((s) => s.project);
  const bridge = useStudio((s) => s.bridge);
  const refresh = useStudio((s) => s.refresh);
  const navigate = useStudio((s) => s.navigate);
  const setOverlay = useStudio((s) => s.setOverlay);
  const replay = getTransport().mode === "replay";
  return (
    <header className="topbar">
      <span className="brand">
        <Icon name="gate" size={16} className="brand-mark" />
        AISDLC Studio
      </span>
      {project && (
        <button type="button" className="repo-switch" onClick={() => navigate({ view: "projects" })} title="Switch repository">
          <span className="repo-name">{project.context.name}</span>
          <span className="repo-ref mono">
            {project.context.git.branch ?? "no branch"}@{project.context.git.head ?? "no commit"}
          </span>
        </button>
      )}
      {project && (
        <button type="button" className="search-trigger" onClick={() => setOverlay("palette", true)} aria-keyshortcuts="Control+K">
          <Icon name="search" size={15} />
          <span>Search or jump to…</span>
          <kbd>Ctrl K</kbd>
        </button>
      )}
      <span className="spacer" />
      {replay && <span className="chip">Browser preview of a recorded demo</span>}
      <span className="bridge" title={bridge.detail ?? undefined}>
        <span className={`dot ${BRIDGE_TONE[bridge.state] ?? "bad"}`} />
        <span>Bridge {bridge.state}</span>
      </span>
      {project && (
        <button type="button" className="icon-btn" aria-label="Refresh from the runner" title="Refresh from the runner (Shift+R)" onClick={() => void refresh()}>
          <Icon name="refresh" size={17} />
        </button>
      )}
      <SettingsMenu />
    </header>
  );
}

export function Breadcrumbs() {
  const route = useStudio((s) => s.route);
  const project = useStudio((s) => s.project);
  const navigate = useStudio((s) => s.navigate);
  const portfolio = useProjection<Portfolio>("project.snapshot", {}, !!project && route.view === "intent");
  if (!project || route.view === "projects") return null;
  const intent = route.view === "intent" ? portfolio.data?.intents.find((item) => item.id === route.intentId) : undefined;
  const crumbs: { label: string; go?: () => void }[] = [{ label: project.context.name, go: () => navigate({ view: "cockpit" }) }];
  if (route.view === "intent" && route.intentId) {
    crumbs.push({ label: `${route.intentId}${intent ? ` ${intent.title}` : ""}`, go: () => navigate({ view: "intent", intentId: route.intentId, tab: "overview" }) });
    crumbs.push({ label: TAB_LABEL[route.tab ?? "overview"] });
  } else {
    crumbs.push({ label: VIEW_LABEL[route.view] ?? route.view });
  }
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <ol>
        {crumbs.map((crumb, index) => (
          <li key={index}>
            {index > 0 && <Icon name="chevronRight" size={13} className="crumb-sep" />}
            {crumb.go && index < crumbs.length - 1 ? (
              <button type="button" onClick={crumb.go}>{crumb.label}</button>
            ) : (
              <span aria-current={index === crumbs.length - 1 ? "page" : undefined}>{crumb.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function StatusBar() {
  const project = useStudio((s) => s.project);
  if (!project) return <footer className="footer">No repository open</footer>;
  return (
    <footer className="footer">
      <span className="footer-item">
        <span className={`dot ${project.freshness === "current" ? "ok" : project.freshness === "stale" ? "warn" : "info"}`} />
        {project.freshness === "current" ? "Up to date" : `Data ${project.freshness}`}
      </span>
      <span>{project.lastEventAt ? `Runner activity ${ago(project.lastEventAt)}` : "Watching for runner activity"}</span>
      <span style={{ marginLeft: "auto" }}>Every change goes through the runner and its audit log.</span>
      <span className="muted">Press ? for shortcuts</span>
    </footer>
  );
}

const SHORTCUTS: [string, string][] = [
  ["Ctrl K", "Search or jump to anything"],
  ["g then n", "Needs you"],
  ["g then c", "Cockpit"],
  ["g then p", "Portfolio"],
  ["g then b", "Codebase"],
  ["g then l", "How AISDLC works"],
  ["g then s", "Story Mode"],
  ["g then d", "Doctor"],
  ["g then f", "Pipeline"],
  ["Shift N", "Start an Intent"],
  ["Shift R", "Refresh from the runner"],
  ["[", "Show or hide sidebar labels"],
  ["← →", "Move between tabs"],
  ["Esc", "Close a dialog or panel"],
  ["?", "This list"],
];

export function ShortcutSheet() {
  const open = useStudio((s) => s.shortcutsOpen);
  const setOverlay = useStudio((s) => s.setOverlay);
  const root = useRef<HTMLDivElement>(null);
  const close = () => setOverlay("shortcuts", false);
  useFocusTrap(root, open, close);
  if (!open) return null;
  return (
    <div className="overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && close()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="keys-title" ref={root} tabIndex={-1}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 id="keys-title">Keyboard shortcuts</h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={close}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <dl className="keys">
          {SHORTCUTS.map(([keys, what]) => (
            <div key={keys}>
              <dt>{keys.split(" ").map((key, index) => (key === "then" ? <span key={index} className="muted"> then </span> : <kbd key={index}>{key}</kbd>))}</dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

/** Global keys. Ignored while typing in a field, except Ctrl+K, which always opens search. */
export function useShortcuts() {
  const navigate = useStudio((s) => s.navigate);
  const setOverlay = useStudio((s) => s.setOverlay);
  const refresh = useStudio((s) => s.refresh);
  const toggleSidebar = useStudio((s) => s.toggleSidebar);
  const project = useStudio((s) => s.project);
  useEffect(() => {
    let leader = 0;
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (project) setOverlay("palette", true);
        return;
      }
      const target = event.target as HTMLElement | null;
      const typing = !!target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
      const state = useStudio.getState();
      if (typing || event.ctrlKey || event.metaKey || event.altKey || state.paletteOpen || state.shortcutsOpen || state.newIntentOpen || state.viewer) return;
      if (event.key === "?") {
        setOverlay("shortcuts", true);
        return;
      }
      if (!project) return;
      if (leader && Date.now() - leader < 1200) {
        const views: Record<string, "inbox" | "cockpit" | "portfolio" | "codebase" | "learn" | "story" | "doctor" | "pipeline"> = { n: "inbox", c: "cockpit", p: "portfolio", b: "codebase", l: "learn", s: "story", d: "doctor", f: "pipeline" };
        leader = 0;
        const view = views[event.key];
        if (view) navigate({ view });
        return;
      }
      if (event.key === "g") leader = Date.now();
      else if (event.key === "N") setOverlay("newIntent", true);
      else if (event.key === "R") void refresh();
      else if (event.key === "[") toggleSidebar();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, setOverlay, refresh, toggleSidebar, project]);
}
