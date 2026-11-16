"""W6: adopting an existing repository. `understand scan` inventories what git tracks — facts,
never stored, never run — and Studio's codebase map joins it to adoption progress and to
the modules each Intent's Tasks change."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisdlc.cli import app
from aisdlc.inventory import InventoryError, module_of, owners_of, scan
from aisdlc.typerlite import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aisdlc.desktop.bridge import Bridge  # noqa: E402
from tools.make_demo_repo import generate  # noqa: E402

runner = CliRunner()

FILES = {
    "pyproject.toml": "[project]\nname = 'shop'\n[project.scripts]\nshop = \"shop.cli:main\"\n"
    "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
    "shop/__init__.py": "",
    "shop/cart.py": "def total(items):\n    return sum(items)\n",
    "shop/cli.py": "def main():\n    pass\n",
    "tests/test_cart.py": "from shop.cart import total\n\ndef test_total():\n    assert total([1, 2]) == 3\n",
    "tests/test_cli.py": "def test_cli():\n    pass\n",
    "web/package.json": json.dumps(
        {"name": "web", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^3"}}
    ),
    "web/src/app.ts": "export const x = 1;\n",
    "web/src/app.test.ts": "import { x } from './app';\n",
    "services/api/main.py": "print('api')\n",
    "services/worker/run.py": "print('worker')\n",
    "Dockerfile": "FROM python:3.12\n",
    ".github/workflows/ci.yml": "on: push\n",
    ".github/CODEOWNERS": "* @everyone\n/shop/ @team-core\nweb/ @team-web\n",
    "docs/index.md": "# Shop\n",
}


def _repository(root: Path) -> Path:
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "shop"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def test_scan_reads_facts_from_git_and_changes_nothing(tmp_path: Path) -> None:
    root = _repository(tmp_path / "shop")
    before = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"], cwd=root, capture_output=True, text=True
    ).stdout
    first = scan(root)
    assert scan(root) == first, "the same commit always gives the same answer"
    assert (
        subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=root, capture_output=True, text=True).stdout
        == before
    ), "the scan writes nothing, not even a cache"

    assert first["tracked_files"] == len(FILES)
    assert first["languages"][0]["language"] == "Python"
    suites = {(suite["framework"], suite["root"]): suite for suite in first["test_suites"]}
    assert suites[("pytest", "tests")]["command"] == "python -m pytest -q tests"
    assert suites[("pytest", "tests")]["test_files"] == 2
    assert suites[("vitest", "web")]["command"] == "npm --prefix web test"
    assert suites[("vitest", "web")]["test_files"] == 1
    assert [probe["command"] for probe in first["proposed_probes"]] == [s["command"] for s in first["test_suites"]]
    assert first["ci"] == [{"system": "GitHub Actions", "path": ".github/workflows/ci.yml"}]
    assert first["owners"] == {"file": ".github/CODEOWNERS", "rules": 3}

    modules = {module["path"]: module for module in first["modules"]}
    assert {"services/api", "services/worker"} <= set(modules), "container folders open one level"
    assert modules["shop"]["owners"] == ["@team-core"] and modules["web"]["owners"] == ["@team-web"]
    assert modules["docs"]["owners"] == ["@everyone"] and modules["docs"]["kind"] == "docs"
    assert modules["tests"]["kind"] == "tests" and modules[".github"]["kind"] == "infrastructure"
    assert modules["shop"]["languages"] == ["Python"] and modules["web"]["languages"] == ["TypeScript"]
    kinds = {(item["kind"], item["path"]) for item in first["entry_points"]}
    assert {("container image", "Dockerfile"), ("program entry", "services/api/main.py")} <= kinds
    assert any(item.get("detail") == "shop → shop.cli:main" for item in first["entry_points"])
    assert first["notes"] == []


def test_paths_resolve_to_their_module_and_owners() -> None:
    modules = [{"path": "services"}, {"path": "services/api"}, {"path": "web"}]
    assert module_of("services/api/routes/x.py", modules) == "services/api"
    assert module_of("services\\worker\\run.py", modules) == "services"
    assert module_of("README.md", modules) is None
    rules = [("*", ["@all"]), ("/shop/", ["@core"]), ("*.md", ["@docs"])]
    assert owners_of("shop/cart.py", rules) == ["@core"]
    assert owners_of("shop/README.md", rules) == ["@docs"], "the last matching rule wins"
    assert owners_of("lib/shop/x.py", rules) == ["@all"], "an anchored rule matches only from the root"


def test_scan_needs_git_and_the_cli_prints_it(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    with pytest.raises(InventoryError, match="not a git repository"):
        scan(tmp_path / "plain")
    root = _repository(tmp_path / "shop")
    result = runner.invoke(app, ["understand", "scan", "--repo", str(root)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["test_suites"][0]["framework"] == "pytest"


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate(tmp_path_factory.mktemp("codebase") / "orders-service", force=False)


def _payload(root: Path, op: str, **params):  # noqa: ANN202
    bridge = Bridge()
    project = bridge.handle({"id": "o", "op": "project.open", "params": {"root": str(root)}})["project_id"]
    reply = bridge.handle({"id": op, "op": op, "params": {"project_id": project, **params}})
    assert reply["error"] is None, reply["error"]
    return reply["payload"]


def test_codebase_map_joins_modules_to_the_intents_that_change_them(demo: Path) -> None:
    payload = _payload(demo, "codebase.inspect")
    assert payload["managed"] is True
    steps = {step["id"]: step for step in payload["adoption"]["steps"]}
    assert steps["init"]["done"] and steps["intent"]["done"]
    assert steps["baseline"]["handoff"] == "/aisdlc:baseline"
    src = next(module for module in payload["modules"] if module["path"] == "src")
    touching = {item["intent"]: item for item in src["intents"]}
    assert {"I001", "I002", "I003"} <= set(touching)
    assert "src/search.py" in touching["I002"]["paths"] and "I002-P00-T001" in touching["I002"]["tasks"]
    assert src["contended"] is True, "two open Intents change the same module"
    assert "src" in payload["footprint"]["I002"]["modules"]
    assert "I003" in payload["footprint"]["I002"]["shared_with"]
    assert "I001" not in payload["footprint"]["I002"]["shared_with"], "a completed Intent no longer contends"


def test_editing_a_task_suggests_modules_and_test_commands(demo: Path) -> None:
    path = next(iter(sorted((demo / "delivery/intents").glob("I002-*/04-build/*/tasks/*/task.yaml"))))
    relative = path.relative_to(next((demo / "delivery/intents").glob("I002-*"))).as_posix()
    report = _payload(demo, "artifact.check", intent_id="I002", path=relative, content=path.read_text(encoding="utf-8"))
    assert "src/" in report["suggestions"]["affected_paths"]
    assert report["suggestions"]["verification_commands"] == [suite["command"] for suite in scan(demo)["test_suites"]]
    spec = _payload(
        demo,
        "artifact.check",
        intent_id="I002",
        path="02-specification/requirements.yaml",
        content=(next((demo / "delivery/intents").glob("I002-*")) / "02-specification/requirements.yaml").read_text(
            encoding="utf-8"
        ),
    )
    assert spec["suggestions"] == {}
