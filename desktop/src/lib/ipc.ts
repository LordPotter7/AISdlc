/**
 * Host transport. Inside the Tauri webview every call goes through the native host, which
 * supervises the Python bridge. Anywhere else (a plain browser, tests) a replay transport
 * serves envelopes recorded from the real bridge over the demo repository
 * (`tools/record_studio_fixtures.py`). Replay can preview an operation but never pretends
 * to execute one.
 */
import type { Envelope, GenerationNotification, OperationPreview } from "./contract";
import type demo from "../fixtures/demo.json";

export interface RegistryEntry {
  id: string;
  root: string;
  label: string;
  added_at: string;
  last_opened: string | null;
  cursors?: unknown | null;
  missing?: boolean;
}

export interface Discovered {
  root: string;
  label: string;
  has_aisdlc: boolean;
  has_git: boolean;
  registered: boolean;
}

export interface BridgeStatus {
  state: "connecting" | "current" | "stale" | "unavailable" | "error";
  detail?: string;
  at?: string;
  python?: string | null;
  python_source?: string | null;
  python_skipped?: string[];
  repo?: string | null;
  pid?: number | null;
  restarts?: number;
  engine?: { name: string; version: string; python: string } | null;
}

export interface OpenProjectResult {
  registry_id: string;
  previous_registry_id: string;
  project_id: string;
  root: string;
  envelope: Envelope;
  watched: string[];
}

export interface AppInfo {
  app_version: string;
  platform?: string;
  arch?: string;
  data_dir: string;
  python: string | null;
  python_source?: string | null;
  repo?: string | null;
  engine?: { name: string; version: string; python: string } | null;
  bridge?: { state: string; detail: string; pid: number | null; restarts: number };
}

export interface HostError {
  code: string;
  message: string;
}

export function isHostError(value: unknown): value is HostError {
  return !!value && typeof value === "object" && typeof (value as HostError).code === "string";
}

export function errorMessage(err: unknown): string {
  if (isHostError(err)) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

export type BridgeOp =
  | "hello"
  | "project.snapshot"
  | "project.reconcile"
  | "project.subscribe"
  | "project.unsubscribe"
  | "project.cursors"
  | "learn.inspect"
  | "artifact.read"
  | "artifact.check"
  | "authoring.compose"
  | "overview.inspect"
  | "inbox.inspect"
  | "codebase.inspect"
  | "portfolio.inspect"
  | "pipeline.inspect"
  | "intent.inspect"
  | "gate.packet"
  | "spec.inspect"
  | "trace.inspect"
  | "story.inspect"
  | "events.page"
  | "diagnostics.inspect"
  | "operation.preview"
  | "operation.execute"
  | "operation.cancel";

export type Unlisten = () => void;

export interface Transport {
  readonly mode: "native" | "replay";
  registryList(): Promise<RegistryEntry[]>;
  registryAdd(root?: string): Promise<RegistryEntry | null>;
  registryRemove(id: string): Promise<RegistryEntry>;
  bridgeStatus(): Promise<BridgeStatus>;
  bridgeRequest<P = unknown>(op: BridgeOp, params: Record<string, unknown>): Promise<Envelope<P>>;
  bridgeRestart(): Promise<BridgeStatus>;
  openProject(registryId: string, activate?: boolean): Promise<OpenProjectResult>;
  closeProject(projectId: string): Promise<{ project_id: string; cursors_saved: boolean }>;
  saveExportDialog(defaultName?: string): Promise<string | null>;
  appInfo(): Promise<AppInfo>;
  onNotification(handler: (n: GenerationNotification) => void): Promise<Unlisten>;
  onStatus(handler: (s: BridgeStatus) => void): Promise<Unlisten>;
}

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

// ---------------------------------------------------------------------------
// Native transport
// ---------------------------------------------------------------------------
export class TauriTransport implements Transport {
  readonly mode = "native" as const;
  private async api() {
    const [core, event] = await Promise.all([import("@tauri-apps/api/core"), import("@tauri-apps/api/event")]);
    return { invoke: core.invoke, listen: event.listen };
  }
  async registryList() {
    return (await this.api()).invoke<RegistryEntry[]>("registry_list");
  }
  async registryAdd(root?: string) {
    return (await this.api()).invoke<RegistryEntry | null>("registry_add", root ? { root } : {});
  }
  async registryRemove(id: string) {
    return (await this.api()).invoke<RegistryEntry>("registry_remove", { id });
  }
  async bridgeStatus() {
    return (await this.api()).invoke<BridgeStatus>("bridge_status");
  }
  async bridgeRequest<P>(op: BridgeOp, params: Record<string, unknown>) {
    return (await this.api()).invoke<Envelope<P>>("bridge_request", { op, params });
  }
  async bridgeRestart() {
    return (await this.api()).invoke<BridgeStatus>("bridge_restart");
  }
  async openProject(registryId: string, activate = true) {
    return (await this.api()).invoke<OpenProjectResult>("open_project", { registryId, activate });
  }
  async closeProject(projectId: string) {
    return (await this.api()).invoke<{ project_id: string; cursors_saved: boolean }>("close_project", { projectId });
  }
  async saveExportDialog(defaultName?: string) {
    return (await this.api()).invoke<string | null>("save_export_dialog", defaultName ? { defaultName } : {});
  }
  async appInfo() {
    return (await this.api()).invoke<AppInfo>("app_info");
  }
  async onNotification(handler: (n: GenerationNotification) => void) {
    return (await this.api()).listen<GenerationNotification>("bridge://notification", (e) => handler(e.payload));
  }
  async onStatus(handler: (s: BridgeStatus) => void) {
    return (await this.api()).listen<BridgeStatus>("bridge://status", (e) => handler(e.payload));
  }
}

// ---------------------------------------------------------------------------
// Replay transport: real envelopes recorded from the bridge over the demo repository
// ---------------------------------------------------------------------------
type Recorded = typeof demo;
type RecordedIntent = Recorded["intents"][keyof Recorded["intents"]];

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export class ReplayTransport implements Transport {
  readonly mode = "replay" as const;
  private loaded: Recorded | null;
  private loading: Promise<Recorded> | null = null;

  /** Pass recorded data directly (tests), or let it load on first use (browser preview). */
  constructor(data: Recorded | null = null, private readonly latency = 80) {
    this.loaded = data;
  }

  /** The recording is loaded lazily so the native build never carries it in its main bundle. */
  private async ensure(): Promise<Recorded> {
    if (this.loaded) return this.loaded;
    this.loading ??= import("../fixtures/demo.json").then((module) => (this.loaded = module.default as Recorded));
    return this.loading;
  }

  private get data(): Recorded {
    if (!this.loaded) throw new Error("replay data not loaded");
    return this.loaded;
  }

  private async wait<T>(value: T | (() => T)): Promise<T> {
    await this.ensure();
    const resolved = typeof value === "function" ? (value as () => T)() : value;
    return new Promise((resolve) => setTimeout(() => resolve(resolved), this.latency));
  }

  private entry(): RegistryEntry {
    return {
      id: this.data.open.project_id,
      root: this.data.open.payload.context.root,
      label: "orders-service (recorded demo)",
      added_at: this.data.open.collected_at,
      last_opened: this.data.open.collected_at,
    };
  }

  private error<P>(op: string, code: string, message: string): Envelope<P> {
    const base = clone(this.data.hello) as unknown as Envelope<P>;
    return { ...base, kind: "error", request_id: op, freshness: "error", error: { code, message }, payload: {} as P };
  }

  async registryList() {
    return this.wait(() => [this.entry()]);
  }
  async registryAdd() {
    return this.wait(null);
  }
  async registryRemove() {
    return this.wait(() => this.entry());
  }
  async bridgeStatus(): Promise<BridgeStatus> {
    return this.wait(() => ({
      state: "current" as const,
      detail: "Replaying envelopes recorded from the real bridge over the demo repository",
      python: this.data.hello.payload.engine.python,
      python_source: "recorded",
      engine: this.data.hello.payload.engine,
    }));
  }
  async bridgeRestart() {
    return this.bridgeStatus();
  }
  async openProject(): Promise<OpenProjectResult> {
    return this.wait(() => ({
      registry_id: this.data.open.project_id,
      previous_registry_id: this.data.open.project_id,
      project_id: this.data.open.project_id,
      root: this.data.open.payload.context.root,
      envelope: clone(this.data.open) as unknown as Envelope,
      watched: [],
    }));
  }
  async closeProject(projectId: string) {
    return this.wait({ project_id: projectId, cursors_saved: false });
  }
  async saveExportDialog() {
    return this.wait(null);
  }
  async appInfo(): Promise<AppInfo> {
    return this.wait(() => ({
      app_version: "0.2.0 (browser preview)",
      data_dir: "(none: browser preview)",
      python: this.data.hello.payload.engine.python,
      python_source: "recorded",
      engine: this.data.hello.payload.engine,
    }));
  }
  async onNotification() {
    return () => undefined;
  }
  async onStatus() {
    return () => undefined;
  }

  async bridgeRequest<P>(op: BridgeOp, params: Record<string, unknown>): Promise<Envelope<P>> {
    await this.ensure();
    const intentId = typeof params.intent_id === "string" ? params.intent_id : "";
    const intent = (this.data.intents as Record<string, RecordedIntent>)[intentId];
    const pick = (value: unknown) => this.wait(clone(value) as Envelope<P>);
    switch (op) {
      case "hello":
        return pick(this.data.hello);
      case "project.snapshot":
        return pick(this.data.snapshot);
      case "project.reconcile":
      case "project.subscribe":
      case "project.unsubscribe":
      case "project.cursors":
        return pick({ ...this.data.snapshot, kind: "project", payload: { changed: false, generation: this.data.snapshot.generation } });
      case "diagnostics.inspect":
        return pick(this.data.diagnostics);
      case "learn.inspect":
        return pick(this.data.learn);
      case "inbox.inspect":
        return pick(this.data.inbox);
      case "codebase.inspect":
        return pick(this.data.codebase);
      case "portfolio.inspect":
        return pick(this.data.portfolio);
      case "pipeline.inspect":
        return pick(this.data.pipeline);
      case "overview.inspect":
        if (!intent) return this.wait(this.error<P>(op, "unknown_intent", `no Intent folder for ${intentId}`));
        return pick(intent.overview);
      case "artifact.read": {
        const path = String(params.path ?? "");
        const scope = String(params.scope ?? "intent");
        const recorded =
          scope === "intent"
            ? (intent?.files as Record<string, unknown> | undefined)?.[path]
            : (this.data.framework_files as Record<string, unknown>)[`${scope}:${path}`];
        if (!recorded) return this.wait(this.error<P>(op, "not_recorded", `the demo recording has no ${path}`));
        return pick(recorded);
      }
      case "artifact.check": {
        // The recording holds each file's check as it is on disk; a draft is checked the same way.
        const recorded = (intent?.checks as Record<string, unknown> | undefined)?.[String(params.path ?? "")];
        if (!recorded) return this.wait(this.error<P>(op, "not_recorded", `the demo recording has no check for ${String(params.path)}`));
        return pick(recorded);
      }
      case "events.page":
        return pick(intent ? intent.events : this.data.events);
      case "intent.inspect":
      case "spec.inspect":
      case "trace.inspect":
      case "story.inspect": {
        if (!intent) return this.wait(this.error<P>(op, "unknown_intent", `no Intent folder for ${intentId}`));
        const key = op.split(".")[0] as "intent" | "spec" | "trace" | "story";
        return pick(intent[key]);
      }
      case "gate.packet": {
        const gate = String(params.gate ?? "") as keyof RecordedIntent["gates"];
        const packet = intent?.gates?.[gate];
        if (!packet) return this.wait(this.error<P>(op, "not_recorded", `the demo has no ${String(gate)} packet for ${intentId}`));
        return pick(packet);
      }
      case "operation.preview": {
        const preview: OperationPreview = {
          name: String(params.name),
          title: String(params.name).replace(/_/g, " "),
          writes: true,
          effect: "Browser preview: the command below would run in the desktop app.",
          command: `ai-sdlc ${String(params.name).replace(/_/g, "-")} …`,
          may_change: [],
          base_hashes: {},
          token: "replay",
          expires_in_seconds: 600,
        };
        return this.wait({ ...(clone(this.data.hello) as unknown as Envelope<P>), kind: "operation", payload: preview as P });
      }
      case "operation.execute":
        return this.wait(this.error<P>(op, "replay_mode", "This is a recorded demo in a browser. Operations run only in the desktop app."));
      case "authoring.compose":
        return this.wait(this.error<P>(op, "replay_mode", "Composing runs the runner, so it works only in the desktop app."));
      case "operation.cancel":
        return pick({ ...this.data.hello, kind: "operation", payload: { cancelled: true } });
    }
  }
}

let current: Transport | null = null;

export function getTransport(): Transport {
  if (!current) current = isTauri() ? new TauriTransport() : new ReplayTransport();
  return current;
}

export function setTransport(transport: Transport | null) {
  current = transport;
}
