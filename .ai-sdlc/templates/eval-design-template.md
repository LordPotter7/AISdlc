# Eval Design: {{INTENT_ID}}

Use this artifact when `contains_model_behavior` is true or any criterion uses Class S, J, or N.

## {{EVAL_ID}} — {{EVAL_NAME}} (Class {{S_OR_J_OR_N}})

- Owner: {{DATASET_OWNER}}
- Dataset and version: {{DATASET_PATH_AND_VERSION}}
- Dataset hash: {{SHA256}}
- Metric: {{METRIC}}
- Threshold operator: {{GREATER_THAN_OR_EQUAL|LESS_THAN_OR_EQUAL|EQUAL}}
- Threshold value: {{PREDECLARED_VALUE}}
- Minimum sample size: {{MINIMUM_SAMPLE_SIZE}}
- Tolerance band: {{TOLERANCE_BAND}}
- Model identifier: {{MODEL_ID}}
- Prompt version and hash: {{PROMPT_VERSION_AND_HASH}}
- Temperature: {{TEMPERATURE}}
- Seed: {{SEED_OR_PROVIDER_UNSUPPORTED}}
- Runs: 3 for S/J; declared suite run for N

For Class J, record the judge model, prompt hash, human-labeled calibration set, and required agreement of at least 0.85 (the runner's floor; a repository may raise it, never lower it). For Class N, define attacker success precisely and require zero successes.
