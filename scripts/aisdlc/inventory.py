"""What an existing repository contains, read from git (W5.2).

`understand` records what a system *is* as prose a person reads, anchored to code, and
`baseline` pins what it *does*. Both start from a question nobody should answer by hand
in an unfamiliar repository: what is in here? Which languages, which packages, where the
tests are and how they run, who owns what, where the CI lives, and how the code divides
into modules.

This module answers that question as facts, never as claims. It reads only the files git
tracks at HEAD (and a few manifests among them), runs nothing, and writes nothing. The
same commit always gives the same answer, so there is no stored inventory to drift from
the code and no second file to disagree with the understanding: the scan is recomputed
whenever it is asked for.

Its suggestions stay suggestions. A detected test command is a *proposed* probe, shown
next to the evidence it was inferred from; it acquires meaning only when a person copies
it into a probes file and runs `baseline capture`, the same deliberate step the
understanding's Proposed checks require.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FILES = 200_000
MAX_MODULES = 200

LANGUAGES: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".cs": "C#",
    ".fs": "F#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".dart": "Dart",
    ".lua": "Lua",
    ".r": "R",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".sql": "SQL",
    ".sh": "Shell",
    ".bash": "Shell",
    ".ps1": "PowerShell",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".less": "CSS",
    ".tf": "Terraform",
    ".proto": "Protobuf",
    ".ipynb": "Jupyter",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
}
# Documentation and configuration are counted, but a module is not "written in" them.
NOT_CODE = {"Markdown", "reStructuredText", "YAML", "JSON", "TOML"}

MANIFESTS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("setup.cfg", "python"),
    ("Pipfile", "python"),
    ("requirements*.txt", "python"),
    ("package.json", "node"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("pom.xml", "maven"),
    ("build.gradle", "gradle"),
    ("build.gradle.kts", "gradle"),
    ("*.csproj", "dotnet"),
    ("*.sln", "dotnet"),
    ("Gemfile", "ruby"),
    ("composer.json", "php"),
    ("mix.exs", "elixir"),
    ("pubspec.yaml", "dart"),
)
CI: tuple[tuple[str, str], ...] = (
    (".github/workflows/*.yml", "GitHub Actions"),
    (".github/workflows/*.yaml", "GitHub Actions"),
    (".gitlab-ci.yml", "GitLab CI"),
    ("azure-pipelines.yml", "Azure Pipelines"),
    ("Jenkinsfile", "Jenkins"),
    (".circleci/config.yml", "CircleCI"),
    ("bitbucket-pipelines.yml", "Bitbucket Pipelines"),
    (".buildkite/pipeline.yml", "Buildkite"),
    (".travis.yml", "Travis CI"),
)
CODEOWNERS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")
# Folders that hold many modules rather than being one.
CONTAINERS = {
    "packages",
    "apps",
    "services",
    "libs",
    "modules",
    "src",
    "components",
    "crates",
    "cmd",
    "internal",
    "pkg",
    "plugins",
}
KINDS = {
    "tests": {"tests", "test", "spec", "specs", "__tests__", "e2e", "integration_tests"},
    "docs": {"docs", "doc", "documentation", "book", "site"},
    "infrastructure": {
        ".github",
        "deploy",
        "deployment",
        "infra",
        "k8s",
        "kubernetes",
        "helm",
        "terraform",
        "docker",
        "ops",
        "ci",
        "scripts",
    },
}
PYTHON_TEST = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py)$")
JS_TEST = re.compile(r"(^|/)(__tests__/.*\.[cm]?[jt]sx?|[^/]*\.(test|spec)\.[cm]?[jt]sx?)$")
ENTRY_NAMES = {
    "__main__.py",
    "main.py",
    "app.py",
    "manage.py",
    "server.py",
    "wsgi.py",
    "asgi.py",
    "main.go",
    "main.rs",
    "Program.cs",
    "index.js",
    "index.ts",
    "server.js",
    "server.ts",
}


class InventoryError(RuntimeError):
    """Raised when a repository cannot be read."""


def tracked_files(repository_root: Path) -> tuple[list[str], str | None]:
    """The files git tracks, as POSIX paths, and HEAD's commit (None before the first commit)."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=repository_root, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise InventoryError(f"{repository_root} is not a git repository the scan can read: {error}") from None
    files = [path for path in listed.stdout.decode("utf-8", errors="replace").split("\0") if path]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository_root, capture_output=True, text=True)
    return sorted(files)[:MAX_FILES], (head.stdout.strip() or None) if head.returncode == 0 else None


def _read(repository_root: Path, relative: str, limit: int = 256_000) -> str:
    try:
        with (repository_root / relative).open("rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _matches(path: str, pattern: str) -> bool:
    name = PurePosixPath(path).name
    return fnmatch.fnmatch(path, pattern) if "/" in pattern else fnmatch.fnmatch(name, pattern)


def _language(path: str) -> str | None:
    return LANGUAGES.get(PurePosixPath(path).suffix.lower())


def _owner_rules(repository_root: Path, files: set[str]) -> tuple[str | None, list[tuple[str, list[str]]]]:
    """CODEOWNERS rules in file order; the last matching rule wins, as on GitHub and GitLab."""
    for candidate in CODEOWNERS:
        if candidate in files:
            rules = []
            for line in _read(repository_root, candidate).splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith("["):
                    continue
                pattern, *owners = line.split()
                if owners:
                    rules.append((pattern, owners))
            return candidate, rules
    return None, []


def owners_of(path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    """The owners CODEOWNERS assigns to a path (a file, or a folder given with a trailing `/`)."""
    found: list[str] = []
    for pattern, owners in rules:
        anchored = pattern.startswith("/")
        body = pattern.strip("/")
        folder = pattern.endswith("/")
        if body in {"", "*"}:
            found = owners
            continue
        target = path.rstrip("/")
        candidates = (
            [target] if anchored else [target, *(target.split("/", i)[-1] for i in range(1, target.count("/") + 1))]
        )
        hit = any(
            fnmatch.fnmatch(item, body)
            or item.startswith(body + "/")
            or (not folder and fnmatch.fnmatch(item, body + "/*"))
            for item in candidates
        )
        if hit:
            found = owners
    return found


def _test_suites(repository_root: Path, files: list[str], manifests: list[dict[str, str]]) -> list[dict[str, Any]]:
    suites: list[dict[str, Any]] = []
    names = set(files)

    python_tests = [path for path in files if PYTHON_TEST.search(path)]
    if python_tests:
        roots = Counter(path.split("/")[0] if "/" in path else "." for path in python_tests)
        config = [
            path for path in ("pytest.ini", "conftest.py", "tox.ini", "setup.cfg", "pyproject.toml") if path in names
        ]
        declared = any(
            "pytest" in _read(repository_root, path)
            for path in [*config, *[m["path"] for m in manifests if m["ecosystem"] == "python"]]
        ) or any(path.endswith("conftest.py") for path in files)
        framework = "pytest" if declared else "unittest"
        for rank, (root, count) in enumerate(roots.most_common(4)):
            # One file that happens to be named like a test is not a suite, unless it is the only one.
            if root == "." or (rank and count < 2):
                continue
            command = f"python -m pytest -q {root}" if declared else f"python -m unittest discover -s {root}"
            suites.append(
                {
                    "framework": framework,
                    "ecosystem": "python",
                    "root": root,
                    "command": command,
                    "test_files": count,
                    "evidence": sorted({*config, *[p for p in python_tests if p.startswith(root + "/")][:3]})[:6],
                }
            )

    for manifest in (m for m in manifests if m["path"].endswith("package.json")):
        try:
            package = json.loads(_read(repository_root, manifest["path"]))
        except ValueError:
            continue
        script = str((package.get("scripts") or {}).get("test") or "")
        if not script or "no test specified" in script:
            continue
        folder = str(PurePosixPath(manifest["path"]).parent)
        dependencies = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
        framework = next(
            (
                name
                for name in ("vitest", "jest", "mocha", "ava", "playwright", "cypress")
                if name in dependencies or name in script
            ),
            "npm test",
        )
        prefix = "" if folder == "." else f"{folder}/"
        tool = "pnpm" if f"{prefix}pnpm-lock.yaml" in names else "yarn" if f"{prefix}yarn.lock" in names else "npm"
        command = (
            f"{tool} test"
            if folder == "."
            else f"{tool} --prefix {folder} test"
            if tool == "npm"
            else f"{tool} --cwd {folder} test"
        )
        count = sum(1 for path in files if path.startswith(prefix) and JS_TEST.search(path))
        suites.append(
            {
                "framework": framework,
                "ecosystem": "node",
                "root": folder,
                "command": command,
                "test_files": count,
                "evidence": [manifest["path"]],
            }
        )

    by_ecosystem = {
        "rust": ("cargo", "cargo test"),
        "go": ("go test", "go test ./..."),
        "maven": ("maven", "mvn -q test"),
        "dotnet": ("dotnet", "dotnet test"),
        "elixir": ("mix", "mix test"),
        "dart": ("dart", "dart test"),
    }
    seen: set[str] = set()
    for manifest in manifests:
        ecosystem = manifest["ecosystem"]
        folder = str(PurePosixPath(manifest["path"]).parent)
        if ecosystem == "gradle" and folder not in seen:
            seen.add(folder)
            wrapper = "./gradlew" if (f"{folder}/gradlew" if folder != "." else "gradlew") in names else "gradle"
            suites.append(
                {
                    "framework": "gradle",
                    "ecosystem": "gradle",
                    "root": folder,
                    "command": f"{wrapper} test",
                    "test_files": None,
                    "evidence": [manifest["path"]],
                }
            )
        elif ecosystem in by_ecosystem and (ecosystem, folder) not in {(s["ecosystem"], s["root"]) for s in suites}:
            framework, command = by_ecosystem[ecosystem]
            if folder != ".":
                command = f"cd {folder} && {command}"
            suites.append(
                {
                    "framework": framework,
                    "ecosystem": ecosystem,
                    "root": folder,
                    "command": command,
                    "test_files": None,
                    "evidence": [manifest["path"]],
                }
            )
    if "Gemfile" in names and any(path.startswith("spec/") for path in files):
        suites.append(
            {
                "framework": "rspec",
                "ecosystem": "ruby",
                "root": "spec",
                "command": "bundle exec rspec",
                "test_files": sum(1 for p in files if p.startswith("spec/") and p.endswith("_spec.rb")),
                "evidence": ["Gemfile"],
            }
        )
    if "phpunit.xml" in names or "phpunit.xml.dist" in names:
        suites.append(
            {
                "framework": "phpunit",
                "ecosystem": "php",
                "root": ".",
                "command": "vendor/bin/phpunit",
                "test_files": None,
                "evidence": [p for p in ("phpunit.xml", "phpunit.xml.dist") if p in names],
            }
        )
    return suites


def _module_roots(files: list[str]) -> list[str]:
    """Top-level folders, with container folders (`packages/`, `services/`, …) opened one level."""
    top: dict[str, set[str]] = {}
    for path in files:
        parts = path.split("/")
        if len(parts) < 2:
            continue
        top.setdefault(parts[0], set())
        if len(parts) > 2:
            top[parts[0]].add(parts[1])
    roots = []
    for folder, children in sorted(top.items()):
        if folder in CONTAINERS and len(children) >= 2:
            roots.extend(f"{folder}/{child}" for child in sorted(children))
        else:
            roots.append(folder)
    return roots[:MAX_MODULES]


def _kind(path: str, lifecycle: set[str]) -> str:
    name = path.split("/")[-1].lower()
    if path in lifecycle:
        return "lifecycle"  # this framework's own records, not the product
    if path.startswith(".") and path != ".github":
        return "tooling"
    for kind, names in KINDS.items():
        if name in names or path.split("/")[0].lower() in names:
            return kind
    return "code"


def module_of(path: str, modules: list[dict[str, Any]]) -> str | None:
    """The module a path belongs to: the longest module path that contains it."""
    path = path.replace("\\", "/").strip("/")
    best = None
    for module in modules:
        root = module["path"]
        if (path == root or path.startswith(root + "/")) and (best is None or len(root) > len(best)):
            best = root
    return best


def scan(repository_root: Path) -> dict[str, Any]:
    """Inventory the tracked files at HEAD. Reads, never runs and never writes."""
    repository_root = repository_root.resolve()
    files, commit = tracked_files(repository_root)
    names = set(files)
    languages = Counter(language for path in files if (language := _language(path)))

    manifests = [
        {"path": path, "ecosystem": ecosystem}
        for path in files
        for pattern, ecosystem in MANIFESTS
        if _matches(path, pattern) and "node_modules/" not in path
    ]
    manifests = list({item["path"]: item for item in manifests}.values())
    ci = [{"system": system, "path": path} for path in files for pattern, system in CI if _matches(path, pattern)]
    owners_file, rules = _owner_rules(repository_root, names)
    suites = _test_suites(repository_root, files, manifests)
    lifecycle = {".ai-sdlc"}
    try:
        from .config import delivery_root

        lifecycle.add(delivery_root(repository_root).resolve().relative_to(repository_root).parts[0])
    except Exception:  # noqa: BLE001 - an unmanaged repository has no delivery folder to set aside
        pass

    modules = []
    for root in _module_roots(files):
        inside = [path for path in files if path.startswith(root + "/")]
        code = Counter(language for path in inside if (language := _language(path)) and language not in NOT_CODE)
        modules.append(
            {
                "path": root,
                "kind": _kind(root, lifecycle),
                "files": len(inside),
                "code_files": sum(code.values()),
                "languages": [language for language, _ in code.most_common(3)],
                "test_files": sum(1 for path in inside if PYTHON_TEST.search(path) or JS_TEST.search(path)),
                "owners": owners_of(root + "/", rules),
                "manifests": [item["path"] for item in manifests if item["path"].startswith(root + "/")],
            }
        )
    root_files = [path for path in files if "/" not in path]

    entry_points = []
    for path in files:
        name = PurePosixPath(path).name
        depth = path.count("/")
        if name.startswith("Dockerfile") or name.endswith(".Dockerfile"):
            entry_points.append({"kind": "container image", "path": path})
        elif re.fullmatch(r"(docker-)?compose[\w.-]*\.ya?ml", name):
            entry_points.append({"kind": "compose file", "path": path})
        elif name in ENTRY_NAMES and depth <= 2:
            entry_points.append({"kind": "program entry", "path": path})
    if "pyproject.toml" in names:
        block = re.search(
            r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)", _read(repository_root, "pyproject.toml"), re.M | re.S
        )
        for match in re.finditer(r'^\s*([\w.-]+)\s*=\s*"([^"]+)"', block.group(1) if block else "", re.M):
            entry_points.append(
                {"kind": "console script", "path": "pyproject.toml", "detail": f"{match[1]} → {match[2]}"}
            )

    notes = []
    if not owners_file:
        notes.append("No CODEOWNERS file, so the scan cannot say who owns which module.")
    if not suites:
        notes.append("No test suite was recognised. A baseline can still pin build or lint commands.")
    if not ci:
        notes.append("No CI configuration was found in tracked files.")
    total = sum(languages.values()) or 1
    return {
        "commit": commit,
        "tracked_files": len(files),
        "languages": [
            {"language": language, "files": count, "share": round(count / total, 3), "code": language not in NOT_CODE}
            for language, count in languages.most_common()
        ],
        "manifests": manifests,
        "test_suites": suites,
        "ci": ci,
        "owners": {"file": owners_file, "rules": len(rules)},
        "modules": modules,
        "root_files": len(root_files),
        "entry_points": entry_points[:60],
        "proposed_probes": [
            {
                "name": f"{suite['framework']}-{suite['root']}".replace("/", "-").replace(".", "root").strip("-"),
                "kind": "characterization",
                "command": suite["command"],
                "timeout_seconds": 1800,
            }
            for suite in suites
        ],
        "notes": notes,
    }
