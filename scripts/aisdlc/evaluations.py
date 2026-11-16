from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import model_family
from .storage import FileMutex, atomic_write_json, atomic_write_text
from .vendor import yaml


class EvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunValidation:
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


class DatasetRegistry:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.path = self.repository_root / ".ai-sdlc" / "evals" / "datasets.yaml"
        self.lock_path = self.repository_root / ".ai-sdlc" / "runtime" / "datasets.lock"

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"datasets": {}}
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {"datasets": {}}

    def register(
        self,
        name: str,
        path: Path,
        *,
        owner: str,
        version: str,
        trigger: str | None = None,
        selection_policy: str = "all",
    ) -> None:
        resolved = path.resolve()
        if not resolved.exists():
            raise EvaluationError(f"dataset does not exist: {resolved}")
        if not selection_policy.strip():
            raise EvaluationError("dataset selection policy must be predeclared")
        try:
            stored_path = str(resolved.relative_to(self.repository_root)).replace("\\", "/")
        except ValueError:
            stored_path = str(resolved)
        with FileMutex(self.lock_path):
            registry = self.read()
            registry.setdefault("datasets", {})[name] = {
                "path": stored_path,
                "owner": owner,
                "version": version,
                "hash": self.compute_hash(resolved),
                "trigger": trigger,
                "selection_policy": selection_policy,
            }
            atomic_write_text(self.path, yaml.safe_dump(registry, sort_keys=True))

    def resolve(self, name: str) -> dict[str, Any]:
        try:
            return self.read()["datasets"][name]
        except KeyError as error:
            raise EvaluationError(f"unknown dataset: {name}") from error

    def inherit(self, flags: dict[str, bool]) -> list[str]:
        inherited = []
        for name, record in sorted(self.read().get("datasets", {}).items()):
            trigger = record.get("trigger")
            if trigger and flags.get(trigger) is True:
                inherited.append(name)
        return inherited

    def decline_inherited(self, intent_id: str, name: str, reason: str) -> dict[str, str]:
        self.resolve(name)
        if not reason.strip():
            raise EvaluationError("declining an inherited suite requires a written reason")
        path = self.repository_root / ".ai-sdlc" / "runtime" / "suite-declines.yaml"
        with FileMutex(self.lock_path):
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"declines": []}
            record = {
                "intent_id": intent_id,
                "suite": name,
                "reason": reason.strip(),
                "status": "escalated",
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            existing.setdefault("declines", []).append(record)
            atomic_write_text(path, yaml.safe_dump(existing, sort_keys=False))
        return record

    @staticmethod
    def compute_hash(path: Path) -> str:
        digest = hashlib.sha256()
        paths = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
        for item in paths:
            relative = item.name if path.is_file() else str(item.relative_to(path)).replace("\\", "/")
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
        return f"sha256:{digest.hexdigest()}"


class EvaluationRunner:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.registry = DatasetRegistry(self.repository_root)

    def execute(self, spec: dict[str, Any], output_root: Path, *, adapter_name: str | None = None) -> Path:
        """Produce per-case results *and* adjudicate them, in one runner-owned operation.

        This is the difference between `evaluate`, which trusts results handed to it, and
        a closed loop. The runner loads the pinned dataset, drives the adapter itself, and
        writes what came back — so no agent stands between the model and the evidence.
        """
        from .adapters import resolve_adapter, run_dataset

        adapter_name = adapter_name or spec.get("adapter") or "command"
        dataset = self.registry.resolve(spec["dataset"])
        cases = self._load_cases(dataset)
        if not cases:
            raise EvaluationError(f"dataset {spec['dataset']} contains no cases")
        adapter = resolve_adapter(adapter_name, self.repository_root)
        settings = self._settings()
        pins = dict(spec["pins"])
        runs: list[dict[str, Any]] = []
        for index in range(settings.max_runs if settings.sequential_stopping else settings.runs):
            runs.append(run_dataset(adapter, cases, pins))
            if index + 1 < settings.runs:
                continue
            if not settings.sequential_stopping or self._decided(spec, runs):
                break
        return self.evaluate(spec, runs, output_root)

    def _decided(self, spec: dict[str, Any], runs: list[dict[str, Any]]) -> bool:
        """Has the predeclared rule already been settled beyond the interval? (W2.4)

        Stopping early is only legitimate when the answer cannot change: every run so far
        agrees, and the confidence interval sits wholly on one side of the threshold.
        A result that straddles the threshold keeps sampling up to the configured maximum.
        """
        if spec.get("verification_class") == "N":
            return False
        try:
            result = self._evaluate_scored(spec, runs)
        except (KeyError, ValueError, statistics.StatisticsError):
            return False
        if result["flaky"]:
            return False
        interval = result["confidence_interval_95"]
        target = float(spec["threshold"]["value"])
        operator = spec["threshold"]["operator"]
        if operator in {">=", ">"}:
            return interval["low"] > target or interval["high"] < target
        if operator in {"<=", "<"}:
            return interval["high"] < target or interval["low"] > target
        return False

    @staticmethod
    def _load_cases(dataset: dict[str, Any]) -> list[dict[str, Any]]:
        path = Path(dataset["path"])
        if not path.is_absolute():
            path = Path.cwd() / path
        candidates = [path] if path.is_file() else sorted(path.glob("*.json")) + sorted(path.glob("*.jsonl"))
        cases: list[dict[str, Any]] = []
        for candidate in candidates:
            text = candidate.read_text(encoding="utf-8")
            if candidate.suffix == ".jsonl":
                cases.extend(json.loads(line) for line in text.splitlines() if line.strip())
            else:
                payload = json.loads(text)
                cases.extend(payload if isinstance(payload, list) else payload.get("cases", []))
        return cases

    def evaluate(self, spec: dict[str, Any], runs: list[dict[str, Any]], output_root: Path) -> Path:
        verification_class = spec.get("verification_class")
        if verification_class not in {"S", "J", "N"}:
            raise EvaluationError("evaluation class must be S, J, or N")
        dataset = self.registry.resolve(spec["dataset"])
        self._validate_common(spec, runs)
        if verification_class == "J":
            self._validate_judge(spec)
        result = self._evaluate_negative(spec, runs) if verification_class == "N" else self._evaluate_scored(spec, runs)
        run_root = self._next_run_root(output_root, spec["id"])
        run_root.mkdir(parents=True, exist_ok=False)
        per_case_path = run_root / "per-case-results.json"
        atomic_write_json(per_case_path, runs)
        pins = spec["pins"]
        manifest = {
            "eval_id": spec["id"],
            "verification_class": verification_class,
            "dataset": spec["dataset"],
            "dataset_hash": dataset["hash"],
            "runs": len(runs),
            "model_id": pins["model_id"],
            "prompt_version": pins["prompt_version"],
            "prompt_hash": pins["prompt_hash"],
            "temperature": pins["temperature"],
            "seed": pins.get("seed"),
            "source_commit": self._source_commit(),
            "captured_by": "evaluation-runner",
            "captured_at": datetime.now(UTC).isoformat(),
            "metric": spec["metric"],
            "threshold": spec["threshold"],
            "minimum_sample_size": spec["minimum_sample_size"],
            "tolerance_band": spec.get("tolerance_band"),
            "result": result,
            "per_case_results": per_case_path.name,
        }
        if verification_class == "J":
            manifest["judge"] = spec["judge"]
            manifest["judge_human_review"] = self._judge_review_queue(spec, runs)
            manifest["judge_agreement_floor"] = self._agreement_floor()
        if verification_class in {"S", "J"}:
            manifest["sampling"] = self._sampling_record(runs, result)
        manifest_path = run_root / "manifest.json"
        atomic_write_json(manifest_path, manifest)
        return manifest_path

    def _validate_common(self, spec: dict[str, Any], runs: list[dict[str, Any]]) -> None:
        required = {"id", "verification_class", "dataset", "metric", "threshold", "minimum_sample_size", "pins"}
        missing = sorted(required - spec.keys())
        if missing:
            raise EvaluationError(f"missing evaluation fields: {', '.join(missing)}")
        settings = self._settings()
        if spec["verification_class"] in {"S", "J"}:
            # W2.4: with sequential stopping the sample size adapts between the declared
            # minimum and a configured ceiling. The decision *rule* stays predeclared —
            # only how many runs it takes to reach it varies — so this is not the
            # "choose the threshold after seeing the result" failure it superficially
            # resembles.
            if settings.sequential_stopping:
                if not settings.runs <= len(runs) <= settings.max_runs:
                    raise EvaluationError(
                        f"Class S and J evaluations require between {settings.runs} and {settings.max_runs} runs "
                        f"under sequential stopping; got {len(runs)}"
                    )
            elif len(runs) != settings.runs:
                raise EvaluationError(f"Class S and J evaluations require exactly {settings.runs} runs")
        for run in runs:
            if len(run.get("cases", [])) < spec["minimum_sample_size"]:
                raise EvaluationError("run is below the predeclared minimum sample size")

    def _validate_judge(self, spec: dict[str, Any]) -> None:
        """Hold Class J judges to 2026 calibration practice (W1.5).

        Each check below corresponds to a documented judge failure mode rather than to a
        style preference:

        - *Family bias*: a judge over-rewards outputs from its own family, so the judge
          model may not share a family with the generator it grades.
        - *Position bias*: pairwise judgments flip with presentation order, so both
          orderings must run and disagreement must be surfaced rather than averaged away.
        - *Length-confidence bias*: fluent hallucinations are under-penalised by holistic
          scores, so rubrics decompose into discrete binary checks.
        - *Anchor drift*: a pinned prompt hash fixes the words but not the standard, so
          annotated exemplars anchor what the words mean.
        - *Post-hoc rationalisation*: a score produced before its reasoning is a guess
          with an explanation attached, so reasoning comes first.
        """
        settings = self._settings()
        judge = spec.get("judge")
        if not judge or "calibration" not in judge:
            raise EvaluationError("Class J requires a calibration record")
        calibration = judge["calibration"]
        if any(calibration.get(field) != judge.get(field) for field in ("model_id", "prompt_version", "prompt_hash")):
            raise EvaluationError("judge calibration is stale for the current model or prompt")
        matrix = calibration.get("confusion_matrix")
        expected_cells = {"true_positive", "true_negative", "false_positive", "false_negative"}
        if not isinstance(matrix, dict) or not expected_cells <= matrix.keys():
            raise EvaluationError("judge calibration requires a confusion matrix")
        total = sum(int(matrix[cell]) for cell in expected_cells)
        if total <= 0:
            raise EvaluationError("judge calibration confusion matrix is empty")
        measured_agreement = (int(matrix["true_positive"]) + int(matrix["true_negative"])) / total
        if abs(measured_agreement - float(calibration.get("agreement", 0))) > 0.01:
            raise EvaluationError("judge calibration agreement does not match its confusion matrix")
        floor = self._agreement_floor()
        if measured_agreement < floor:
            raise EvaluationError(
                f"judge calibration agreement {measured_agreement:.3f} is below the required floor {floor:.2f}"
            )
        if settings.judge_forbid_generator_family:
            judge_family = model_family(judge.get("model_id"))
            generator_family = model_family(spec.get("pins", {}).get("model_id"))
            if judge_family is not None and judge_family == generator_family:
                raise EvaluationError(
                    f"judge model family {judge_family!r} matches the generator's; a judge may not grade its own "
                    "family's output"
                )
        if settings.judge_require_dual_order and not judge.get("dual_order"):
            raise EvaluationError(
                "Class J requires pairwise judgments in both orderings; set `judge.dual_order: true` and record "
                "order disagreement per case"
            )
        if settings.judge_require_binary_rubric:
            rubric = judge.get("rubric")
            if not isinstance(rubric, list) or not rubric:
                raise EvaluationError("Class J requires a rubric decomposed into discrete checks")
            for check in rubric:
                if not isinstance(check, dict) or check.get("type") != "binary" or not check.get("id"):
                    raise EvaluationError(
                        "every Class J rubric check must be binary and identified; holistic or free-form "
                        "scales under-penalise fluent, confident errors"
                    )
        exemplars = judge.get("exemplars") or []
        if len(exemplars) < settings.judge_minimum_exemplars:
            raise EvaluationError(
                f"Class J requires at least {settings.judge_minimum_exemplars} annotated few-shot exemplars; "
                f"got {len(exemplars)}"
            )
        if any(not str(item.get("annotation", "")).strip() for item in exemplars if isinstance(item, dict)):
            raise EvaluationError("every judge exemplar must carry an annotation explaining its label")
        if settings.judge_require_reasoning and not judge.get("reasoning_before_score"):
            raise EvaluationError(
                "Class J requires reasoning before the score; set `judge.reasoning_before_score: true`"
            )

    def _settings(self):
        from .config import ConfigError, load_config

        try:
            return load_config(self.repository_root).evaluations
        except ConfigError:
            from .config import EvaluationConfig

            return EvaluationConfig()

    def _agreement_floor(self) -> float:
        from .config import ConfigError, effective_judge_agreement, load_config

        try:
            return effective_judge_agreement(load_config(self.repository_root))
        except ConfigError:
            from .config import JUDGE_AGREEMENT_FLOOR

            return JUDGE_AGREEMENT_FLOOR

    def _judge_review_queue(self, spec: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
        """Route the least-confident judged cases to a human (W1.5).

        Every automated judge has a band where it is guessing. Sending that band to a
        person is what keeps a judged criterion honest without paying for full human
        review, and recording the routed fraction is what makes the escape hatch
        auditable rather than aspirational.
        """
        settings = self._settings()
        fraction = settings.judge_human_review_fraction
        cases: list[dict[str, Any]] = []
        for run_index, run in enumerate(runs):
            for case in run.get("cases", []):
                if not isinstance(case, dict):
                    continue
                # Lowest margin first; a case where the two orderings disagreed is
                # maximally uncertain regardless of its reported margin.
                margin = 0.0 if case.get("order_disagreement") else abs(float(case.get("margin", 1.0)))
                cases.append({"run": run_index, "case_id": case.get("id"), "margin": margin})
        if not cases or fraction <= 0:
            return {"fraction": fraction, "routed": 0, "cases": []}
        count = max(1, round(len(cases) * fraction))
        routed = sorted(cases, key=lambda item: item["margin"])[:count]
        return {
            "fraction": fraction,
            "routed": len(routed),
            "total_cases": len(cases),
            "cases": routed,
            "status": "awaiting_human_review",
        }

    @staticmethod
    def _compare(value: float, threshold: dict[str, Any]) -> bool:
        operator, target = threshold["operator"], float(threshold["value"])
        comparisons = {
            ">=": value >= target,
            ">": value > target,
            "<=": value <= target,
            "<": value < target,
            "==": value == target,
        }
        if operator not in comparisons:
            raise EvaluationError(f"unsupported threshold operator: {operator}")
        return comparisons[operator]

    def _evaluate_scored(self, spec: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
        scores = [float(run["score"]) for run in runs]
        median = statistics.median(scores)
        operator = spec["threshold"]["operator"]
        worst = min(scores) if operator in {">=", ">"} else max(scores)
        tolerance = float(spec.get("tolerance_band", 0))
        target = float(spec["threshold"]["value"])
        within_tolerance = worst >= target - tolerance if operator in {">=", ">"} else worst <= target + tolerance
        individual_passes = [self._compare(score, spec["threshold"]) for score in scores]
        flaky = any(individual_passes) and not all(individual_passes)
        threshold_pass = self._compare(median, spec["threshold"]) and within_tolerance
        passed = threshold_pass and not flaky
        standard_error = statistics.stdev(scores) / len(scores) ** 0.5 if len(scores) > 1 else 0.0
        return {
            "scores": scores,
            "median": median,
            "minimum": min(scores),
            "maximum": max(scores),
            "worst": worst,
            "confidence_interval_95": {
                "method": "normal-approximation-over-independent-runs",
                "low": median - 1.96 * standard_error,
                "high": median + 1.96 * standard_error,
            },
            "flaky": flaky,
            "passed": passed,
            "decision": "flaky" if flaky else "passed" if passed else "failed",
            "total_cost_usd": sum(float(run.get("cost_usd", 0)) for run in runs),
            "latency_seconds": [float(run.get("latency_seconds", 0)) for run in runs],
        }

    @staticmethod
    def _evaluate_negative(spec: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
        successes = sum(1 for run in runs for case in run.get("cases", []) if case.get("success") is True)
        passed = successes == 0
        return {
            "observed_successes": successes,
            "cases": sum(len(run.get("cases", [])) for run in runs),
            "passed": passed,
            "decision": "passed" if passed else "failed",
            "claim": "zero observed successes" if passed else f"{successes} observed attacker successes",
            "coverage_limitation": "The suite bounds observed behavior; it does not establish zero risk.",
        }

    def _sampling_record(self, runs: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
        """Record why this many runs were enough (W2.4).

        Fixed three-run sampling costs the same whether the result is unambiguous or
        borderline. Under sequential stopping the runner reports which case it was, so a
        cheap decision is visibly cheap and a borderline one is visibly borderline rather
        than both looking identical in the ledger.
        """
        settings = self._settings()
        interval = result.get("confidence_interval_95") or {}
        decisive = bool(interval) and not result.get("flaky")
        return {
            "policy": "sequential" if settings.sequential_stopping else "fixed",
            "runs": len(runs),
            "minimum_runs": settings.runs,
            "maximum_runs": settings.max_runs if settings.sequential_stopping else settings.runs,
            "stopped_early": settings.sequential_stopping and len(runs) == settings.runs and decisive,
            "extended": settings.sequential_stopping and len(runs) > settings.runs,
        }

    @staticmethod
    def _next_run_root(output_root: Path, eval_id: str) -> Path:
        base = output_root / eval_id
        existing = [int(path.name.split("-", 1)[1]) for path in base.glob("run-*")] if base.exists() else []
        return base / f"run-{max(existing, default=0) + 1:03d}"

    def _source_commit(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repository_root, check=False, text=True, capture_output=True
        )
        return result.stdout.strip() if result.returncode == 0 else "uncommitted"


def validate_run_manifest(manifest_path: Path, *, expected_pins: dict[str, Any]) -> RunValidation:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return RunValidation((f"unreadable run manifest: {error}",))
    errors = []
    fields = ["model_id", "prompt_version", "prompt_hash", "temperature", "seed"]
    fields.extend(field for field in ("dataset_hash", "source_commit") if field in expected_pins)
    for field in fields:
        if manifest.get(field) != expected_pins.get(field):
            errors.append(f"{field} pin does not match")
    return RunValidation(tuple(errors))
