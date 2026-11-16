"""Lifecycle metrics export (W4.4).

The framework measures better internals than DORA's four keys — it knows when a defect was
introduced versus detected, whether a promoted lesson later fired, and what verification
actually cost. But a platform team comparing this framework to the rest of its portfolio
needs the four keys, because that is the vocabulary the rest of the portfolio reports in.

So this exports both: the loop metrics as they are, and a DORA projection derived from
lifecycle events. The projection is explicitly labelled as a proxy. Release and deployment
are outside this lifecycle by design, so "deployment frequency" here means Intents
reaching close, not artifacts reaching production — reporting it as the real thing would
be a lie that a dashboard would then propagate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .costs import CostLedger, gate_latencies
from .lessons import LoopMetrics
from .state import StateStore


class MetricsExporter:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def collect(self) -> dict[str, Any]:
        loop = LoopMetrics.from_repository(self.repository_root)
        cost = CostLedger(self.repository_root).summary()
        return {
            "schema": "aisdlc.metrics/v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "loop_metrics": loop,
            "cost": cost,
            "gate_latency_seconds": gate_latencies(self.repository_root),
            "dora_proxy": self.dora_proxy(),
        }

    def dora_proxy(self) -> dict[str, Any]:
        """Project the four keys from lifecycle events, labelled as proxies.

        - *Delivery frequency*: Intents closed per week. The lifecycle's unit of delivered
          value, which is the closest honest analogue to deployment frequency.
        - *Lead time*: Intent registration to close.
        - *Change-fail rate*: the share of closed Intents that produced a corrective Task
          at the verification stage — the framework's own record of "we got it wrong".
        - *Recovery time*: how long a blocked or reverted Task took to reach a terminal
          resolution.
        """
        store = StateStore(self.repository_root)
        if not store.audit_path.exists():
            return {"available": False, "reason": "no audit history yet"}
        events: list[dict[str, Any]] = []
        for line in store.audit_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        created: dict[str, datetime] = {}
        closed: dict[str, datetime] = {}
        recoveries: list[float] = []
        blocked_at: dict[str, datetime] = {}
        reverted = 0
        for event in events:
            try:
                at = datetime.fromisoformat(str(event.get("at")))
            except ValueError:
                continue
            entity, target = str(event.get("entity_id")), str(event.get("to"))
            if event.get("entity_type") == "intent":
                if target == "initialized":
                    created[entity] = at
                elif target == "completed":
                    closed[entity] = at
            elif event.get("entity_type") == "task":
                if target == "blocked":
                    blocked_at[entity] = at
                elif target in {"verified", "merged", "failed"} and entity in blocked_at:
                    recoveries.append((at - blocked_at.pop(entity)).total_seconds())
                elif target == "reverted":
                    reverted += 1
        lead_times = [
            (closed[intent_id] - created[intent_id]).total_seconds() for intent_id in closed if intent_id in created
        ]
        if closed:
            span = (max(closed.values()) - min(created.values(), default=max(closed.values()))).total_seconds()
            weeks = max(span / (7 * 86400), 1e-9)
            frequency = len(closed) / weeks
        else:
            frequency = 0.0
        return {
            "available": True,
            "note": (
                "Proxies derived from lifecycle events. Release and deployment are outside this lifecycle, "
                "so delivery frequency counts Intents closed, not artifacts deployed."
            ),
            "delivery_frequency_per_week": round(frequency, 4),
            "median_lead_time_seconds": round(sorted(lead_times)[len(lead_times) // 2], 3) if lead_times else 0.0,
            "change_fail_rate": round(reverted / len(closed), 4) if closed else 0.0,
            "median_recovery_seconds": round(sorted(recoveries)[len(recoveries) // 2], 3) if recoveries else 0.0,
            "intents_closed": len(closed),
        }

    def to_openmetrics(self, payload: dict[str, Any]) -> str:
        """Render the numeric metrics in OpenMetrics text format.

        Flat gauges only. A dashboard should be able to scrape this without knowing
        anything about the lifecycle's structure, and anything that is not a number
        belongs in the JSON export rather than smuggled into a label.
        """
        lines: list[str] = []

        def gauge(name: str, value: Any, help_text: str, labels: str = "") -> None:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{labels} {float(value)}")

        for key, value in sorted(payload.get("loop_metrics", {}).items()):
            gauge(f"aisdlc_loop_{key}", value, f"Loop metric: {key}")
        cost = payload.get("cost", {})
        gauge("aisdlc_verification_tax", cost.get("verification_tax"), "Verification cost over implementation cost")
        gauge("aisdlc_verification_seconds", cost.get("verification_seconds"), "Total verification wall clock")
        gauge("aisdlc_implementation_seconds", cost.get("implementation_seconds"), "Total implementation wall clock")
        for gate, seconds in sorted(payload.get("gate_latency_seconds", {}).items()):
            intent_id, _, gate_name = str(gate).partition(":")
            gauge(
                "aisdlc_gate_latency_seconds",
                seconds,
                "Seconds from a gate becoming presentable to it being decided",
                labels=f'{{intent="{intent_id}",gate="{gate_name}"}}',
            )
        dora = payload.get("dora_proxy", {})
        if dora.get("available"):
            for name, key, help_text in (
                ("delivery_frequency_per_week", "delivery_frequency_per_week", "Intents closed per week (proxy)"),
                ("median_lead_time_seconds", "median_lead_time_seconds", "Intent registration to close (proxy)"),
                ("change_fail_rate", "change_fail_rate", "Reverted Tasks over closed Intents (proxy)"),
                ("median_recovery_seconds", "median_recovery_seconds", "Blocked Task to resolution (proxy)"),
            ):
                gauge(f"aisdlc_dora_{name}", dora.get(key), help_text)
        lines.append("# EOF")
        return "\n".join(lines) + "\n"
