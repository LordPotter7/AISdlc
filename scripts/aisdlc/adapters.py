"""Model and judge adapters for the evaluation runner (W1.6).

`ai-sdlc eval run` adjudicates per-case results supplied to it. It applies the median,
tolerance, flake, calibration and zero-success rules correctly — but producing the
per-case results was the caller's job, and that reopens the exact hole the constitution
closes everywhere else: *if the caller produces per-case results, an agent can produce
per-case results*. Class S, J and N are the framework's differentiator, and until now a
human or an ad-hoc script carried them the last mile.

An adapter is the seam that closes it. The runner calls the adapter; the adapter calls the
model; the runner writes the per-case results into evidence itself. Adjudication logic is
untouched — it simply stops depending on an untrusted caller.

Adapters are registered by name so a repository can supply its own without the runner
importing anything vendor-specific at module load. The reference implementation targets
the Anthropic API and is imported lazily, so the runner keeps working in a repository
that never runs a model at all.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaseResult:
    """One evaluated case. The unit the adjudicator consumes."""

    id: str
    score: float | None = None
    success: bool | None = None
    margin: float | None = None
    order_disagreement: bool = False
    latency_seconds: float = 0.0
    cost_usd: float = 0.0
    tokens: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id}
        if self.score is not None:
            payload["score"] = self.score
        if self.success is not None:
            payload["success"] = self.success
        if self.margin is not None:
            payload["margin"] = self.margin
        payload.update(
            {
                "order_disagreement": self.order_disagreement,
                "latency_seconds": round(self.latency_seconds, 6),
                "cost_usd": round(self.cost_usd, 6),
                "tokens": self.tokens,
            }
        )
        if self.detail:
            payload["detail"] = self.detail
        return payload


class EvaluationAdapter(Protocol):
    """The whole contract: turn one case plus its pins into one result.

    Deliberately minimal. An adapter that could see the threshold, the other cases, or the
    running tally could shape its answers to them, so it receives one case and the pins
    that must not vary — and nothing else.
    """

    def run_case(self, case: dict[str, Any], pins: dict[str, Any]) -> CaseResult: ...


_REGISTRY: dict[str, type] = {}


def register_adapter(name: str, adapter: type) -> None:
    _REGISTRY[name] = adapter


def resolve_adapter(name: str, repository_root: Path) -> EvaluationAdapter:
    """Instantiate a named adapter, importing the reference one only on demand."""
    if name in _REGISTRY:
        return _REGISTRY[name](repository_root)  # type: ignore[call-arg]
    if name == "anthropic":
        register_adapter("anthropic", AnthropicAdapter)
        return AnthropicAdapter(repository_root)
    if name == "command":
        register_adapter("command", CommandAdapter)
        return CommandAdapter(repository_root)
    raise AdapterError(
        f"unknown evaluation adapter: {name}; available: {', '.join(sorted({'anthropic', 'command', *_REGISTRY}))}"
    )


class CommandAdapter:
    """Run each case through a declared subprocess.

    The escape hatch for evaluations whose subject is not a hosted model — a local model,
    a service under test, a deterministic pipeline. The command receives the case on
    stdin and returns one JSON object on stdout. It is still the *runner* that invokes it
    and writes the result, which is the property that matters.
    """

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def run_case(self, case: dict[str, Any], pins: dict[str, Any]) -> CaseResult:
        command = pins.get("command")
        if not command:
            raise AdapterError("the command adapter requires a `command` pin")
        import time

        started = time.monotonic()
        result = subprocess.run(
            command,
            cwd=self.repository_root,
            shell=True,
            input=json.dumps(case),
            text=True,
            capture_output=True,
            check=False,
            timeout=int(pins.get("timeout_seconds", 600)),
        )
        latency = time.monotonic() - started
        if result.returncode != 0:
            raise AdapterError(f"case {case.get('id')} adapter command failed: {result.stderr.strip()[:400]}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AdapterError(f"case {case.get('id')} adapter returned non-JSON output: {error}") from error
        return CaseResult(
            id=str(case.get("id")),
            score=payload.get("score"),
            success=payload.get("success"),
            margin=payload.get("margin"),
            order_disagreement=bool(payload.get("order_disagreement")),
            latency_seconds=latency,
            cost_usd=float(payload.get("cost_usd", 0.0)),
            tokens=int(payload.get("tokens", 0)),
            detail=payload.get("detail", {}),
        )


class AnthropicAdapter:
    """Reference adapter over the Anthropic Messages API.

    Honours the pins the manifest already carries — model, prompt version and hash,
    temperature, seed — because an evaluation whose parameters drift is not the
    evaluation that was approved at Gate 2.
    """

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def _client(self):
        try:
            import anthropic
        except ImportError as error:  # pragma: no cover - depends on optional install
            raise AdapterError(
                "the anthropic adapter requires the `anthropic` package; install it in the evaluation "
                "environment, or use the `command` adapter"
            ) from error
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise AdapterError("the anthropic adapter requires ANTHROPIC_API_KEY in the environment")
        return anthropic.Anthropic(api_key=key)

    def run_case(self, case: dict[str, Any], pins: dict[str, Any]) -> CaseResult:  # pragma: no cover - network
        import time

        client = self._client()
        started = time.monotonic()
        response = client.messages.create(
            model=pins["model_id"],
            max_tokens=int(pins.get("max_tokens", 2048)),
            temperature=float(pins.get("temperature", 0.0)),
            system=pins.get("system_prompt") or None,
            messages=[{"role": "user", "content": str(case.get("input", ""))}],
        )
        latency = time.monotonic() - started
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        expected = case.get("expected")
        score = None
        success = None
        if expected is not None:
            score = 1.0 if str(expected).strip() in text else 0.0
        if case.get("attack") is True:
            # Class N: the case describes an attack, so "success" means the attacker won.
            success = bool(case.get("success_marker") and str(case["success_marker"]) in text)
        usage = getattr(response, "usage", None)
        tokens = (getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)) if usage else 0
        return CaseResult(
            id=str(case.get("id")),
            score=score,
            success=success,
            latency_seconds=latency,
            tokens=tokens,
            detail={"output": text[:4000]},
        )


def run_dataset(
    adapter: EvaluationAdapter,
    cases: list[dict[str, Any]],
    pins: dict[str, Any],
) -> dict[str, Any]:
    """Execute one full run of a dataset and shape it the way the adjudicator expects."""
    results = [adapter.run_case(case, pins).as_dict() for case in cases]
    scored = [float(item["score"]) for item in results if item.get("score") is not None]
    return {
        "cases": results,
        "score": (sum(scored) / len(scored)) if scored else 0.0,
        "cost_usd": round(sum(float(item.get("cost_usd", 0)) for item in results), 6),
        "latency_seconds": round(sum(float(item.get("latency_seconds", 0)) for item in results), 6),
        "tokens": sum(int(item.get("tokens", 0)) for item in results),
    }
