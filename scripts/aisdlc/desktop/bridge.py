"""The desktop bridge: framed, bounded stdio JSON, protocol 1.0.

Wire-compatible with the AI Governance desktop bridge: one JSON object per line in each
direction, UTF-8, at most ``MAX_FRAME`` bytes; requests are ``{"id", "op", "params"}``;
every reply is an envelope; subscribed projects also produce unsolicited
``{"kind": "notification", ...}`` lines when their generation moves, driven by a bounded
poll thread and by ``project.reconcile`` hints from the host's file watcher.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from aisdlc.desktop import PROTOCOL_VERSION, codebase, operations, pipeline_view, portfolio, projections
from aisdlc.desktop.protocol import SCHEMA_VERSIONS, BridgeError, Envelope, as_dict, now_iso, sanitize_text
from aisdlc.desktop.store import ProjectStore

MAX_FRAME = 4 * 1024 * 1024
ACTIVE_POLL_SECONDS = 5.0
INACTIVE_POLL_SECONDS = 30.0

Handler = Callable[["Bridge", dict[str, Any], Envelope], dict[str, Any]]


class Bridge:
    def __init__(self) -> None:
        self.projects: dict[str, ProjectStore] = {}
        self.subscribed: set[str] = set()
        self.active: str | None = None
        self._lock = threading.RLock()
        self._notify: Callable[[dict[str, Any]], None] | None = None
        self._stop = threading.Event()
        self._last_poll: dict[str, float] = {}

    def store_for(self, params: dict[str, Any]) -> ProjectStore:
        project_id = str(params.get("project_id") or "")
        with self._lock:
            store = self.projects.get(project_id)
        if store is None:
            raise BridgeError(
                "unknown_project", "project is not open in this bridge", detail={"project_id": project_id}
            )
        return store

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = sanitize_text(request.get("id"), limit=80) or "anonymous"
        op = str(request.get("op") or "")
        params = as_dict(request.get("params"))
        envelope = Envelope(request_id=request_id, kind=op.split(".")[0])
        handler = HANDLERS.get(op)
        if handler is None:
            envelope.kind = "error"
            return envelope.fail(BridgeError("unknown_operation", f"unsupported operation {op!r}"))
        try:
            if op not in {"hello", "project.open"}:
                store = self.store_for(params)
                envelope.project_id = store.context.project_id
                envelope.worktree_id = store.context.worktree_id
                envelope.generation = store.generation
                envelope.source_fingerprint = store.source_fingerprint
                if store.last_error:
                    envelope.freshness = "error"
                    envelope.warnings.append(store.last_error)
            payload = handler(self, params, envelope)
        except BridgeError as exc:
            return envelope.fail(exc)
        except Exception as exc:  # noqa: BLE001 - never let one projection kill the bridge
            return envelope.fail(BridgeError("internal", f"{type(exc).__name__}: {exc}"))
        return envelope.wrap(payload)

    # -- background reconciliation ------------------------------------------ #
    def poll_once(self) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        now = time.monotonic()
        with self._lock:
            targets = [(pid, self.projects[pid]) for pid in self.subscribed if pid in self.projects]
        for project_id, store in targets:
            cadence = ACTIVE_POLL_SECONDS if project_id == self.active else INACTIVE_POLL_SECONDS
            if now - self._last_poll.get(project_id, 0.0) < cadence:
                continue
            self._last_poll[project_id] = now
            if store.reconcile():
                notes.append(self.notification(store, "poll"))
        return notes

    def notification(self, store: ProjectStore, source: str) -> dict[str, Any]:
        return {
            "kind": "notification",
            "protocol_version": PROTOCOL_VERSION,
            "event": "generation",
            "project_id": store.context.project_id,
            "generation": store.generation,
            "source": source,
            "at": now_iso(),
            "freshness": "error" if store.last_error else "current",
        }

    def _poll_loop(self) -> None:
        while not self._stop.wait(1.0):
            for note in self.poll_once():
                if self._notify is not None:
                    self._notify(note)

    def serve(self, stdin: IO[bytes], stdout: IO[bytes]) -> int:
        write_lock = threading.Lock()

        def emit(message: dict[str, Any]) -> None:
            data = (json.dumps(message, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            with write_lock:
                stdout.write(data)
                stdout.flush()

        self._notify = emit
        threading.Thread(target=self._poll_loop, name="bridge-poll", daemon=True).start()
        try:
            while True:
                line = stdin.readline(MAX_FRAME + 1)
                if not line:
                    return 0
                if len(line) > MAX_FRAME:
                    emit(
                        Envelope(request_id="oversized", kind="error").fail(
                            BridgeError("frame_too_large", "request exceeds the frame limit")
                        )
                    )
                    continue
                if not line.strip():
                    continue
                try:
                    request = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    emit(
                        Envelope(request_id="malformed", kind="error").fail(
                            BridgeError("malformed_request", "request line is not a JSON object")
                        )
                    )
                    continue
                if not isinstance(request, dict):
                    emit(
                        Envelope(request_id="malformed", kind="error").fail(
                            BridgeError("malformed_request", "request must be a JSON object")
                        )
                    )
                    continue
                emit(self.handle(request))
        finally:
            self._stop.set()


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #
def op_hello(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    from aisdlc.versioning import SCHEMA_VERSION, runner_version

    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_versions": dict(SCHEMA_VERSIONS),
        "engine": {
            "name": "aisdlc",
            "version": runner_version(),
            "record_generation": SCHEMA_VERSION,
            "python": sys.version.split()[0],
        },
        "operations": sorted(HANDLERS),
        "runner_operations": operations.catalogue(),
        "limits": {"max_frame_bytes": MAX_FRAME},
        "poll_seconds": {"active": ACTIVE_POLL_SECONDS, "inactive": INACTIVE_POLL_SECONDS},
    }


def op_project_open(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    root = params.get("root")
    if not isinstance(root, str) or not root:
        raise BridgeError("invalid_params", "root is required")
    store = ProjectStore.open(Path(root), cursors=as_dict(params.get("cursors")) or None)
    with bridge._lock:
        existing = bridge.projects.get(store.context.project_id)
        if existing is not None:
            store = existing  # one canonical root, one store, one generation
        else:
            bridge.projects[store.context.project_id] = store
        if bridge.active is None or params.get("activate"):
            bridge.active = store.context.project_id
    envelope.project_id = store.context.project_id
    envelope.worktree_id = store.context.worktree_id
    envelope.generation = store.generation
    envelope.source_fingerprint = store.source_fingerprint
    envelope.kind = "context"
    return {"context": store.context.to_dict(), "generation": store.generation, "catch_up": store.catch_up()}


def op_project_close(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    store = bridge.store_for(params)
    with bridge._lock:
        bridge.projects.pop(store.context.project_id, None)
        bridge.subscribed.discard(store.context.project_id)
        if bridge.active == store.context.project_id:
            bridge.active = next(iter(bridge.projects), None)
    return {"closed": store.context.project_id, "cursors": store.cursors()}


def op_project_snapshot(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    store = bridge.store_for(params)
    if params.get("reconcile", True):
        store.reconcile()
        envelope.generation = store.generation
        envelope.source_fingerprint = store.source_fingerprint
    envelope.kind = "portfolio"
    payload, omissions = projections.portfolio(store)
    envelope.omissions.extend(omissions)
    return payload


def op_project_reconcile(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    store = bridge.store_for(params)
    changed = store.reconcile()
    envelope.generation = store.generation
    envelope.source_fingerprint = store.source_fingerprint
    if changed and bridge._notify is not None and store.context.project_id in bridge.subscribed:
        bridge._notify(bridge.notification(store, sanitize_text(params.get("hint"), limit=40) or "hint"))
    return {"changed": changed, "generation": store.generation, "last_reconciled": store.last_reconciled}


def op_project_subscribe(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    store = bridge.store_for(params)
    with bridge._lock:
        bridge.subscribed.add(store.context.project_id)
        if params.get("activate"):
            bridge.active = store.context.project_id
    return {"subscribed": True, "active": bridge.active == store.context.project_id}


def op_project_unsubscribe(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    store = bridge.store_for(params)
    with bridge._lock:
        bridge.subscribed.discard(store.context.project_id)
    return {"subscribed": False}


def op_project_cursors(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
    return {"cursors": bridge.store_for(params).cursors()}


def _delegate(function: Callable[..., Any], kind: str, *, needs_intent: bool = True) -> Handler:
    def handler(bridge: Bridge, params: dict[str, Any], envelope: Envelope) -> dict[str, Any]:
        store = bridge.store_for(params)
        envelope.kind = kind
        if needs_intent:
            intent_id = str(params.get("intent_id") or "")
            if not intent_id:
                raise BridgeError("invalid_params", "intent_id is required")
            payload, omissions = function(store, intent_id, params)
        else:
            payload, omissions = function(store, params)
        envelope.omissions.extend(omissions)
        return payload

    return handler


HANDLERS: dict[str, Handler] = {
    "hello": op_hello,
    "project.open": op_project_open,
    "project.close": op_project_close,
    "project.snapshot": op_project_snapshot,
    "project.reconcile": op_project_reconcile,
    "project.subscribe": op_project_subscribe,
    "project.unsubscribe": op_project_unsubscribe,
    "project.cursors": op_project_cursors,
    "intent.inspect": _delegate(projections.intent_inspect, "intent"),
    "gate.packet": _delegate(projections.gate_packet, "gate"),
    "spec.inspect": _delegate(projections.spec_inspect, "spec"),
    "trace.inspect": _delegate(projections.trace_inspect, "trace"),
    "story.inspect": _delegate(projections.story, "story"),
    "events.page": _delegate(projections.events_page, "events", needs_intent=False),
    "diagnostics.inspect": _delegate(projections.diagnostics, "diagnostics", needs_intent=False),
    "learn.inspect": _delegate(projections.learn_inspect, "learn", needs_intent=False),
    "artifact.read": _delegate(projections.artifact_read, "artifact", needs_intent=False),
    "artifact.check": _delegate(projections.artifact_check, "artifact"),
    "authoring.compose": _delegate(projections.compose_requirement, "authoring"),
    "overview.inspect": _delegate(projections.overview_inspect, "overview"),
    "inbox.inspect": _delegate(projections.inbox_inspect, "inbox", needs_intent=False),
    "codebase.inspect": _delegate(codebase.codebase_inspect, "codebase", needs_intent=False),
    "portfolio.inspect": _delegate(portfolio.portfolio_inspect, "portfolio", needs_intent=False),
    "pipeline.inspect": _delegate(pipeline_view.pipeline_inspect, "pipeline", needs_intent=False),
    "operation.preview": _delegate(operations.preview, "operation", needs_intent=False),
    "operation.execute": _delegate(operations.execute, "operation", needs_intent=False),
    "operation.cancel": _delegate(operations.cancel, "operation", needs_intent=False),
}


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="aisdlc-desktop-bridge").parse_args(argv)
    return Bridge().serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
