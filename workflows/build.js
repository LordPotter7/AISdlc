/**
 * The Build-stage loop, as a plugin workflow.
 *
 * WHAT THIS IS NOT: an orchestrator. `scripts/aisdlc/orchestration.py` keeps every
 * decision that carries authority — ready-batch selection under dependency and resource
 * locks, worktree creation, evidence validation, reviewer-model independence, retry
 * accounting, merge readiness. None of that moved here and none of it may. ADR-001 says
 * the runner is the lifecycle authority, and a workflow is model-facing text: it can
 * describe an order, it cannot enforce one.
 *
 * WHAT THIS IS: the phase structure of one Build pass, so the model runs the loop the same
 * way twice and a reader can see the shape without reading the runner. Every phase below
 * ends in a runner call, and the runner's answer — not this file — decides what happens
 * next.
 *
 * Invoked as:
 *
 *   Workflow({ name: "aisdlc:build",
 *              args: { intentId: "I001", repositoryRoot: "<abs path>", effort: "normal" } })
 *
 * With no args (a bare slash command), do not call Workflow: tell the user to run
 * `/aisdlc:status <intent>` first, which reports whether the Intent is even in `building`.
 */

const RUNNER = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py"';

export const meta = {
  name: "build",
  description:
    "One pass of the AI SDLC Build stage: select the ready batch, implement each Task, capture evidence, " +
    "register an independent review, and stop at the first refusal.",
  whenToUse:
    "Run by the implementer from /aisdlc:run, once an Intent is in `building` and Gate 3 is approved. " +
    "args carry intentId and repositoryRoot. If invoked with no args, do not call Workflow — tell the user " +
    "to run /aisdlc:status, which reports the single next legal action.",
  phases: [
    {
      title: "Select",
      detail:
        "`ready-tasks` returns the dependency- and lock-safe batch. It is the only source of what may run; " +
        "never widen it because a Task 'looks independent'. A Task missing from the batch is a Task the " +
        "runner is refusing, and the refusal names the reason.",
    },
    {
      title: "Claim",
      detail:
        "`start-task` moves one Task to in_progress. It refuses before the Intent is `building`, and " +
        "refuses a later phase until Gate 4. A refusal here is the gate working, not an obstacle to route around.",
    },
    {
      title: "Implement",
      detail:
        "Write the code. Declare visible commands freely; never author, edit, or weaken a held-out command — " +
        "that is the one instrument this session is not allowed to touch, and the plugin-root guard enforces it.",
    },
    {
      title: "Verify",
      detail:
        "`verify-task` runs the visible and held-out suites in one attempt against one commit and writes the " +
        "manifest. A visible pass with a held-out failure is a specification-conformance finding, not a flake: " +
        "fix the implementation, never the test. Two retries; a third failure blocks the Task.",
    },
    {
      title: "Review",
      detail:
        "Delegate to the aisdlc-verifier agent from a fresh context, then `review-task` with the review file " +
        "and the manifest. The runner checks reviewer independence by model family and refuses a review with " +
        "no recorded reviewer_model. Self-review is void.",
    },
    {
      title: "Integrate",
      detail:
        "`merge-ready`, then `merge`, both pinned to the verified commit. Readiness fails on a stale base, " +
        "altered evidence, or a commit mismatch. Stop at the first refusal and report it — do not continue " +
        "the batch around a Task the runner declined.",
    },
  ],
};

/**
 * The runner calls each phase makes, in order. Kept as data so the phase text above and
 * the commands below cannot drift apart.
 */
export const commands = {
  select: (a) => `${RUNNER} ready-tasks ${a.intentId} --repo "${a.repositoryRoot}"`,
  claim: (a) => `${RUNNER} start-task ${a.taskId} --repo "${a.repositoryRoot}" --actor ${a.actor}`,
  verify: (a) => `${RUNNER} verify-task ${a.taskId} --repo "${a.repositoryRoot}"`,
  review: (a) =>
    `${RUNNER} review-task ${a.taskId} --repo "${a.repositoryRoot}" ` +
    `--review "${a.reviewPath}" --evidence "${a.manifestPath}" --implementer ${a.implementerId}`,
  mergeReady: (a) => `${RUNNER} merge-ready ${a.taskId} --repo "${a.repositoryRoot}" --expected-commit ${a.commit}`,
  merge: (a) => `${RUNNER} merge ${a.taskId} --repo "${a.repositoryRoot}" --expected-commit ${a.commit}`,
};

/**
 * Stop conditions. Each is a refusal the runner can return, and each ends the pass rather
 * than being retried or worked around. Reporting one is a successful outcome for this
 * workflow — an unfinished batch is information, not failure.
 */
export const stopOn = [
  "the Intent is not in `building`",
  "Gate 3 is unapproved, or Gate 4 is unapproved for a later phase",
  "a conformance gap: the visible suite passed and the held-out suite failed",
  "a third failed attempt, which blocks the Task and writes an escalation package",
  "a review whose reviewer model shares a family with the implementer's",
  "evidence that is altered, missing, or pinned to another commit",
  "a verified base more than `merge.max_base_lag` commits behind the integration branch",
];
