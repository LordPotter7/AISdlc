"""Bridge protocol: envelopes, errors, sanitization.

Ported from the AI Governance desktop bridge (protocol 1.0) so both desktop apps share
one wire contract and one renderer client.

Every response carries the same envelope so the renderer can check
compatibility, attribute data to a project and generation, and display
freshness and omissions without inspecting the payload.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aisdlc.desktop import PROTOCOL_VERSION

#: Schema versions of the individual projections. A renderer that knows an
#: older major version of a projection must show a compatibility error.
SCHEMA_VERSIONS: dict[str, str] = {
    "hello": "1.0",
    "context": "1.0",
    "portfolio": "1.0",
    "intent": "1.0",
    "gate": "1.0",
    "spec": "1.0",
    "trace": "1.0",
    "story": "1.0",
    "events": "1.0",
    "diagnostics": "1.0",
    "operation": "1.0",
}

#: Freshness labels shown in the workspace footer (plan section 6).
FRESHNESS = ("connecting", "current", "updating", "stale", "unavailable", "error")

#: Longest string the renderer is ever handed. Longer values are cut and the
#: omission is recorded in the envelope.
MAX_TEXT = 2000

# C0/C1 controls except tab/newline, plus bidi overrides and zero-width joiners
# that can disguise what a path or reason says (plan section 8).
_UNSAFE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"​-‏‪-‮⁦-⁩﻿]"
)


def as_dict(value: Any) -> dict[str, Any]:
    """``value`` when it is a mapping, else an empty dict (typed narrowing)."""
    return dict(value) if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``"3 files"`` / ``"1 file"``: display text the app shows verbatim."""
    return f"{count} {singular if count == 1 else (plural_form or singular + 's')}"


def sanitize_text(value: Any, *, limit: int = MAX_TEXT) -> str:
    """Plain text safe to render: no controls, no bidi tricks, bounded.

    The renderer treats everything as text, never markup; this removes the
    characters that can still mislead a reader in plain text.
    """
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFC", text)
    text = _UNSAFE.sub("�", text)
    if len(text) > limit:
        return text[:limit] + f"… [+{len(text) - limit} chars]"
    return text


def sanitize_value(value: Any, *, limit: int = MAX_TEXT, depth: int = 0) -> Any:
    """Sanitize recursively through JSON-shaped values (depth-bounded)."""
    if depth > 12:
        return "[nested too deeply]"
    if isinstance(value, str):
        return sanitize_text(value, limit=limit)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [sanitize_value(item, limit=limit, depth=depth + 1) for item in value[:500]]
    if isinstance(value, dict):
        return {
            sanitize_text(str(key), limit=200): sanitize_value(item, limit=limit, depth=depth + 1)
            for key, item in list(value.items())[:500]
        }
    return sanitize_text(value, limit=limit)


class BridgeError(Exception):
    """A structured, renderable failure.

    ``code`` is stable and machine-readable; ``message`` is safe to display;
    ``detail`` is optional structured context. ``retryable`` tells the shell
    whether reconciliation may clear it.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": sanitize_text(self.message),
            "detail": sanitize_value(self.detail),
            "retryable": self.retryable,
        }


@dataclass
class Omission:
    """Something the projection deliberately left out, and why."""

    what: str
    reason: str
    count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"what": self.what, "reason": self.reason}
        if self.count is not None:
            data["count"] = self.count
        return data


@dataclass
class Envelope:
    """The common response frame (plan section 5, "New bridge contract")."""

    request_id: str
    kind: str
    project_id: str = ""
    worktree_id: str = ""
    generation: int = 0
    collected_at: str = field(default_factory=now_iso)
    source_fingerprint: str = ""
    freshness: str = "current"
    omissions: list[Omission] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cursor: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)

    def wrap(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.freshness not in FRESHNESS:
            raise ValueError(f"unknown freshness {self.freshness!r}")
        return {
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": SCHEMA_VERSIONS.get(self.kind, "1.0"),
            "kind": self.kind,
            "request_id": self.request_id,
            "project_id": self.project_id,
            "worktree_id": self.worktree_id,
            "generation": self.generation,
            "collected_at": self.collected_at,
            "source_fingerprint": self.source_fingerprint,
            "freshness": self.freshness,
            "omissions": [o.to_dict() for o in self.omissions],
            "warnings": [sanitize_text(w) for w in self.warnings],
            "cursor": self.cursor,
            "scope": sanitize_value(self.scope),
            "error": None,
            "payload": payload,
        }

    def fail(self, error: BridgeError) -> dict[str, Any]:
        data = self.wrap({})
        data["freshness"] = "error"
        data["error"] = error.to_dict()
        return data


def enum_or_unknown(value: Any, allowed: tuple[str, ...] | frozenset[str]) -> dict[str, Any]:
    """Keep future/unknown enum values displayable instead of dropping them.

    Returns ``{"value": <str>, "known": bool}``; the renderer shows unknown
    values verbatim with an "unknown" badge (plan section 5).
    """
    text = sanitize_text(value, limit=64)
    return {"value": text, "known": text in allowed}
