"""Repository configuration, as plain dataclasses.

This was a Pydantic model. Pydantic is a compiled extension, and the plugin has to run
under any stock `python3` with no install step, so the three things the models actually
used are implemented here instead: reject unknown keys, apply numeric bounds, and build
nested sections from a mapping. `load_config` still returns a `Config` and every call
site still reads attributes off it, so nothing outside this module changed.

The bounds live in each field's `metadata` — `{"ge": 1024}` reads the same as
`Field(ge=1024)` did, and `_build` is the single place that enforces them.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from .vendor import yaml

# The judge-agreement floor the framework will not run below, independent of what a
# repository writes in its own config. 2026 practice puts human-agreement validation at
# >0.85; a repository may raise this, never lower it.
JUDGE_AGREEMENT_FLOOR = 0.85


class ConfigError(ValueError):
    """Raised when `.ai-sdlc/config.yaml` is invalid."""


# Settings this framework used to have. A repository scaffolded by an older runner still
# carries them, and refusing the whole file would brick it on upgrade — which is exactly
# what happened to the first repository upgraded past ADR-007.
#
# They are *ignored*, not accepted: nothing reads them, `doctor` reports them so a reader
# knows they are inert, and `init --force` rewrites the file without them. Unknown keys
# that are not on this list are still refused, because a misspelled setting that is
# silently ignored looks exactly like a setting that is being honoured.
#
# Keyed by section, with "" for the top level.
RETIRED_SETTINGS: dict[str, dict[str, str]] = {
    "": {"runner": "ADR-007: the runner pin went with bare-name PATH resolution"},
    "evidence": {
        "local_authority": "ADR-007: evidence has one kind now",
        "authoritative_provider": "ADR-007: evidence has one kind now",
        "require_attestation": "ADR-007: signing needed a CI workload identity",
        "attestation_provider": "ADR-007: signing needed a CI workload identity",
    },
    "scale": {
        "repository_id": "ADR-007: federation was removed with CI signing",
        "federation_enabled": "ADR-007: federation was removed with CI signing",
        "index_enabled": "ADR-007: the derived SQLite index was removed",
    },
}


def retired_settings(raw: dict[str, Any]) -> list[str]:
    """Retired keys present in a parsed config, as `section.key` strings."""
    found: list[str] = []
    for key in raw:
        if key in RETIRED_SETTINGS.get("", {}):
            found.append(key)
    for section, keys in RETIRED_SETTINGS.items():
        if not section:
            continue
        block = raw.get(section)
        if isinstance(block, dict):
            found.extend(f"{section}.{key}" for key in block if key in keys)
    return sorted(found)


@dataclass(frozen=True)
class RedactionRule:
    """One repository-declared credential shape."""

    name: str = ""
    pattern: str = ""


@dataclass(frozen=True)
class EvidenceConfig:
    retain_failed_attempts: bool = True
    # W3.7. Command output larger than this is content-addressed into the offload store
    # and referenced from the manifest by hash rather than inlined in the attempt.
    max_payload_bytes: int = field(default=1_048_576, metadata={"ge": 1024})
    # R3.3. Captured output is masked before it is hashed, because evidence is immutable:
    # a credential that reaches the manifest is pinned there by the same machinery that
    # makes evidence worth trusting. On by default — an opt-in leak control protects only
    # the repositories that already knew to ask.
    redact_secrets: bool = True
    redact_builtins: bool = True
    redact: tuple[RedactionRule, ...] = ()
    # A stream this heavily masked is reported as suspicious rather than accepted. An
    # over-broad rule is the one way redaction could be used to hide a failing check.
    max_redaction_ratio: float = field(default=0.5, metadata={"ge": 0, "le": 1})


@dataclass(frozen=True)
class ReviewConfig:
    """W1.1 — independence is a property of the model, not of the persona name."""

    # The model the verifier and red-team agents must run. `null` means the repository
    # has not pinned one; `require_reviewer_model` then decides whether that is fatal.
    reviewer_model: str | None = None
    forbid_same_family: bool = True
    # Fail closed: a review with no recorded reviewer model is invalid rather than
    # accepted as legacy-shaped.
    require_reviewer_model: bool = True
    # F3. Gate 2 and Gate 3 require a recorded adversarial review of the artifact they
    # approve. The model default is False so that a repository with no configuration at
    # all — a bare directory in a test, say — behaves as it always did; the shipped
    # `.ai-sdlc/config.yaml` that `init` scaffolds sets it True, which is the setting a
    # real repository runs under. A spec defect found at Gate 2 costs a rewrite; the same
    # defect found at Task review costs the implementation and its evidence.
    require_pre_gate_review: bool = False


@dataclass(frozen=True)
class EvaluationConfig:
    runs: int = field(default=3, metadata={"ge": 3})
    judge_minimum_agreement: float = field(default=JUDGE_AGREEMENT_FLOOR, metadata={"ge": 0, "le": 1})
    # W1.5 judge calibration hardening.
    judge_require_dual_order: bool = True
    judge_require_binary_rubric: bool = True
    judge_minimum_exemplars: int = field(default=2, metadata={"ge": 0})
    judge_require_reasoning: bool = True
    judge_human_review_fraction: float = field(default=0.05, metadata={"ge": 0, "le": 1})
    judge_forbid_generator_family: bool = True
    # W2.4 sequential early stopping. Decision rules stay predeclared; only the sample
    # size adapts.
    sequential_stopping: bool = False
    max_runs: int = field(default=5, metadata={"ge": 3})


@dataclass(frozen=True)
class VerificationConfig:
    """W1.2 and W1.4 — instrument provenance and test-quality companions."""

    require_holdout: bool = True
    allow_holdout_waiver: bool = True
    # Severity assigned to a Task whose visible suite passes and whose held-out suite
    # fails. This is a specification-conformance finding, not a flake.
    conformance_gap_severity: str = "high"
    mutation_default_threshold: float | None = field(default=None, metadata={"ge": 0, "le": 1})
    # Mutation runs are phase- and convergence-time only, never per-attempt.
    mutation_stages: tuple[str, ...] = ("phase", "convergence")


@dataclass(frozen=True)
class OrchestrationConfig:
    max_parallel_tasks: int = field(default=4, metadata={"ge": 1})
    retry_limit: int = field(default=2, metadata={"ge": 0})
    retain_worktrees: bool = True


@dataclass(frozen=True)
class GateConfig:
    """W3.3 — who *may* approve, recorded alongside who did."""

    # Gate identifier -> approver identities or roles permitted to decide it. An empty
    # mapping for a gate means any approver is accepted, preserving current behaviour.
    approvers: dict[str, list[str]] = field(default_factory=dict)
    require_declared_approver: bool = False
    # R4.3. Off by default: a solo operator's typed name is an honest record, and an
    # air-gapped machine may have no identity provider at all. On, a gate decision must be
    # bound to an identity the runner verified rather than one the caller asserted.
    require_verified: bool = False


@dataclass(frozen=True)
class MergeConfig:
    """W3.3 — trunk-based staleness discipline for many concurrent worktrees."""

    max_base_lag: int = field(default=50, metadata={"ge": 0})
    integration_ref: str = "HEAD"


@dataclass(frozen=True)
class ScaleConfig:
    """W3.1 — program behaviour."""

    programs_enabled: bool = True
    # W6. The most Intents that may be in `building` at once, across the repository. Unset
    # means no limit, which is today's behaviour. A limit turns "too much in flight" from a
    # feeling into a refusal that names what is building, so a team finishes before it starts.
    max_building: int | None = field(default=None, metadata={"ge": 1})
    # W3.2. Identifier this repository answers to when it participates in a federated
    # Intent declared elsewhere.
    repository_id: str | None = None
    federation_enabled: bool = False


@dataclass(frozen=True)
class CostConfig:
    """W2.2 — the verification tax is only manageable once it is measured."""

    enabled: bool = True
    currency: str = "usd"
    # Ratio of verification cost to implementation cost above which the loop metrics
    # report the tax as elevated. Reported, never enforced: it informs decay review.
    verification_tax_warning_ratio: float = field(default=1.0, metadata={"ge": 0})


@dataclass(frozen=True)
class SecurityConfig:
    """W4.3 — security inherited as standing suites, plus declared sandbox posture."""

    require_declared_posture: bool = False
    default_network_policy: str = "deny"
    default_filesystem_scope: str = "worktree"
    default_credential_surface: str = "none"


@dataclass(frozen=True)
class UnderstandingConfig:
    """W5.1 — advisory project understanding, permanently at the weakest control tier."""

    enabled: bool = True
    # Fraction of anchored paths that may change before the understanding is called
    # stale. Above it, the document is excluded from context packs rather than carried
    # with a warning: stale orientation is worse than none.
    drift_threshold: float = field(default=0.34, metadata={"gt": 0, "le": 1})
    # Escape hatch for a repository that would rather read a stale understanding than
    # none. Off by default, and never a gate either way.
    include_when_stale: bool = False


@dataclass(frozen=True)
class SpecificationConfig:
    """Wave S1 — requirement-quality lint at Gate 2.

    `fail_on` is the lowest lint severity that blocks `advance <id> specified` and is
    listed as outstanding in the G2 gate packet. `error` blocks only what is objectively
    unfinished; `warning` also blocks ambiguity and unmeasured quality requirements.
    """

    fail_on: str = field(default="error", metadata={"choices": ("error", "warning", "never")})


@dataclass(frozen=True)
class Config:
    version: int = 1
    # R1.3. The runner version that scaffolded this repository, recorded by `init` so a
    # later runner can say which one wrote the records it is reading.
    runner_version: str | None = None
    delivery_root: str = "delivery/intents"
    default_artifact_mode: str = "compact"
    retry_limit: int = field(default=2, metadata={"ge": 0})
    evidence: EvidenceConfig = EvidenceConfig()
    review: ReviewConfig = ReviewConfig()
    evaluations: EvaluationConfig = EvaluationConfig()
    verification: VerificationConfig = VerificationConfig()
    orchestration: OrchestrationConfig = OrchestrationConfig()
    gates: GateConfig = GateConfig()
    merge: MergeConfig = MergeConfig()
    scale: ScaleConfig = ScaleConfig()
    cost: CostConfig = CostConfig()
    security: SecurityConfig = SecurityConfig()
    understanding: UnderstandingConfig = UnderstandingConfig()
    specification: SpecificationConfig = SpecificationConfig()


# -- construction --------------------------------------------------------------------


def _is_dataclass_type(hint: Any) -> bool:
    return isinstance(hint, type) and dataclasses.is_dataclass(hint)


def _optional_inner(hint: Any) -> Any | None:
    """Return `X` for `X | None`, else None."""
    if get_origin(hint) in (Union, types.UnionType):
        args = [arg for arg in get_args(hint) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return None


def _check_bounds(value: Any, bounds: dict[str, Any], where: str) -> None:
    if "choices" in bounds and value not in bounds["choices"]:
        allowed = ", ".join(repr(choice) for choice in bounds["choices"])
        raise ConfigError(f"{where}: {value!r} is not one of {allowed}")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return
    if "ge" in bounds and value < bounds["ge"]:
        raise ConfigError(f"{where}: {value} is less than the minimum of {bounds['ge']}")
    if "gt" in bounds and value <= bounds["gt"]:
        raise ConfigError(f"{where}: {value} must be greater than {bounds['gt']}")
    if "le" in bounds and value > bounds["le"]:
        raise ConfigError(f"{where}: {value} is greater than the maximum of {bounds['le']}")
    if "lt" in bounds and value >= bounds["lt"]:
        raise ConfigError(f"{where}: {value} must be less than {bounds['lt']}")


def _coerce(value: Any, hint: Any, where: str) -> Any:
    inner = _optional_inner(hint)
    if inner is not None:
        return None if value is None else _coerce(value, inner, where)
    if _is_dataclass_type(hint):
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: expected a mapping, got {type(value).__name__}")
        return _build(hint, value, where)
    origin = get_origin(hint)
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{where}: expected a list, got {type(value).__name__}")
        (item_hint, *_rest) = get_args(hint) or (Any,)
        return tuple(_coerce(item, item_hint, f"{where}[{index}]") for index, item in enumerate(value))
    if hint is float and isinstance(value, int) and not isinstance(value, bool):
        # YAML writes `1` where a float is meant often enough that refusing it would be
        # pedantry, and Pydantic accepted it too.
        return float(value)
    if hint in (int, float, str, bool) and not isinstance(value, hint):
        raise ConfigError(f"{where}: expected {hint.__name__}, got {type(value).__name__}")
    return value


def _build(cls: type, raw: dict[str, Any], where: str = "") -> Any:
    """Construct one dataclass section, refusing keys it does not declare.

    Refusing unknown keys is the `extra="forbid"` behaviour, and it is load-bearing: a
    misspelled setting that is silently ignored looks exactly like a setting that is
    being honoured.
    """
    hints = typing.get_type_hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls)}
    retired = RETIRED_SETTINGS.get(where, {})
    unknown = sorted(set(raw) - set(fields) - set(retired))
    if unknown:
        prefix = f"{where}: " if where else ""
        raise ConfigError(f"{prefix}unknown setting(s): {', '.join(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, value in raw.items():
        if name in retired:
            continue  # inert; reported by `doctor`, cleared by `init --force`
        location = f"{where}.{name}" if where else name
        coerced = _coerce(value, hints[name], location)
        _check_bounds(coerced, dict(fields[name].metadata), location)
        kwargs[name] = coerced
    return cls(**kwargs)


def load_config(repository_root: Path) -> Config:
    path = repository_root / ".ai-sdlc" / "config.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(str(error)) from error
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml must contain a mapping")
    return _build(Config, raw)


def delivery_root(repository_root: Path) -> Path:
    """Resolve the configured delivery root for a repository.

    Every lookup of Intent and Task artifacts goes through here so a target repository
    can relocate `delivery/intents` without silently breaking identifier resolution or
    the durable-Task hook checks. Raises `ConfigError` when the configuration is
    unreadable; callers fail closed rather than guessing the default.
    """
    configured = load_config(repository_root).delivery_root
    return repository_root / Path(configured)


def effective_judge_agreement(config: Config) -> float:
    """The agreement a judge must actually reach.

    A repository may raise the floor but never lower it. Reading the configured value
    directly would let an old `0.80` in a target repository's `config.yaml` silently keep
    the pre-2026 threshold after an upgrade.
    """
    return max(float(config.evaluations.judge_minimum_agreement), JUDGE_AGREEMENT_FLOOR)


def model_family(model_id: str | None) -> str | None:
    """Reduce a model identifier to the family that carries judge bias.

    Family bias is documented at the family level, not the exact-version level: a judge
    over-rewards its own family's outputs whether or not the point releases match. The
    identity that matters for independence is therefore the vendor-and-family prefix,
    which is what this returns.
    """
    if not model_id:
        return None
    normalized = str(model_id).strip().lower()
    if not normalized:
        return None
    # Strip a provider route prefix such as `us.anthropic.` or `bedrock/`.
    for separator in ("/", "::"):
        if separator in normalized:
            normalized = normalized.rsplit(separator, 1)[-1]
    parts = [part for part in normalized.replace("_", "-").split("-") if part]
    if not parts:
        return None
    # `claude-fable-5-20260101` -> `claude-fable`; `gpt-5.2` -> `gpt`; `inherit` -> `inherit`.
    family = [parts[0]]
    for part in parts[1:]:
        if part.isdigit() or part[0].isdigit():
            break
        family.append(part)
    return "-".join(family[:2])
