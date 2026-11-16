/**
 * Wire contract with the AISDLC bridge (protocol 1.0, shared with the AI Governance app).
 * Payload types mirror `scripts/aisdlc/desktop/projections.py`. Fields the runner may not
 * have recorded are optional or nullable: missing is never rendered as zero or "passed".
 */

export type Freshness = "connecting" | "current" | "updating" | "stale" | "unavailable" | "error";

export interface Omission {
  what: string;
  reason: string;
  count?: number;
}

export interface BridgeErrorBody {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
  retryable?: boolean;
}

export interface Envelope<P = unknown> {
  protocol_version: string;
  schema_version: string;
  kind: string;
  request_id: string;
  project_id: string;
  worktree_id: string;
  generation: number;
  collected_at: string;
  source_fingerprint: string;
  freshness: Freshness;
  omissions: Omission[];
  warnings: string[];
  cursor: string | null;
  scope: Record<string, unknown>;
  error: BridgeErrorBody | null;
  payload: P;
}

export interface GenerationNotification {
  kind: "notification";
  event: "generation";
  project_id: string;
  generation: number;
  source: string;
  at: string;
  freshness: Freshness;
}

export type GateId = "G1" | "G2" | "G3" | "G4" | "G5";
export const GATES: GateId[] = ["G1", "G2", "G3", "G4", "G5"];
export type VerificationClass = "D" | "S" | "J" | "N";

export interface ProjectContext {
  project_id: string;
  worktree_id: string;
  root: string;
  name: string;
  initialized: boolean;
  delivery_dir: string;
  git: { branch: string | null; head: string | null; is_repository: boolean };
  runner: { version: string; module: string };
  configuration: Record<string, unknown>;
  problems: string[];
}

export interface GateRecord {
  status: "undecided" | "approved" | "reopened" | string;
  revision?: number | null;
  approver?: string | null;
  strength?: string | null;
  reason?: string | null;
  at?: string | null;
  /** ADR-010: a gate that needs several different people, and who has approved so far. */
  needed?: number;
  approvals?: string[];
  approvers?: string[];
}

export interface IntentSummary {
  id: string;
  title: string;
  /** The pipeline profile it runs under (ADR-010); null when the pipeline cannot be read. */
  profile?: IntentProfile | null;
  type: string | null;
  mode: "compact" | "full" | null;
  contains_model_behavior: boolean | null;
  trigger_flags: Record<string, boolean>;
  status: string | null;
  status_known: boolean;
  status_since: string | null;
  path: string | null;
  gates: Record<GateId, GateRecord>;
  waiting_on: { gate: GateId; since: string | null } | null;
  /** The stage being worked in: the stage after the last approved gate. Null once completed. */
  working_stage?: StageId | null;
  next_legal_actions: string[];
  requirements: number | null;
  criteria: number | null;
  classes: Partial<Record<VerificationClass, number>>;
  tasks: Record<string, number>;
  open_findings: Record<"critical" | "high" | "medium" | "low", number>;
  lint: { counts: Record<string, number>; passed: boolean } | null;
}

export interface AuditEvent {
  sequence: number | null;
  at: string | null;
  entity_type: string | null;
  entity_id: string | null;
  from: string | null;
  to: string | null;
  event: string | null;
  actor: string | null;
  details?: Record<string, unknown>;
}

export interface ContentionItem {
  resource: string;
  intent: string;
  status: string;
  claimant?: string;
}

export interface Portfolio {
  repository: { name: string; root: string };
  intents: IntentSummary[];
  waiting: { intent: string; title: string; gate: GateId; since: string | null }[];
  kpis: {
    intents: number;
    waiting_gates: number;
    tasks_total: number;
    tasks_verified: number;
    open_high: number;
    contention: number;
  };
  contention: ContentionItem[];
  recent_events: AuditEvent[];
  audit_events: number;
}

export interface RailStage {
  id: StageId;
  title: string;
  folder: string;
  gate: GateId | null;
  status: string;
  state: "done" | "current" | "future";
  present: boolean;
  gate_record: GateRecord | null;
}

export type StageId = "register" | "frame" | "specify" | "plan" | "build" | "converge" | "close";

export interface TaskRow {
  id: string;
  title: string | null;
  phase: string | null;
  status: string;
  attempt: number | null;
  depends_on: string[];
  requirements: string[];
  criteria: string[];
  shared_resources: string[];
  has_holdout: boolean;
  reviewed: boolean;
}

export interface IntentDetail extends IntentSummary {
  rail: RailStage[];
  task_list: TaskRow[];
  artifacts: Record<StageId, string[]>;
}

/** Where a record is written: a file inside the Intent folder and a 1-based line (null for a whole file). */
export interface SourceRef {
  file: string;
  line: number | null;
}

export interface LintFinding {
  rule: string;
  severity: "info" | "warning" | "error";
  location: string;
  message: string;
  source?: SourceRef | null;
}

export interface LintReport {
  passed: boolean;
  fail_on?: string;
  counts: Record<string, number>;
  requirements?: number;
  prioritised?: number;
  quality_coverage?: Record<string, string[]>;
  blocking?: LintFinding[];
  findings?: LintFinding[];
  error?: string;
}

export interface SpecDiff {
  baseline_revision: number | null;
  baseline_captured_at: string | null;
  requirements: { added: string[]; removed: string[]; changed: string[] };
  criteria: { added: string[]; removed: string[]; changed: string[] };
  reprioritised: { requirement: string; from: string | null; to: string | null }[];
  other_artifacts: { added: string[]; removed: string[]; changed: string[] };
  tasks_losing_evidence: { task: string; requirements: string[] }[];
  error?: string;
}

export interface GatePacket {
  intent_id: string;
  title: string;
  gate: GateId;
  question: string;
  generated_at: string;
  intent_status: string | null;
  gate_status: string;
  revision: number;
  previous_approver: string | null;
  artifacts: { path: string; present: boolean; bytes: number | null }[];
  outstanding: string[];
  inherited_controls: Record<string, unknown>;
  evidence: unknown;
  open_findings: { id: string; severity: string; summary?: string }[];
  adversarial_review: {
    applicable: boolean;
    recorded?: boolean;
    current?: boolean;
    decision?: string | null;
    reviewer_model?: string | null;
    reviewer_agent_id?: string | null;
    findings?: number;
    stale_reason?: string | null;
  };
  specification_quality: LintReport | null;
  specification_diff: SpecDiff | null;
  cost: unknown;
}

export interface GatePacketPayload {
  packet: GatePacket;
  markdown: string;
}

export interface Requirement {
  id: string;
  source?: SourceRef | null;
  statement: string;
  acceptance_criteria: string[];
  priority?: "must" | "should" | "could" | "wont";
  rationale?: string;
  sources?: string[];
  category?: string;
  measure?: { metric: string; target: number | string; unit?: string; condition?: string };
  compliance?: { framework: string; control: string }[];
  data_classification?: string;
  personal_data?: boolean;
  wcag?: { version: string; level: string };
}

export interface Criterion {
  id: string;
  source?: SourceRef | null;
  verification_class: VerificationClass;
  statement: string;
  check?: string;
  given?: string;
  when?: string;
  then?: string;
  contract_operations?: string[];
}

export interface SpecPayload {
  present: boolean;
  requirements?: Requirement[];
  criteria?: Criterion[];
  success_criteria?: { id: string; statement: string; acceptance_criteria?: string[]; brief_measure?: string }[];
  glossary?: { term: string; definition: string }[];
  lint?: LintReport | null;
  validation_errors?: string[];
  contracts?: {
    operations: { reference: string; contract: string; name: string; covered_by: string[]; excluded_reason: string | null }[];
    errors: string[];
    breaking_changes: { api: string; operation: string; change: string; baseline_intent: string; accepted_by: string | null }[];
    contention: ContentionItem[];
  };
  threat_model?: {
    boundaries: { id: string; name: string; not_applicable?: { category: string; reason: string }[] }[];
    threats: {
      id: string;
      source?: SourceRef | null;
      boundary: string;
      stride: string;
      description: string;
      disposition: string;
      mitigated_by?: string[];
      verified_by?: string[];
      inherited_from?: string;
      accepted_by?: string;
      reason?: string;
    }[];
  } | null;
  stride_gaps?: Record<string, string[]>;
  inherited_threats?: { lesson_id: string; stride: string; rule?: string }[];
}

export type TraceLane = "success" | "requirement" | "criterion" | "task" | "evidence";

export interface TraceNode {
  id: string;
  lane: TraceLane;
  label: string;
  priority?: string | null;
  category?: string | null;
  deferred?: boolean;
  changed?: boolean;
  added?: boolean;
  verification_class?: VerificationClass;
  status?: string;
  attempt?: number | null;
  stale_requirements?: string[];
  passed?: boolean;
  commit?: string;
  holdout?: boolean;
  stale?: boolean;
  review_decision?: string | null;
  reviewer_model?: string | null;
  implementer_model?: string | null;
}

export interface TraceGap {
  id: string;
  kind: string;
  message: string;
}

export interface TracePayload {
  present: boolean;
  nodes: TraceNode[];
  edges: { from: string; to: string; kind: string }[];
  gaps: TraceGap[];
  gap_counts?: Record<string, number>;
  spec_diff?: SpecDiff | null;
}

export interface StoryEnforcement {
  kind: "refused" | "gate_approved" | "gate_reopened" | "adversarial_review" | "inherited" | "independent_review" | "task_failed" | string;
  text: string;
  at?: string | null;
  reasons?: string[];
}

export interface StoryArtifact {
  path: string;
  provenance: "verified_as_approved" | "changed_since_approval" | "current" | string;
  note: string;
  content: string;
  truncated: boolean;
}

export interface StoryChapter {
  id: StageId;
  title: string;
  gate: GateId | null;
  claim: string;
  reached: boolean;
  started_at: string | null;
  ended_at: string | null;
  event_count: number;
  enforcement: StoryEnforcement[];
  artifact: StoryArtifact | null;
  tasks: { task: string; decision: string | null; reviewer_model: string | null; implementer_model: string | null; passed: boolean | null; attempt: number | null; commit: string | null }[] | null;
}

export interface StoryPayload {
  intent: { id: string; title: string; status: string | null; mode: string | null; type: string | null };
  chapters: StoryChapter[];
  timeline: (AuditEvent & { stage: StageId; index: number })[];
  complete: boolean;
}

export interface EventsPayload {
  events: AuditEvent[];
  matched_total: number;
  next_cursor: string | null;
}

export interface DiagnosticsPayload {
  context: ProjectContext;
  python: { version: string; executable: string };
  doctor: { status: string; checks: { check: string; status: string; detail: string }[]; error?: string };
  version_consistency: string[];
  fingerprint_truncated: boolean;
}

export interface OperationPreview {
  name: string;
  title: string;
  writes: boolean;
  effect: string;
  command: string;
  may_change: string[];
  base_hashes: Record<string, string | null>;
  token: string;
  expires_in_seconds: number;
  draft?: DraftReport;
}

export interface OperationResult {
  name: string;
  exit_code: number;
  succeeded: boolean;
  output: unknown;
  error_text: string | null;
}

export interface GuideStage {
  id: StageId;
  title: string;
  folder: string;
  gate: GateId | null;
  owner: string;
  what: string;
  your_part: string;
  authored: { file: string; template?: string; schema?: string; note?: string }[];
  command: string;
}

export interface GuideTerm {
  id: string;
  term: string;
  definition: string;
}

export interface LintRule {
  code: string;
  severity: "info" | "warning" | "error";
  title: string;
  explanation: string;
  fix: string;
}

export interface LearnPayload {
  stages: GuideStage[];
  gates: Partial<Record<GateId, string>>;
  terms: GuideTerm[];
  lint_rules: LintRule[];
  templates: string[];
  intents: { id: string; title: string; status: string | null; mode: string | null; working_stage: StageId | null; waiting_on: IntentSummary["waiting_on"] }[];
  facts: { intents: number; completed: number; approved_gates: number; waiting: number };
}

export interface ArtifactPayload {
  scope: "intent" | "template" | "schema";
  intent_id: string | null;
  path: string;
  language: "yaml" | "json" | "markdown" | "text";
  text: string;
  bytes: number;
  lines: number;
  truncated: boolean;
  sha256: string;
  owner: "you" | "runner" | "framework";
  produced_by: string | null;
  locked: { gate: GateId; approver: string | null; at: string | null; command: string } | null;
  /** Whether the runner would accept an edit right now, by the same rule `artifact write` applies. */
  editable: boolean;
  edit_refusal: string | null;
}

/** One problem the runner finds in a draft, on the line it concerns when it names one. */
export interface Diagnostic {
  file: string | null;
  line: number | null;
  column: number | null;
  severity: "error" | "warning" | "info";
  source: "syntax" | "validation" | "lint";
  code: string | null;
  message: string;
}

/** A record the specification defines, where it is written, and what it says. */
export interface SpecIdentifier {
  id: string;
  label: string;
  file: string;
  line: number;
}

/** A key the file's schema allows, with its meaning and allowed values. */
export interface SchemaKey {
  key: string;
  description: string;
  values: string[];
}

/** `artifact.check`: the runner's own checks over a draft, run on a copy. Never writes. */
export interface ArtifactCheck {
  path: string;
  passed: boolean;
  diagnostics: Diagnostic[];
  identifiers: SpecIdentifier[];
  keys: SchemaKey[];
  /** For a Task: the codebase's modules as `affected_paths`, its test commands as checks. */
  suggestions?: { affected_paths?: string[]; verification_commands?: string[] };
}

/** What saving a file would change, and what the runner's checks say about the new content. */
export interface DraftReport {
  diff: string;
  changed: boolean;
  refusal: string | null;
  checks: { validation_errors: string[]; lint: { passed: boolean; counts: Record<string, number>; findings: LintFinding[] } | null } | null;
}

export interface ComposePayload {
  path: string;
  content: string;
  expected_sha: string;
  ids: { requirement: string; criteria: string[] };
}

export interface SuspectLink {
  kind: "evidence" | "plan";
  task: string | null;
  reason: string;
  clear: string;
}

export interface OverviewRow {
  id: string;
  priority: string | null;
  category: string | null;
  statement: string;
  sources: string[];
  criteria: { id: string; class: VerificationClass; statement: string }[];
  tasks: { id: string; status: string }[];
  proven: boolean;
  suspect: SuspectLink[];
}

export interface OverviewPayload {
  intent: Pick<IntentSummary, "id" | "title" | "type" | "mode" | "status" | "working_stage" | "waiting_on" | "open_findings" | "lint" | "tasks">;
  brief: Record<string, string>;
  requirements: OverviewRow[];
  success_criteria: { id: string; statement: string; brief_measure: string | null; criteria: string[] }[];
  coverage: { requirements: number; with_criterion: number; with_task: number; proven: number; suspect: number };
  touches: string[];
  contracts: { path: string; api: string | null; breaking_changes_accepted: number }[];
  contention: { resource: string; intent: string; status: string }[];
  threats: { total: number; by_disposition: Record<string, number>; inherited: string[] };
  plan: { id: string; title: string | null; status: string; requirements: string[] }[];
  moved_since_g2: string[];
}

export type InboxKind = "gate" | "contention" | "blocked" | "finding" | "lint" | "suspect";

export interface InboxItem {
  kind: InboxKind;
  intent: string;
  title: string;
  since: string | null;
  summary: string;
  gate?: GateId;
  resource?: string;
  intents?: string[];
  task?: string;
  requirements?: string[];
}

export interface InboxPayload {
  items: InboxItem[];
  counts: Record<InboxKind, number>;
}

/** One module of the codebase, and the Intents whose Tasks declare paths inside it. */
export interface CodebaseModule {
  path: string;
  kind: "code" | "tests" | "docs" | "infrastructure" | "tooling" | "lifecycle";
  files: number;
  code_files: number;
  languages: string[];
  test_files: number;
  owners: string[];
  manifests: string[];
  intents: { intent: string; title: string | null; status: string | null; active: boolean; tasks: string[]; paths: string[] }[];
  /** More than one open Intent changes this module. */
  contended: boolean;
}

export interface TestSuite {
  framework: string;
  ecosystem: string;
  root: string;
  command: string;
  test_files: number | null;
  evidence: string[];
}

export interface AdoptionStep {
  id: "init" | "understand" | "baseline" | "intent";
  title: string;
  done: boolean;
  state?: string | null;
  detail: string;
  command: string;
  handoff: string;
}

export interface BaselineRecord {
  id: string;
  label: string | null;
  captured_at: string | null;
  commit: string | null;
  probes: { name: string; command: string; exit_code: number | null; value: number | null }[];
}

/** `codebase.inspect`: the repository read from git at HEAD, never stored, and how adoption stands. */
export interface CodebasePayload {
  managed: boolean;
  inventory: {
    commit: string | null;
    tracked_files: number;
    languages: { language: string; files: number; share: number; code: boolean }[];
    manifests: { path: string; ecosystem: string }[];
    test_suites: TestSuite[];
    ci: { system: string; path: string }[];
    owners: { file: string | null; rules: number };
    root_files: number;
    entry_points: { kind: string; path: string; detail?: string }[];
    proposed_probes: { name: string; kind: string; command: string; timeout_seconds: number }[];
    notes: string[];
  };
  modules: CodebaseModule[];
  footprint: Record<string, { modules: string[]; owners: string[]; shared_with: string[] }>;
  unmapped: { intent: string; task: string; path: string }[];
  adoption: { steps: AdoptionStep[]; complete: boolean };
  understanding: {
    status: "absent" | "fresh" | "drifting" | "stale" | "invalid";
    source?: string | null;
    pinned_commit?: string | null;
    recorded_at?: string | null;
    anchored_paths?: number;
    changed_paths?: string[];
    detail?: string;
  };
  baselines: BaselineRecord[];
}

export type IntentPriority = "high" | "normal" | "low";

export interface PortfolioIntent {
  id: string;
  title: string;
  status: string | null;
  active: boolean;
  /** The latest `priority_set` decision; null means never set (normal). */
  priority: { priority: IntentPriority; by: string | null; reason: string | null; at: string | null } | null;
  program: string | null;
  /** Programme predecessors that have neither completed nor been waived. */
  blocked_by: string[];
  proof: { requirements: number; proven: number; suspect: number };
  modules: string[];
  shared_with: string[];
}

export interface ProgramEdge {
  from: string;
  to: string;
  waived: boolean;
  waived_by: string | null;
  waiver_reason: string | null;
  satisfied: boolean;
}

/** `portfolio.inspect`: every Intent in priority order, programmes, work in progress, proof. */
export interface PortfolioPayload {
  intents: PortfolioIntent[];
  programs: { id: string; title: string | null; owner: string | null; members: string[]; edges: ProgramEdge[]; closed: number; proof: { requirements: number; proven: number } }[];
  wip: { building: string[]; ready: string[]; limit: number | null; full: boolean };
  proof: { requirements: number; proven: number; intents: number };
  overlaps: { module: string; intents: string[] }[];
}

/** One default a profile relaxes, with the waiver that allows it (ADR-010). */
export interface LoosenedRule {
  rule: string;
  from?: unknown;
  to: unknown;
  waiver?: { approved_by: string | null; reason: string | null; approved_at: string | null };
}

export interface GatePolicy {
  approvals: number;
  approvers: string[];
}

export interface PipelineProfile {
  name: string;
  description: string;
  modes: ("compact" | "full")[];
  required_artifacts: Record<string, string[]>;
  fail_on: "warning" | "error" | "never";
  rules: Record<string, "info" | "warning" | "error">;
  gates: Record<GateId, GatePolicy>;
  holdout_waiver: "allowed" | "forbidden";
  loosened: LoosenedRule[];
}

/** `pipeline.inspect`: the constitution, `standard`, every profile, and who runs under which. */
export interface PipelinePayload {
  constitution: string[];
  present: boolean;
  error: string | null;
  sha256: string | null;
  default: string;
  by_type: Record<string, string>;
  profiles: Record<string, PipelineProfile>;
  intents: { id: string; title: string; type: string | null; status: string | null; profile: string; implicit: boolean; loosened: string[]; stricter: string[] }[];
  pinned_counts: Record<string, number>;
}

/** The profile an Intent runs under, as the Intent summary reports it. */
export interface IntentProfile {
  name: string;
  implicit: boolean;
  modes: string[];
  fail_on: string;
  gates: Record<string, GatePolicy>;
  loosened: { rule: string; to: unknown; approved_by?: string | null; reason?: string | null; approved_at?: string | null }[];
}
