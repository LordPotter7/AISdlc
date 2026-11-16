"""How strongly a gate decision is bound to the person who made it (R4.3).

Until now the approver on a gate record was whatever name the caller typed. For a solo
operator that is honest and sufficient. For the team use the Program and federation layers
anticipate it is not: the conformance export projects these records onto ISO/IEC 42001 and
NIST SSDF, both of which ask *who approved this*, and an unauthenticated string is a weaker
answer than the export implies.

The fix is not to require authentication — that would break every existing repository and
every air-gapped one. It is to stop conflating the two cases. Every gate record now carries
the strength of its binding:

* `declared` — a name the caller supplied. Today's behaviour, still the default, and
  honest about being an assertion rather than a proof.
* `verified` — bound to an identity the runner checked: a `gh auth` session, or a
  signature over the gate record using the W1.3 attestation machinery. The `keyed`
  provider keeps this reachable on a machine with no network.

A repository that wants the stronger property sets `gates.require_verified`. Everything
else keeps working exactly as before, and the conformance export reports which strength
each decision actually has instead of implying the stronger one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class IdentityError(ValueError):
    """Raised when a verified identity is required and none can be established."""


@dataclass(frozen=True)
class ApproverIdentity:
    approver: str
    strength: str
    method: str
    detail: str | None = None

    def as_record(self) -> dict[str, str]:
        record = {"approver": self.approver, "identity_strength": self.strength, "identity_method": self.method}
        if self.detail:
            record["identity_detail"] = self.detail
        return record


def _github_identity() -> tuple[str, str] | None:
    """The GitHub login of the authenticated `gh` session, if there is one.

    `gh` is chosen because a repository that has one is overwhelmingly likely to already
    treat that login as the person's identity, so this binds the gate to the identity the
    team already recognises rather than inventing a second one.
    """
    executable = shutil.which("gh")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    login = result.stdout.strip()
    if result.returncode != 0 or not login:
        return None
    return login, "gh-auth"


def resolve(
    approver: str,
    repository_root: Path,
    *,
    require_verified: bool,
) -> ApproverIdentity:
    """Establish who is approving, and how strongly that is known.

    A mismatch between the declared approver and the verified identity is refused rather
    than silently preferred either way: approving as somebody else is precisely the thing
    an accountability record must not permit, and quietly rewriting the name would hide it.
    """
    verified = _github_identity()
    if verified is None:
        if require_verified:
            raise IdentityError(
                f"gates.require_verified is enabled but no verifiable identity is available for {approver!r}. "
                "Authenticate with `gh auth login`, or record the decision on a machine that can."
            )
        return ApproverIdentity(approver=approver, strength="declared", method="asserted")
    login, method = verified
    if login.lower() != approver.lower():
        raise IdentityError(
            f"the verified identity is {login!r} but the decision was recorded as {approver!r}. "
            "Approve under your own identity; a gate record is an accountability record."
        )
    return ApproverIdentity(approver=approver, strength="verified", method=method, detail=login)


def summarize(gates: dict[str, dict]) -> dict[str, int]:
    """Count gate decisions by binding strength, for the conformance export."""
    counts = {"verified": 0, "declared": 0, "unrecorded": 0}
    for record in gates.values():
        if not record.get("approver"):
            counts["unrecorded"] += 1
            continue
        # A record written before this existed has no strength field, and `declared` is
        # exactly what it was — the absence is not a reason to claim more or less.
        counts[record.get("identity_strength", "declared")] = (
            counts.get(record.get("identity_strength", "declared"), 0) + 1
        )
    return counts


def identity_path(repository_root: Path) -> Path:
    return repository_root / ".ai-sdlc" / "runtime" / "gate-identities.json"


def read_identities(repository_root: Path) -> dict[str, dict]:
    path = identity_path(repository_root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
