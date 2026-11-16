/**
 * App state: which repository is open, where the user is, and how fresh the data is.
 * Lifecycle data itself is never stored here as truth — views fetch projections from the
 * bridge and refetch when the project's generation moves.
 */
import { create } from "zustand";
import type { Freshness, GenerationNotification, ProjectContext } from "@/lib/contract";
import { errorMessage, getTransport, type BridgeStatus, type RegistryEntry } from "@/lib/ipc";

export type View = "projects" | "learn" | "inbox" | "cockpit" | "portfolio" | "codebase" | "intent" | "story" | "doctor" | "pipeline";
export type IntentTab = "overview" | "summary" | "specification" | "files" | "traceability" | "activity";
export type Theme = "dark" | "light" | "system";
export type Density = "comfortable" | "compact";
export type ToastTone = "ok" | "bad" | "info" | "gate";

/** A short message that echoes what the runner did, in its words. */
export interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  detail?: string;
}

export interface Route {
  view: View;
  intentId?: string;
  tab?: IntentTab;
}

/** A file the read-only viewer shows: an Intent artifact, or a framework template or schema. */
export interface ViewerTarget {
  scope: "intent" | "template" | "schema";
  intentId?: string;
  path: string;
  line?: number | null;
  /** Create the file (it does not exist yet), starting from this framework template. */
  create?: { template: string };
}

interface OpenProject {
  registryId: string;
  projectId: string;
  context: ProjectContext;
  generation: number;
  freshness: Freshness;
  lastEventAt: string | null;
}

interface StudioState {
  route: Route;
  history: Route[];
  registry: RegistryEntry[];
  project: OpenProject | null;
  bridge: BridgeStatus;
  error: string | null;
  opening: boolean;
  theme: Theme;
  refreshNonce: number;
  viewer: ViewerTarget | null;
  openFile(target: ViewerTarget): void;
  closeFile(): void;
  navigate(route: Route): void;
  back(): void;
  loadRegistry(): Promise<void>;
  addRepository(): Promise<void>;
  open(entry: RegistryEntry): Promise<void>;
  applyNotification(note: GenerationNotification): void;
  setBridge(status: BridgeStatus): void;
  refresh(): Promise<void>;
  setTheme(theme: Theme): void;
  clearError(): void;
  density: Density;
  setDensity(density: Density): void;
  sidebarCollapsed: boolean;
  toggleSidebar(): void;
  paletteOpen: boolean;
  shortcutsOpen: boolean;
  newIntentOpen: boolean;
  setOverlay(which: "palette" | "shortcuts" | "newIntent", open: boolean): void;
  toasts: Toast[];
  toast(toast: Omit<Toast, "id">): void;
  dismissToast(id: number): void;
}

const PREFS = "aisdlc.prefs";

interface Prefs {
  theme?: Theme;
  density?: Density;
  sidebarCollapsed?: boolean;
}

/** Per-viewer conveniences only; the app works the same when storage is unavailable. */
function loadPrefs(): Prefs {
  try {
    const prefs = JSON.parse(localStorage.getItem(PREFS) ?? "{}");
    return typeof prefs === "object" && prefs ? prefs : {};
  } catch {
    return {};
  }
}

function savePrefs(patch: Prefs) {
  try {
    localStorage.setItem(PREFS, JSON.stringify({ ...loadPrefs(), ...patch }));
  } catch {
    /* preference storage is optional */
  }
}

function loadTheme(): Theme {
  const theme = loadPrefs().theme;
  return theme === "light" || theme === "system" ? theme : "dark";
}

export function applyDensity(density: Density) {
  document.documentElement.dataset.density = density;
}

let toastSeq = 0;

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
}

export const useStudio = create<StudioState>((set, get) => ({
  route: { view: "projects" },
  history: [],
  registry: [],
  project: null,
  bridge: { state: "connecting" },
  error: null,
  opening: false,
  theme: loadTheme(),
  refreshNonce: 0,
  viewer: null,

  openFile(target) {
    set({ viewer: target });
  },
  closeFile() {
    set({ viewer: null });
  },

  navigate(route) {
    set((state) => ({ route, history: [...state.history.slice(-30), state.route] }));
  },
  back() {
    const history = get().history;
    const previous = history[history.length - 1];
    if (previous) set({ route: previous, history: history.slice(0, -1) });
  },

  async loadRegistry() {
    try {
      set({ registry: await getTransport().registryList() });
    } catch (err) {
      set({ error: errorMessage(err) });
    }
  },

  async addRepository() {
    try {
      const added = await getTransport().registryAdd();
      if (added) {
        await get().loadRegistry();
        await get().open(added);
      }
    } catch (err) {
      set({ error: errorMessage(err) });
    }
  },

  async open(entry) {
    set({ opening: true, error: null });
    try {
      const result = await getTransport().openProject(entry.id, true);
      if (result.envelope.error) throw new Error(result.envelope.error.message);
      const payload = result.envelope.payload as { context: ProjectContext };
      set({
        project: {
          registryId: result.registry_id,
          projectId: result.project_id,
          context: payload.context,
          generation: result.envelope.generation,
          freshness: result.envelope.freshness,
          lastEventAt: null,
        },
        route: { view: "cockpit" },
        history: [],
      });
      await get().loadRegistry();
    } catch (err) {
      set({ error: errorMessage(err) });
    } finally {
      set({ opening: false });
    }
  },

  applyNotification(note) {
    const project = get().project;
    if (!project || note.project_id !== project.projectId) return;
    // Apply only newer generations: a late notification must not roll a view back.
    if (note.generation <= project.generation && note.freshness === project.freshness) return;
    set({
      project: {
        ...project,
        generation: Math.max(project.generation, note.generation),
        freshness: note.freshness,
        lastEventAt: note.at,
      },
    });
  },

  setBridge(status) {
    set({ bridge: status });
  },

  async refresh() {
    const project = get().project;
    if (!project) return;
    try {
      const envelope = await getTransport().bridgeRequest<{ changed: boolean; generation: number }>("project.reconcile", {
        project_id: project.projectId,
        hint: "manual",
      });
      set((state) => ({
        project: state.project ? { ...state.project, generation: envelope.generation, freshness: envelope.freshness } : null,
        // A manual refresh refetches every view even when nothing on disk moved.
        refreshNonce: state.refreshNonce + 1,
      }));
    } catch (err) {
      set({ error: errorMessage(err) });
    }
  },

  setTheme(theme) {
    savePrefs({ theme });
    applyTheme(theme);
    set({ theme });
  },

  density: loadPrefs().density === "compact" ? "compact" : "comfortable",
  setDensity(density) {
    savePrefs({ density });
    applyDensity(density);
    set({ density });
  },
  sidebarCollapsed: loadPrefs().sidebarCollapsed === true,
  toggleSidebar() {
    const sidebarCollapsed = !get().sidebarCollapsed;
    savePrefs({ sidebarCollapsed });
    set({ sidebarCollapsed });
  },
  paletteOpen: false,
  shortcutsOpen: false,
  newIntentOpen: false,
  setOverlay(which, open) {
    set(which === "palette" ? { paletteOpen: open } : which === "shortcuts" ? { shortcutsOpen: open } : { newIntentOpen: open });
  },
  toasts: [],
  toast(toast) {
    const id = ++toastSeq;
    set((state) => ({ toasts: [...state.toasts.slice(-3), { ...toast, id }] }));
    setTimeout(() => get().dismissToast(id), toast.tone === "bad" ? 9000 : 5000);
  },
  dismissToast(id) {
    set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) }));
  },

  clearError() {
    set({ error: null });
  },
}));
