"""Mask credentials in captured output *before* it is hashed.

Evidence is immutable, hashed, and retained. Those are
the properties that make it worth trusting, and they are also what makes it the worst
possible place for a leaked credential: the framework's own rules forbid editing the
record, CI log masking does not reach an uploaded artifact, and a hash pins the secret to
a commit for as long as the repository exists.

So the pass runs before the write, not after. By the time `sha256_file` sees the stream,
the secret was never in it.

Two design constraints pull against each other and both are honoured here.

*Evidence must stay honest about what was masked.* A silent substitution is a way to hide
a failing assertion's output. So every mask is length-annotated (`[redacted:aws-key:20]`),
every rule's hit count is recorded in the manifest, and the redacted fraction of each
stream is measured. A stream that is mostly mask is reported as suspicious rather than
accepted quietly — see `evidence.max_redaction_ratio`.

*The pattern set must be extensible without being a foot-gun.* Built-ins cover the shapes
that leak most often. A repository adds its own under `evidence.redact`, and a pattern
that fails to compile is reported rather than silently ignored, because a redaction rule
that is not running is worse than one that was never written.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

# Ordered: the first rule to match a span wins, so the specific shapes come before the
# generic `key = value` catch-all that would otherwise absorb them and report the wrong
# rule name in the manifest.
# Token shapes start with a lookbehind, not `\b`: `\b` counts `_` as a word character, so a
# token glued to an identifier (`MY_npm_...`, `TOKEN_ghp_...`) would otherwise slip through.
BUILTIN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("private-key-block", r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----"),
    ("aws-access-key", r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}\b"),
    ("aws-secret-key", r"(?i)\baws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})"),
    ("github-fine-grained-pat", r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{50,}\b"),
    ("github-token", r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("slack-token", r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}\b"),
    ("anthropic-key", r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}"),
    ("openai-style-key", r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}\b"),
    ("stripe-key", r"(?<![A-Za-z0-9])[sr]k_live_[A-Za-z0-9]{16,}\b"),
    ("npm-token", r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36}\b"),
    ("google-api-key", r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}\b"),
    ("jwt", r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ("bearer-token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    ("basic-auth-url", r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
    ("azure-account-key", r"(?i)AccountKey=[A-Za-z0-9+/=]{20,}"),
    ("connection-string-password", r"(?i)\b(?:password|pwd)\s*=\s*[^;\s]{6,}"),
    ("assigned-secret", r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
)


class RedactionError(ValueError):
    """Raised when a repository-declared redaction pattern cannot be compiled."""


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)
    redacted_characters: int = 0
    original_characters: int = 0

    @property
    def ratio(self) -> float:
        """Fraction of the original stream that was masked.

        The measure that makes over-broad redaction visible. A rule that eats a failing
        test's entire output produces a ratio near 1, which the evidence validator treats
        as a finding rather than as a clean capture.
        """
        if self.original_characters <= 0:
            return 0.0
        return self.redacted_characters / self.original_characters

    def as_manifest_record(self) -> dict[str, object] | None:
        """The manifest's account of this pass, or `None` when nothing matched.

        Absent means "nothing was masked", which is a different and stronger claim than
        "no redaction record was written", so the field is only ever omitted when the
        pass genuinely found nothing.
        """
        if not self.counts:
            return None
        return {
            "rules": [{"rule": rule, "count": count} for rule, count in sorted(self.counts.items())],
            "redacted_characters": self.redacted_characters,
            "original_characters": self.original_characters,
            "ratio": round(self.ratio, 6),
        }


class Redactor:
    """A compiled set of credential patterns applied to one stream at a time."""

    def __init__(self, extra: Iterable[tuple[str, str]] = (), *, include_builtins: bool = True) -> None:
        patterns: list[tuple[str, str]] = list(BUILTIN_PATTERNS) if include_builtins else []
        patterns.extend(extra)
        self.rules: list[tuple[str, re.Pattern[str]]] = []
        for name, pattern in patterns:
            try:
                self.rules.append((name, re.compile(pattern, re.DOTALL)))
            except re.error as error:
                raise RedactionError(f"redaction pattern {name!r} does not compile: {error}") from error

    def apply(self, text: str) -> RedactionResult:
        """Mask every match, resolving overlaps in favour of the more specific rule.

        Rules are matched against the *original* text and the replacements applied once at
        the end, rather than each rule running over the previous rule's output. Running
        them in sequence looks equivalent and is not: a mask like
        `[redacted:github-token:40]` is itself a run of non-space characters after a
        `token=`, so the generic catch-all would match the specific rule's own output,
        replace it, and leave the manifest reporting one redaction of the wrong shape by
        the wrong rule. Claiming a span for the first rule that wants it — and rules are
        ordered specific-first — keeps the record of what was removed accurate.
        """
        result = RedactionResult(text=text, original_characters=len(text))
        if not text:
            return result

        claimed: list[tuple[int, int, str]] = []
        for name, pattern in self.rules:
            for match in pattern.finditer(text):
                start, end = match.span()
                if end == start:
                    continue
                if any(start < other_end and other_start < end for other_start, other_end, _ in claimed):
                    continue
                claimed.append((start, end, name))

        if not claimed:
            return result

        claimed.sort()
        pieces: list[str] = []
        cursor = 0
        for start, end, name in claimed:
            pieces.append(text[cursor:start])
            pieces.append(f"[redacted:{name}:{end - start}]")
            result.counts[name] = result.counts.get(name, 0) + 1
            result.redacted_characters += end - start
            cursor = end
        pieces.append(text[cursor:])
        result.text = "".join(pieces)
        return result


def redactor_for(repository_root) -> Redactor | None:
    """The redactor a repository's configuration asks for, or `None` when disabled.

    Configuration failures are raised, not swallowed. A repository that declared a
    redaction rule and got no redaction has a false sense of safety, which is worse than
    a loud failure at capture time.
    """
    from .config import load_config

    settings = load_config(repository_root).evidence
    if not settings.redact_secrets:
        return None
    extra = [(rule.name, rule.pattern) for rule in settings.redact]
    return Redactor(extra, include_builtins=settings.redact_builtins)
