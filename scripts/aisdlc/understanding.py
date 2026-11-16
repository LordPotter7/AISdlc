"""Project understanding for brownfield entry (W5.1).

`baseline` pins what an existing system *does*. Nothing pinned what an existing system
*is*: its entry points, module responsibilities, boundaries, data stores, or risk
concentrations. README Path B asked a brownfield adopter to fill in
`.ai-sdlc/project-context.md` from a blank page, which in a system nobody currently
understands is the paralysis problem `baseline` was built to avoid.

This module records that structural understanding as **one Markdown file** a person reads
and the runner validates. There is deliberately no machine-readable sidecar: two files
that can disagree, where the one a human reads is the one that drifts. Everything the
runner needs is in the file's own frontmatter or already in git.

An understanding has exactly two possible sources, never blended:

- `source: repo` — derived from the codebase. Anchored to the artifact that actually
  runs. The claim can be wrong about intent, but not about existence.
- `source: document` — derived from declared Markdown inputs. Anchored to a *description*
  of the system. Verifiable only against what somebody wrote, which is why such a file
  must carry an unverified-against-code notice: a perfectly fresh document-sourced
  understanding may describe a system that no longer exists.

That distinction is the same one the framework already draws between authoritative
evidence (CI, for the commit) and provisional evidence (local). The difference is not
quality of effort but what stands behind the claim, so it is labelled rather than
quietly averaged away.

The artifact is fixed at the weakest control tier, permanently: advisory heuristics, no
gate, no blocking, no convergence check. Its payload is the **Proposed checks** section —
candidate probes and fitness functions a human copies into `probes.yaml` one line at a
time. There is no promotion command, because a proposed check acquiring enforcing power
without anyone reading it is exactly the failure the tier exists to prevent.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .storage import atomic_write_text
from .vendor import yaml


class UnderstandingError(RuntimeError):
    """Raised when an understanding document cannot be recorded or read."""


UNDERSTANDING_PATH = Path(".ai-sdlc") / "project-understanding.md"

SOURCES = ("repo", "document")

# The fixed section order `record` checks for completeness. Divergence is written only by
# a second analysis that was given an earlier understanding as input, so it is optional;
# everything else is required in both modes. The Uncertainty register is last and never
# optional: an analysis that admits its edges is more useful than one that performs
# completeness, in the same spirit as Class N reporting "zero observed successes, never
# zero risk".
REQUIRED_SECTIONS = (
    "Purpose and domain vocabulary",
    "Entry points",
    "Module map and responsibilities",
    "Boundaries",
    "Data model and persistence",
    "Cross-cutting concerns",
    "Build, test, and CI topology",
    "Risk concentrations",
    "Constraints and architectural rules",
    "Proposed checks",
    "Uncertainty register",
)
OPTIONAL_SECTIONS = ("Divergence",)

# Sections exempt from the recommendation scan. "Constraints and architectural rules"
# restates rules the code or the documents already impose, and "Proposed checks" is a
# list of proposals by construction — scanning either would reject correct documents.
RECOMMENDATION_EXEMPT_SECTIONS = ("Constraints and architectural rules", "Proposed checks")

# The cartographer reports what *is* — or in document mode, what the documents *say* —
# never what *ought to be*. The moment it recommends, it has started doing the
# specification's job without a gate. This is a lexical tripwire, not a proof: it catches
# the plain-language forms cheaply, and a determined circumlocution still gets past it.
RECOMMENDATION_MARKERS = (
    "we should",
    "you should",
    "i recommend",
    "we recommend",
    "it is recommended",
    "recommendation:",
    "ought to be",
    "i suggest",
    "we suggest",
    "would be better",
    "should be refactored",
    "should be rewritten",
    "should be replaced",
    "needs to be refactored",
    "we propose",
    "i propose",
)

UNVERIFIED_NOTICE_MARKER = "unverified against the codebase"

# A bracket group that is not a Markdown link target. Checkboxes (`- [ ]`, `- [x]`) and
# link text (`[label](url)`) both survive this pattern and are filtered by path shape.
_BRACKET = re.compile(r"\[([^\[\]\n]+)\](?!\()")
_ANCHOR_PART = re.compile(r"^(?P<path>[^\s:*?\"<>|]+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^##\s+(?:\d+\.\s+)?(?P<title>.+?)\s*$")


@dataclass(frozen=True)
class Anchor:
    """One `[path:lines]` citation, with where in the prose it was written."""

    path: str
    start: int | None
    end: int | None
    line: int

    def render(self) -> str:
        if self.start is None:
            return self.path
        if self.end is None:
            return f"{self.path}:{self.start}"
        return f"{self.path}:{self.start}-{self.end}"


@dataclass
class UnderstandingDocument:
    path: Path
    frontmatter: dict[str, Any]
    body: str
    anchors: list[Anchor] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def source(self) -> str:
        return str(self.frontmatter.get("source", ""))

    @property
    def commit(self) -> str | None:
        value = self.frontmatter.get("commit")
        return str(value) if value else None

    @property
    def inputs(self) -> list[str]:
        return [str(item) for item in self.frontmatter.get("inputs", []) or []]

    def anchored_paths(self) -> list[str]:
        seen: dict[str, None] = {}
        for anchor in self.anchors:
            seen.setdefault(anchor.path, None)
        return list(seen)


def _git(repository_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=False,
        text=True,
        capture_output=True,
    )


def _normalize(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./") if path.startswith("./") else path.replace("\\", "/").strip()


def parse_document(path: Path) -> UnderstandingDocument:
    """Split an understanding file into frontmatter, body, anchors, and sections."""
    if not path.exists():
        raise UnderstandingError(f"understanding document not found: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise UnderstandingError("understanding document must open with YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise UnderstandingError("understanding document has an unterminated frontmatter block")
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as error:
        raise UnderstandingError(f"frontmatter is not valid YAML: {error}") from error
    if not isinstance(frontmatter, dict):
        raise UnderstandingError("frontmatter must be a YAML mapping")
    body = parts[2].lstrip("\n")
    document = UnderstandingDocument(path=path, frontmatter=frontmatter, body=body)
    document.anchors = extract_anchors(body)
    document.sections = extract_sections(body)
    return document


def extract_anchors(body: str) -> list[Anchor]:
    """Pull every `[path:lines]` citation out of the prose, ignoring fenced code.

    Anchors are ignored inside fenced blocks because a code sample is an illustration,
    not a claim about this repository, and validating its brackets would reject correct
    documents that quote example output.
    """
    anchors: list[Anchor] = []
    fenced = False
    for number, line in enumerate(body.splitlines(), start=1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _BRACKET.finditer(line):
            for raw in match.group(1).split(","):
                candidate = raw.strip()
                if not _looks_like_anchor(candidate):
                    continue
                parsed = _ANCHOR_PART.match(candidate)
                if not parsed:
                    continue
                start = int(parsed.group("start")) if parsed.group("start") else None
                end = int(parsed.group("end")) if parsed.group("end") else None
                anchors.append(Anchor(path=_normalize(parsed.group("path")), start=start, end=end, line=number))
    return anchors


def _looks_like_anchor(candidate: str) -> bool:
    """Distinguish a citation from ordinary bracketed prose.

    A checkbox (`[ ]`, `[x]`), a footnote marker, or an aside in square brackets must not
    be mistaken for a claim about a file. Requiring a path separator or a file extension
    and forbidding whitespace is enough to separate the two without a filesystem lookup.
    """
    if not candidate or any(character.isspace() for character in candidate):
        return False
    if "/" not in candidate and "\\" not in candidate and "." not in candidate:
        return False
    return bool(_ANCHOR_PART.match(candidate))


def extract_sections(body: str) -> dict[str, str]:
    """Map each `##` heading to its content, tolerating an optional `N.` number prefix."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(line)
        if heading:
            if current is not None:
                sections[current] = "\n".join(buffer)
            current = heading.group("title")
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer)
    return sections


def section_order(body: str) -> list[str]:
    return list(extract_sections(body))


class UnderstandingService:
    """`record` and `status` for `.ai-sdlc/project-understanding.md`."""

    def __init__(self, repository_root: Path, *, document_path: Path | None = None) -> None:
        self.repository_root = repository_root.resolve()
        # Whether the caller named the file. A missing file the *caller* asked for is a
        # usage error; a missing file at the default path is simply "none recorded yet",
        # and `status` reports the two differently.
        self.document_declared = document_path is not None
        self.document_path = (document_path or (self.repository_root / UNDERSTANDING_PATH)).resolve()

    # -- record ---------------------------------------------------------------

    def record(self) -> dict[str, Any]:
        """Validate the document, then stamp the pin into its own frontmatter.

        Validation runs before the stamp so a rejected document is never pinned. A pin on
        an invalid document would be worse than no pin: it would make an unchecked file
        look checked.
        """
        document = parse_document(self.document_path)
        errors = self.validate(document)
        if errors:
            raise UnderstandingError("; ".join(errors))
        commit = self._head_commit()
        recorded_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._stamp(document, commit=commit, recorded_at=recorded_at)
        return {
            "path": str(self.document_path),
            "source": document.source,
            "commit": commit,
            "recorded_at": recorded_at,
            "anchors": len(document.anchors),
            "anchored_paths": len(document.anchored_paths()),
            "inputs": document.inputs,
            "proposed_checks": _count_proposed_checks(document),
        }

    def validate(self, document: UnderstandingDocument) -> list[str]:
        errors: list[str] = []
        errors.extend(self._validate_frontmatter(document))
        if errors:
            # Anchor and notice checks depend on a declared source and, in document mode,
            # on declared inputs. Reporting them against an unknown source would produce
            # misleading messages.
            return errors
        errors.extend(self._validate_sections(document))
        errors.extend(self._validate_notice(document))
        errors.extend(self._validate_anchors(document))
        errors.extend(self._validate_observational(document))
        return errors

    def _validate_frontmatter(self, document: UnderstandingDocument) -> list[str]:
        errors: list[str] = []
        source = document.frontmatter.get("source")
        if source not in SOURCES:
            errors.append(f"frontmatter `source` must be one of {list(SOURCES)}, got {source!r}")
            return errors
        if source == "document":
            inputs = document.frontmatter.get("inputs")
            if not isinstance(inputs, list) or not inputs:
                errors.append("document mode requires a non-empty `inputs` list in frontmatter")
                return errors
            for item in inputs:
                candidate = self._resolve(str(item))
                if candidate is None:
                    errors.append(f"declared input escapes the repository: {item}")
                elif not candidate.exists():
                    errors.append(f"declared input does not exist: {item}")
                elif candidate.is_file() and candidate.suffix.lower() != ".md":
                    errors.append(f"declared input is not a Markdown file: {item}")
        elif document.frontmatter.get("inputs"):
            errors.append("repo mode does not take `inputs`: the declared source is the repository itself")
        return errors

    def _validate_sections(self, document: UnderstandingDocument) -> list[str]:
        errors: list[str] = []
        present = section_order(document.body)
        known = set(REQUIRED_SECTIONS) | set(OPTIONAL_SECTIONS)
        missing = [name for name in REQUIRED_SECTIONS if name not in present]
        if missing:
            errors.append("missing required sections: " + ", ".join(missing))
        unknown = [name for name in present if name not in known]
        if unknown:
            errors.append("unrecognized sections: " + ", ".join(unknown))
        if not missing:
            expected = [name for name in REQUIRED_SECTIONS]
            actual = [name for name in present if name in set(REQUIRED_SECTIONS)]
            if actual != expected:
                errors.append("required sections are out of order: expected " + " -> ".join(expected))
        return errors

    def _validate_notice(self, document: UnderstandingDocument) -> list[str]:
        if document.source != "document":
            return []
        preamble = document.body.split("\n## ", 1)[0].lower()
        if UNVERIFIED_NOTICE_MARKER not in preamble:
            return [
                "document mode requires an unverified-against-code notice in the opening lines "
                f"containing the phrase {UNVERIFIED_NOTICE_MARKER!r}"
            ]
        return []

    def _validate_anchors(self, document: UnderstandingDocument) -> list[str]:
        errors: list[str] = []
        allowed = self._allowed_roots(document)
        for anchor in document.anchors:
            resolved = self._resolve(anchor.path)
            if resolved is None:
                errors.append(f"line {anchor.line}: anchor escapes the repository: {anchor.render()}")
                continue
            if not self._within(resolved, allowed):
                # In document mode this is a provenance violation, not a bonus: a claim
                # anchored to a source file means the run silently read code and the
                # document's `source: document` label no longer describes it.
                scope = "the declared inputs" if document.source == "document" else "the repository"
                errors.append(f"line {anchor.line}: anchor points outside {scope}: {anchor.render()}")
                continue
            if not resolved.exists():
                errors.append(f"line {anchor.line}: anchored path does not exist: {anchor.render()}")
                continue
            errors.extend(self._validate_line_range(anchor, resolved))
        return errors

    def _validate_line_range(self, anchor: Anchor, resolved: Path) -> list[str]:
        if anchor.start is None:
            return []
        if resolved.is_dir():
            return [f"line {anchor.line}: a line range cannot anchor to a directory: {anchor.render()}"]
        if anchor.end is not None and anchor.end < anchor.start:
            return [f"line {anchor.line}: anchor line range is inverted: {anchor.render()}"]
        try:
            total = len(resolved.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as error:
            return [f"line {anchor.line}: anchored path is unreadable: {anchor.render()} ({error})"]
        highest = anchor.end or anchor.start
        if highest > total:
            return [
                f"line {anchor.line}: anchor cites lines past end of file ({anchor.render()}, file has {total} lines)"
            ]
        return []

    def _validate_observational(self, document: UnderstandingDocument) -> list[str]:
        errors: list[str] = []
        for name, content in document.sections.items():
            if name in RECOMMENDATION_EXEMPT_SECTIONS:
                continue
            for offset, line in enumerate(content.splitlines(), start=1):
                lowered = line.lower()
                for marker in RECOMMENDATION_MARKERS:
                    if marker in lowered:
                        errors.append(
                            f"section {name!r} contains a recommendation rather than an observation "
                            f"({marker!r} at section line {offset}); the analysis reports what is, "
                            "not what ought to be"
                        )
                        break
        return errors

    def _allowed_roots(self, document: UnderstandingDocument) -> list[Path]:
        if document.source != "document":
            return [self.repository_root]
        roots: list[Path] = []
        for item in document.inputs:
            resolved = self._resolve(item)
            if resolved is not None:
                roots.append(resolved)
        return roots

    def _resolve(self, candidate: str) -> Path | None:
        """Resolve a repository-relative path, refusing anything that escapes the root."""
        raw = _normalize(candidate)
        if not raw or Path(raw).is_absolute():
            return None
        resolved = (self.repository_root / raw).resolve()
        try:
            resolved.relative_to(self.repository_root)
        except ValueError:
            return None
        return resolved

    @staticmethod
    def _within(resolved: Path, allowed: list[Path]) -> bool:
        for root in allowed:
            if resolved == root:
                return True
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return True
        return False

    def _stamp(self, document: UnderstandingDocument, *, commit: str, recorded_at: str) -> None:
        frontmatter = dict(document.frontmatter)
        frontmatter["commit"] = commit
        frontmatter["recorded_at"] = recorded_at
        ordered = {key: frontmatter[key] for key in ("source", "commit", "recorded_at") if key in frontmatter}
        for key, value in frontmatter.items():
            ordered.setdefault(key, value)
        rendered = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False).strip()
        atomic_write_text(self.document_path, f"---\n{rendered}\n---\n\n{document.body}")

    def _head_commit(self) -> str:
        result = _git(self.repository_root, "rev-parse", "HEAD")
        if result.returncode != 0:
            raise UnderstandingError(result.stderr.strip() or "cannot resolve HEAD; is this a git repository?")
        return result.stdout.strip()

    # -- status ---------------------------------------------------------------

    def status(self, *, drift_threshold: float = 0.34) -> dict[str, Any]:
        """Report `fresh`, `drifting`, or `stale` from git, with the claims that moved.

        No stored hashes. The pin in frontmatter plus `git diff` over the anchored paths
        answers "has anything this document describes changed since it was written?",
        which is the whole question staleness asks.
        """
        if not self.document_path.exists() and not self.document_declared:
            # "No understanding has been recorded" is an answer to `status`, not a failure
            # to answer it. `/aisdlc:understand` injects this command before doing its
            # work, so raising here made the command fail on the one run that matters —
            # the first, when the document cannot exist yet.
            return {
                "path": str(self.document_path),
                "source": None,
                "status": "absent",
                "pinned_commit": None,
                "head_commit": None,
                "recorded_at": None,
                "anchored_paths": 0,
                "changed_paths": [],
                "drift_ratio": 0.0,
                "drift_threshold": drift_threshold,
                "moved_claims": [],
                "freshness_means": (
                    "no understanding has been recorded yet; run `/aisdlc:understand repo` "
                    "or `/aisdlc:understand document <file.md>` to produce one"
                ),
            }
        document = parse_document(self.document_path)
        if document.source not in SOURCES:
            raise UnderstandingError(f"frontmatter `source` must be one of {list(SOURCES)}")
        commit = document.commit
        if not commit:
            raise UnderstandingError("understanding document is unpinned; run `ai-sdlc understand record` first")
        if _git(self.repository_root, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
            raise UnderstandingError(f"pinned commit is unknown to this repository: {commit}")
        paths = document.anchored_paths()
        head = self._head_commit()
        changed = self._changed_paths(commit, head, paths) if paths else []
        ratio = (len(changed) / len(paths)) if paths else 0.0
        if not changed:
            state = "fresh"
        elif ratio >= drift_threshold:
            state = "stale"
        else:
            state = "drifting"
        moved = [
            {"path": anchor.path, "anchor": anchor.render(), "line": anchor.line}
            for anchor in document.anchors
            if anchor.path in set(changed)
        ]
        return {
            "path": str(self.document_path),
            "source": document.source,
            "status": state,
            "pinned_commit": commit,
            "head_commit": head,
            "recorded_at": document.frontmatter.get("recorded_at"),
            "anchored_paths": len(paths),
            "changed_paths": sorted(changed),
            "drift_ratio": round(ratio, 4),
            "drift_threshold": drift_threshold,
            "moved_claims": moved,
            # A document-mode understanding is fresh when its *sources* have not moved.
            # Freshness never means the code still matches those sources, and the reader
            # is told so rather than left to infer it.
            "freshness_means": (
                "the declared input documents have not changed; the code may still have diverged from them"
                if document.source == "document"
                else "the anchored source files have not changed"
            ),
        }

    def _changed_paths(self, base: str, head: str, paths: list[str]) -> list[str]:
        if base == head:
            return []
        result = _git(self.repository_root, "diff", "--name-only", f"{base}..{head}", "--", *paths)
        if result.returncode != 0:
            raise UnderstandingError(result.stderr.strip() or "git diff failed")
        touched = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        changed: list[str] = []
        for path in paths:
            prefix = path if path.endswith("/") else f"{path}/"
            if path in touched or any(item.startswith(prefix) for item in touched):
                changed.append(path)
        return changed


def _count_proposed_checks(document: UnderstandingDocument) -> int:
    section = document.sections.get("Proposed checks", "")
    return sum(1 for line in section.splitlines() if line.lstrip().startswith(("- [ ]", "- [x]", "-", "*")))


def load_for_context_pack(
    repository_root: Path,
    *,
    drift_threshold: float = 0.34,
    include_when_stale: bool = False,
) -> dict[str, Any] | None:
    """Return the understanding for a Stage 4 context pack, or `None` when unusable.

    Stale orientation is worse than none: a confident paragraph about a module that moved
    invites an implementing agent to inherit a wrong premise from a trusted-looking
    source, and a blank page at least invites checking. A document-mode understanding
    always travels with its mode and its unverified-against-code notice attached, for the
    same reason provisional evidence is labelled rather than quietly mixed with
    authoritative evidence.
    """
    service = UnderstandingService(repository_root)
    if not service.document_path.exists():
        return None
    try:
        report = service.status(drift_threshold=drift_threshold)
        document = parse_document(service.document_path)
    except UnderstandingError as error:
        return {"status": "unreadable", "reason": str(error)}
    if report["status"] == "stale" and not include_when_stale:
        return {
            "status": "stale",
            "source": report["source"],
            "excluded": True,
            "reason": "understanding is stale; excluded because stale orientation is worse than none",
            "changed_paths": report["changed_paths"],
        }
    return {
        "status": report["status"],
        "source": report["source"],
        "excluded": False,
        "pinned_commit": report["pinned_commit"],
        "recorded_at": report["recorded_at"],
        "drift_ratio": report["drift_ratio"],
        "freshness_means": report["freshness_means"],
        "unverified_against_code": document.source == "document",
        "moved_claims": report["moved_claims"],
        "content": document.body,
    }
