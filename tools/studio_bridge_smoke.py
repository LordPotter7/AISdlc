"""Smoke-test the AISDLC Studio bridge against a real repository, over the real stdio wire.

    python tools/studio_bridge_smoke.py [repo] [--writes]

Spawns `python -X utf8 -m aisdlc.desktop` exactly as the Tauri shell does, opens the
repository, and requests every projection a Studio view renders (Cockpit, Intent tabs,
gate packets, Story Mode, Doctor). It then runs the read-only runner operations through
preview → execute and proves the repository is byte-identical afterwards, and checks the
bridge's refusals. `--writes` also approves the Intent waiting on Gate 1 and proves the
confirmation token is single-use — leave it off if you want to approve that gate yourself
in the Studio UI.

Default repo: the copy produced by `tools/samplerepo_scenario.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]


class Wire:
    def __init__(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(REPOSITORY / "scripts")}
        self.process = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "aisdlc.desktop"],
            cwd=REPOSITORY / "scripts",
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.counter = 0
        self.project_id = ""

    def call(self, op: str, **params: Any) -> dict[str, Any]:
        self.counter += 1
        request_id = f"r{self.counter}"
        if self.project_id and op not in {"hello", "project.open"}:
            params.setdefault("project_id", self.project_id)
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write((json.dumps({"id": request_id, "op": op, "params": params}) + "\n").encode())
        self.process.stdin.flush()
        while True:  # subscribed projects may interleave unsolicited notifications
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError(f"bridge exited: {self.process.stderr.read().decode(errors='replace')[-2000:]}")
            message = json.loads(line)
            if message.get("kind") == "notification" and message.get("id") != request_id:
                continue
            return message

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=10)


def digest(root: Path) -> str:
    hasher = hashlib.sha256()
    for base in (root / ".ai-sdlc", root / "delivery"):
        for path in sorted(base.rglob("*")):
            if path.is_file() and "context-packs" not in path.parts:
                hasher.update(str(path.relative_to(root)).encode())
                hasher.update(path.read_bytes())
    return hasher.hexdigest()


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        self.rows.append((ok, label, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def run(root: Path, writes: bool) -> int:
    wire, report = Wire(), Report()
    try:
        hello = wire.call("hello")["payload"]
        report.check(
            "operation.execute" in hello["operations"],
            "hello",
            f"protocol {hello['protocol_version']}, engine {hello['engine']['version']}, "
            f"{len(hello['operations'])} operations",
        )
        opened = wire.call("project.open", root=str(root), activate=True)
        report.check(opened["error"] is None, "project.open", str(root))
        wire.project_id = opened["project_id"]
        wire.call("project.subscribe", activate=True)

        print("Cockpit")
        snapshot = wire.call("project.snapshot")["payload"]
        waiting = [f"{item.get('intent')} {item.get('gate')}" for item in snapshot.get("waiting", [])]
        report.check(bool(snapshot), "project.snapshot", f"waiting on a person: {waiting or 'none'}")
        intents = sorted(path.name.split("-")[0] for path in (root / "delivery/intents").glob("I*-*"))

        print("Intent views")
        for intent_id in intents:
            for op in ("intent.inspect", "spec.inspect", "trace.inspect", "story.inspect"):
                reply = wire.call(op, intent_id=intent_id)
                report.check(
                    reply["error"] is None,
                    f"{op} {intent_id}",
                    "" if reply["error"] is None else reply["error"]["code"],
                )
        for intent_id, gate in (("I001", "G5"), ("I003", "G2"), ("I004", "G1")):
            reply = wire.call("gate.packet", intent_id=intent_id, gate=gate)
            report.check(reply["error"] is None, f"gate.packet {intent_id} {gate}")
        spec = wire.call("spec.inspect", intent_id="I005")["payload"]
        report.check("breaking" in json.dumps(spec).lower(), "spec.inspect I005 shows the accepted breaking change")
        report.check(wire.call("events.page")["error"] is None, "events.page (activity)")
        report.check(wire.call("diagnostics.inspect")["error"] is None, "diagnostics.inspect (Doctor view)")

        print("Read-only runner operations: preview → execute, repository unchanged")
        before = digest(root)
        for name, args in (
            ("validate_intent", {"intent_id": "I001"}),
            ("spec_lint", {"intent_id": "I003"}),
            ("contract_registry", {"intent_id": "I005"}),
            ("doctor", {}),
            ("loop_metrics", {}),
        ):
            preview = wire.call("operation.preview", name=name, args=args)["payload"]
            result = wire.call(
                "operation.execute", name=name, args=args, token=preview["token"], base_hashes=preview["base_hashes"]
            )
            payload = result.get("payload") or {}
            report.check(result["error"] is None, f"{name}", f"`{preview['command']}` exit {payload.get('exit_code')}")
        report.check(digest(root) == before, "read operations left .ai-sdlc/ and delivery/ byte-identical")

        print("Refusals")
        report.check(
            wire.call("intent.inspect", intent_id="I999")["error"]["code"] == "unknown_intent", "unknown Intent refused"
        )
        report.check(
            wire.call("operation.preview", name="repair_state", args={})["error"]["code"] == "unknown_operation",
            "operation outside the allowlist refused",
        )
        injected = wire.call(
            "operation.preview",
            name="gate_approve",
            args={"intent_id": "I004; rm -rf /", "gate": "G1", "approver": "x"},
        )
        report.check(injected["error"]["code"] == "invalid_params", "shell injection in arguments refused")

        if writes:
            print("Writes: approve I004 G1 through preview → execute")
            args = {"intent_id": "I004", "gate": "G1", "approver": "nadia.product"}
            preview = wire.call("operation.preview", name="gate_approve", args=args)["payload"]
            result = wire.call(
                "operation.execute",
                name="gate_approve",
                args=args,
                token=preview["token"],
                base_hashes=preview["base_hashes"],
            )["payload"]
            report.check(result["succeeded"], "gate_approve I004 G1 executed", preview["command"])
            replay = wire.call(
                "operation.execute",
                name="gate_approve",
                args=args,
                token=preview["token"],
                base_hashes=preview["base_hashes"],
            )
            report.check(replay["error"]["code"] == "token_invalid", "confirmation token is single-use")
            args = {
                "resource": "contract:deeptutor-api#listPersonas",
                "intents": ["I003", "I005"],
                "approver": "omar.techlead",
                "reason": "Localized names and scoping touch disjoint fields",
            }
            preview = wire.call("operation.preview", name="coapprove_resource", args=args)["payload"]
            result = wire.call(
                "operation.execute",
                name="coapprove_resource",
                args=args,
                token=preview["token"],
                base_hashes=preview["base_hashes"],
            )["payload"]
            report.check(
                result["succeeded"] and result["output"]["intents"] == ["I003", "I005"],
                "coapprove_resource reaches the runner with both Intents",
                preview["command"],
            )
            contention = wire.call("project.snapshot")["payload"]["kpis"].get("contention")
            report.check(not contention, "Cockpit contention cleared after co-approval", f"contention={contention}")
    finally:
        wire.close()
    failed = [row for row in report.rows if not row[0]]
    print(f"\n{len(report.rows) - len(failed)}/{len(report.rows)} checks passed")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", nargs="?", default=str(REPOSITORY.parent / "SampleRepo-aisdlc"))
    parser.add_argument(
        "--writes",
        action="store_true",
        help="also approve I004 G1 and co-approve the contention (consumes the UI demo steps)",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    return run(Path(args.repo).resolve(), args.writes)


if __name__ == "__main__":
    raise SystemExit(main())
