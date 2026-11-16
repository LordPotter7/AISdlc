# Standing regression suites

Cases promoted out of a closed Intent live here, outside the Intent that produced them. Stage 2 reads the registry and inherits every suite whose trigger matches the Intent's flags. Inheritance is automatic; declining a suite is an escalation with a written reason recorded in the specification.

Each suite directory holds `suite.yaml` (verification class, trigger, owner, threshold) and `cases/` (one file per case). Adding a case is allowed at any time. Removing or editing one requires a recorded reason, because the suite is the measuring instrument.

Register a suite with the runner so its hash is pinned:

```text
ai-sdlc dataset register regression/tool-call-injection .ai-sdlc/evals/regression/tool-call-injection --owner security --version v1 --trigger has_side_effect
```
