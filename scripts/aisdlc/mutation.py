"""Mutation score as a Class D companion (W1.4).

Coverage measures which lines ran. It does not measure whether anything would have
noticed them being wrong, and AI-generated suites reach high coverage while killing far
fewer mutants — the tests execute the code and assert almost nothing about it.

This pairs with held-out verification rather than duplicating it. Held-out tests catch
*specification* gaming: the implementation satisfies the tests it was shown instead of
the requirement they stand for. Mutation score catches *test* weakness: the tests would
not fail even if the implementation were wrong. Different defects, both invisible to a
passing suite, so the framework wants both.

Surviving mutants are returned as structured retry input, exactly like a failing check —
practitioner reports show that feeding them back is what actually moves the score, rather
than reporting a number nobody acts on.

Cost discipline: mutation runs at phase and convergence boundaries, never per attempt.
Running a mutation suite on every retry would multiply the verification tax by the number
of retries, which is the failure mode Wave 2 exists to prevent.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MutationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MutationResult:
    score: float
    killed: int
    survived: int
    threshold: float | None
    surviving_mutants: tuple[dict[str, Any], ...]
    tool: str
    raw_output: str

    @property
    def passed(self) -> bool:
        return self.threshold is None or self.score >= self.threshold

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "score": round(self.score, 6),
            "killed": self.killed,
            "survived": self.survived,
            "threshold": self.threshold,
            "passed": self.passed,
            "surviving_mutants": list(self.surviving_mutants),
        }

    def retry_input(self) -> str:
        """Render surviving mutants as the corrective instruction they imply.

        A bare score tells an agent it did badly. A list of the specific mutations that
        went unnoticed tells it which assertions are missing, which is the form the
        feedback has to take to change the next attempt.
        """
        if self.passed and not self.surviving_mutants:
            return f"Mutation score {self.score:.2%} met the {self.threshold:.2%} threshold." if self.threshold else ""
        lines = [
            f"Mutation score {self.score:.2%}"
            + (f" is below the required {self.threshold:.2%}." if self.threshold else "."),
            "",
            "These mutations survived — the suite passes even with the code changed this way. "
            "Each one names an assertion the tests do not make:",
            "",
        ]
        for mutant in self.surviving_mutants[:25]:
            location = mutant.get("location") or mutant.get("id") or "unknown"
            description = mutant.get("description") or mutant.get("mutation") or ""
            lines.append(f"- {location}: {description}".rstrip())
        if len(self.surviving_mutants) > 25:
            lines.append(f"- ... and {len(self.surviving_mutants) - 25} more")
        return "\n".join(lines)


class MutationRunner:
    """Invoke a mutation tool and normalise its report into evidence."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def run(
        self,
        paths: list[str],
        *,
        threshold: float | None = None,
        tool: str = "mutmut",
        timeout_seconds: int = 3600,
    ) -> MutationResult:
        command = self._command(tool, paths)
        try:
            # An argument list and no shell: `paths` come from a Task's declared paths, which
            # are data, and must never be parsed as shell syntax.
            result = subprocess.run(
                command,
                cwd=self.repository_root,
                shell=False,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise MutationError(f"mutation run failed to start ({tool}): {error}") from error
        output = (result.stdout or "") + (result.stderr or "")
        parsed = self._parse(tool, output)
        return MutationResult(
            score=parsed["score"],
            killed=parsed["killed"],
            survived=parsed["survived"],
            threshold=threshold,
            surviving_mutants=tuple(parsed["surviving_mutants"]),
            tool=tool,
            raw_output=output[-20000:],
        )

    @staticmethod
    def _command(tool: str, paths: list[str]) -> list[str]:
        targets = list(paths) or ["."]
        if tool == "mutmut":
            # mutmut takes one comma-separated value for several paths.
            return ["mutmut", "run", "--paths-to-mutate", ",".join(targets), "--no-progress"]
        if tool == "cosmic-ray":
            return ["cosmic-ray", "exec", *targets]
        raise MutationError(f"unsupported mutation tool: {tool}")

    @staticmethod
    def _parse(tool: str, output: str) -> dict[str, Any]:
        """Extract killed/survived counts from a tool's report.

        Tolerant by design: a mutation tool that changes its output format should degrade
        to a zero score that fails a threshold loudly, never to a silent pass.
        """
        killed = survived = 0
        surviving: list[dict[str, Any]] = []
        if tool == "mutmut":
            killed = _count(output, r"(\d+)\s*(?:🎉|killed)")
            survived = _count(output, r"(\d+)\s*(?:🙁|survived)")
            for match in re.finditer(r"^\s*(?:survived|🙁)\s+(?P<loc>\S+)\s*(?P<desc>.*)$", output, re.MULTILINE):
                surviving.append({"location": match.group("loc"), "description": match.group("desc").strip()})
        elif tool == "cosmic-ray":
            killed = _count(output, r"total killed\D*(\d+)")
            survived = _count(output, r"total survived\D*(\d+)")
        total = killed + survived
        return {
            "score": (killed / total) if total else 0.0,
            "killed": killed,
            "survived": survived,
            "surviving_mutants": surviving,
        }


def _count(output: str, pattern: str) -> int:
    match = re.search(pattern, output, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def declared_thresholds(task: dict[str, Any], default: float | None = None) -> float | None:
    """The strictest mutation threshold any criterion on this Task declares.

    Strictest rather than first: a Task carrying one criterion at 0.7 and another at 0.85
    has to satisfy both, and the tighter bound is the one that does.
    """
    thresholds = [
        float(criterion["mutation_threshold"])
        for criterion in task.get("acceptance_criteria", [])
        if isinstance(criterion, dict) and criterion.get("mutation_threshold") is not None
    ]
    if not thresholds:
        return default
    return max(thresholds)


def attach_to_evidence(manifest_path: Path, result: MutationResult) -> None:
    """Record the mutation result inside the evidence manifest it belongs to.

    Written into the manifest rather than beside it so the existing hash and (once signed)
    attestation cover the mutation score too. A test-quality number that lives outside the
    integrity chain is a claim, not evidence.
    """
    from .storage import atomic_write_json

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mutation"] = result.as_dict()
    atomic_write_json(manifest_path, manifest)
