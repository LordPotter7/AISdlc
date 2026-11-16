import { useMemo, useRef, useState } from "react";
import type { Portfolio } from "@/lib/contract";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useStudio, type Route } from "@/state/store";
import { useProjection } from "@/state/useProjection";
import { Icon, type IconName } from "./Icon";

interface Command {
  id: string;
  group: "Go to" | "Intents" | "Actions";
  label: string;
  hint?: string;
  icon: IconName;
  keywords?: string;
  run(): void;
}

/** Subsequence match with a bonus for word starts; 0 means no match. */
export function score(query: string, text: string): number {
  const needle = query.trim().toLowerCase();
  if (!needle) return 1;
  const hay = text.toLowerCase();
  if (hay.includes(needle)) return 100 - hay.indexOf(needle);
  let position = 0;
  let points = 0;
  for (const char of needle) {
    const found = hay.indexOf(char, position);
    if (found < 0) return 0;
    points += found === 0 || /[\s\-_/]/.test(hay[found - 1] ?? "") ? 3 : 1;
    position = found + 1;
  }
  return points;
}

const TABS = [
  { tab: "summary", label: "What we're building" },
  { tab: "specification", label: "Specification" },
  { tab: "files", label: "Files" },
  { tab: "traceability", label: "Traceability" },
  { tab: "activity", label: "Activity" },
] as const;

/**
 * Search or jump to anything: a view, an Intent (and its tabs), or an action. Keyboard
 * first — type, arrow keys, Enter — and a combobox a screen reader can follow.
 */
export function CommandPalette() {
  const open = useStudio((s) => s.paletteOpen);
  const project = useStudio((s) => s.project);
  if (!open || !project) return null;
  return <Palette />;
}

function Palette() {
  const navigate = useStudio((s) => s.navigate);
  const setOverlay = useStudio((s) => s.setOverlay);
  const refresh = useStudio((s) => s.refresh);
  const theme = useStudio((s) => s.theme);
  const setTheme = useStudio((s) => s.setTheme);
  const density = useStudio((s) => s.density);
  const setDensity = useStudio((s) => s.setDensity);
  const toggleSidebar = useStudio((s) => s.toggleSidebar);
  const portfolio = useProjection<Portfolio>("project.snapshot");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const close = () => setOverlay("palette", false);
  useFocusTrap(root, true, close);

  const commands = useMemo<Command[]>(() => {
    const go = (route: Route) => () => navigate(route);
    const list: Command[] = [
      { id: "v-inbox", group: "Go to", label: "Needs you", icon: "inbox", keywords: "inbox gates decisions", run: go({ view: "inbox" }) },
      { id: "v-cockpit", group: "Go to", label: "Cockpit", icon: "cockpit", keywords: "home portfolio", run: go({ view: "cockpit" }) },
      { id: "v-portfolio", group: "Go to", label: "Portfolio", icon: "portfolio", keywords: "priority programme program dependencies wip build limit order", run: go({ view: "portfolio" }) },
      { id: "v-codebase", group: "Go to", label: "Codebase", icon: "codebase", keywords: "modules owners tests adopt brownfield map scan", run: go({ view: "codebase" }) },
      { id: "v-learn", group: "Go to", label: "How AISDLC works", icon: "learn", keywords: "learn glossary stages lint", run: go({ view: "learn" }) },
      { id: "v-story", group: "Go to", label: "Story Mode", icon: "story", keywords: "present replay", run: go({ view: "story" }) },
      { id: "v-pipeline", group: "Go to", label: "Pipeline", icon: "pipeline", keywords: "profiles rules constitution waivers gates approvals regulated spike", run: go({ view: "pipeline" }) },
      { id: "v-doctor", group: "Go to", label: "Doctor", icon: "doctor", keywords: "health checks runner", run: go({ view: "doctor" }) },
      { id: "v-repos", group: "Go to", label: "Repositories", icon: "repos", keywords: "open switch project", run: go({ view: "projects" }) },
    ];
    for (const intent of portfolio.data?.intents ?? []) {
      list.push({
        id: `i-${intent.id}`,
        group: "Intents",
        label: `${intent.id} ${intent.title}`,
        hint: intent.waiting_on ? `waits on ${intent.waiting_on.gate}` : intent.status ?? undefined,
        icon: intent.waiting_on ? "gate" : "intent",
        run: go({ view: "intent", intentId: intent.id, tab: "overview" }),
      });
      for (const { tab, label } of TABS) {
        list.push({
          id: `i-${intent.id}-${tab}`,
          group: "Intents",
          label: `${intent.id} ${label}`,
          hint: intent.title,
          icon: "arrowRight",
          keywords: intent.title,
          run: go({ view: "intent", intentId: intent.id, tab }),
        });
      }
    }
    list.push(
      { id: "a-new", group: "Actions", label: "Start an Intent", icon: "plus", keywords: "new create register", run: () => setOverlay("newIntent", true) },
      { id: "a-refresh", group: "Actions", label: "Refresh from the runner", icon: "refresh", keywords: "reload reconcile", run: () => void refresh() },
      {
        id: "a-theme",
        group: "Actions",
        label: theme === "light" ? "Use the dark theme" : "Use the light theme",
        icon: "sun",
        keywords: "theme appearance colour color",
        run: () => setTheme(theme === "light" ? "dark" : "light"),
      },
      {
        id: "a-density",
        group: "Actions",
        label: density === "compact" ? "Use comfortable spacing" : "Use compact spacing",
        icon: "settings",
        keywords: "density spacing",
        run: () => setDensity(density === "compact" ? "comfortable" : "compact"),
      },
      { id: "a-sidebar", group: "Actions", label: "Show or hide the sidebar labels", icon: "collapse", keywords: "sidebar collapse", run: toggleSidebar },
      { id: "a-keys", group: "Actions", label: "Keyboard shortcuts", icon: "keyboard", keywords: "help keys", run: () => setOverlay("shortcuts", true) },
    );
    return list;
  }, [portfolio.data, navigate, refresh, theme, setTheme, density, setDensity, toggleSidebar, setOverlay]);

  const results = useMemo(() => {
    const ranked = commands
      .map((command) => ({ command, score: score(query, `${command.label} ${command.keywords ?? ""} ${command.hint ?? ""}`) }))
      .filter((item) => item.score > 0)
      // With no query, hide the per-tab jumps: they are for someone who typed an id.
      .filter((item) => query.trim() || !item.command.id.match(/^i-.+-(summary|specification|files|traceability|activity)$/));
    if (query.trim()) ranked.sort((a, b) => b.score - a.score);
    return ranked.slice(0, 14).map((item) => item.command);
  }, [commands, query]);

  const run = (command: Command | undefined) => {
    if (!command) return;
    close();
    command.run();
  };

  return (
    <div className="overlay palette-overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && close()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Search or jump to" ref={root} tabIndex={-1}>
        <div className="palette-input">
          <Icon name="search" size={18} />
          <input
            autoFocus
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={results[active] ? `pal-${results[active]!.id}` : undefined}
            aria-autocomplete="list"
            placeholder="Search Intents, views and actions"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((index) => Math.min(index + 1, results.length - 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((index) => Math.max(index - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                run(results[active]);
              }
            }}
          />
          <kbd>Esc</kbd>
        </div>
        <ul id="palette-list" role="listbox" aria-label="Results" className="palette-list">
          {results.length === 0 && <li className="palette-empty">Nothing matches “{query}”. Try an Intent id such as I003.</li>}
          {results.map((command, index) => (
            <li
              key={command.id}
              id={`pal-${command.id}`}
              role="option"
              aria-selected={index === active}
              className={`palette-item${index === active ? " active" : ""}`}
              onMouseMove={() => setActive(index)}
              onClick={() => run(command)}
            >
              {(index === 0 || results[index - 1]!.group !== command.group) && <span className="palette-group" aria-hidden="true">{command.group}</span>}
              <span className="palette-row">
                <Icon name={command.icon} size={16} className={command.icon === "gate" ? "gate-ic" : undefined} />
                <span className="palette-label">{command.label}</span>
                {command.hint && <span className="palette-hint">{command.hint}</span>}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
