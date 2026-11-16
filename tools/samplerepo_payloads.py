"""Source files the SampleRepo scenario "implements" inside the DeepTutor copy.

Each constant is the content of one file written into the target repository by
`tools/samplerepo_scenario.py`. The code is real and its tests really run: DeepTutor's own
dependencies (fastapi, loguru, ...) are not installed, so every new module is dependency-free
and every new test loads its module by file path and runs with ``--noconftest`` (DeepTutor's
root conftest imports the uninstalled package).
"""

from __future__ import annotations

# Every scenario test runs through this command shape: no conftest, no cache written to the
# tree (a dirty tree after evidence capture would block convergence and close).
# --import-mode=importlib: the DeepTutor root has an __init__.py, so the default mode would try to
# import the checkout itself as a package. `-P`: DeepTutor has a top-level `logging/` package that
# would shadow the standard library if the working directory were put on sys.path.
PYTEST = "python -P -m pytest -q --noconftest -p no:cacheprovider --import-mode=importlib"

LOAD_HELPER = '''"""Load a DeepTutor module by file path, without importing the uninstalled package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None, relative
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
'''

# --------------------------------------------------------------------------- #
# I001 — login brute-force throttling (full lifecycle, completed)
# --------------------------------------------------------------------------- #
LOGIN_THROTTLE = '''"""Sliding-window login throttle.

``POST /api/v1/auth/login`` had no limit on failed attempts (api/routers/auth.py). This
module decides whether a login attempt may proceed; the router records outcomes and maps a
refusal to HTTP 429 with ``Retry-After``. State is in-process (decision D-001): one API
worker per deployment today, and a restart clearing lockouts is acceptable.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    retry_after: int = 0


class LoginThrottle:
    def __init__(self, max_failures: int = 5, window_seconds: int = 300, lockout_seconds: int = 900,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}

    def check(self, key: str) -> ThrottleDecision:
        now = self._clock()
        until = self._locked_until.get(key, 0.0)
        if until > now:
            return ThrottleDecision(False, int(until - now) + 1)
        return ThrottleDecision(True)

    def record_failure(self, key: str) -> ThrottleDecision:
        now = self._clock()
        window = self._failures[key]
        window.append(now)
        while window and window[0] <= now - self.window_seconds:
            window.popleft()
        if len(window) >= self.max_failures:
            self._locked_until[key] = now + self.lockout_seconds
            window.clear()
        return self.check(key)

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
'''

TEST_LOGIN_THROTTLE = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _load import load

throttle = load("services/security/login_throttle.py", "login_throttle")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_locks_after_max_failures_and_reports_retry_after() -> None:
    clock = Clock()
    guard = throttle.LoginThrottle(max_failures=3, window_seconds=60, lockout_seconds=120, clock=clock)
    for _ in range(2):
        assert guard.record_failure("alice").allowed
    decision = guard.record_failure("alice")
    assert not decision.allowed
    assert 0 < decision.retry_after <= 121


def test_lockout_expires() -> None:
    clock = Clock()
    guard = throttle.LoginThrottle(max_failures=1, lockout_seconds=10, clock=clock)
    guard.record_failure("bob")
    clock.now += 11
    assert guard.check("bob").allowed


def test_failures_outside_the_window_are_forgotten() -> None:
    clock = Clock()
    guard = throttle.LoginThrottle(max_failures=2, window_seconds=10, clock=clock)
    guard.record_failure("carol")
    clock.now += 11
    assert guard.record_failure("carol").allowed


def test_success_resets_the_count() -> None:
    guard = throttle.LoginThrottle(max_failures=2, clock=Clock())
    guard.record_failure("dave")
    guard.record_success("dave")
    assert guard.record_failure("dave").allowed


def test_check_cost_is_bounded_with_ten_thousand_accounts() -> None:
    import time as real_time

    guard = throttle.LoginThrottle(clock=Clock())
    for n in range(10_000):
        guard.record_failure(f"user{n}")
    started = real_time.perf_counter()
    for n in range(10_000):
        guard.check(f"user{n}")
    per_check_ms = (real_time.perf_counter() - started) * 1000 / 10_000
    assert per_check_ms < 2
"""

THROTTLE_KEYS = '''"""Throttle key derivation.

The key must not be steerable by the caller (finding F-001): a key built from
``X-Forwarded-For`` or from the username exactly as typed lets an attacker rotate the header
or vary letter case and whitespace to get a fresh budget on every attempt. The key uses the
normalised account name only, so one account has one budget whatever the source address.
"""

from __future__ import annotations

import unicodedata


def normalise_username(username: str) -> str:
    return unicodedata.normalize("NFKC", username).strip().casefold()


def throttle_key(username: str, headers: dict[str, str] | None = None) -> str:
    # Headers are accepted and deliberately ignored: every one of them is caller-controlled.
    del headers
    return "account:" + normalise_username(username)
'''

TEST_THROTTLE_KEYS = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _load import load

keys = load("services/security/throttle_keys.py", "throttle_keys")


def test_case_and_whitespace_variants_share_one_key() -> None:
    assert keys.throttle_key("Alice") == keys.throttle_key("  alice ")


def test_forwarded_for_header_does_not_change_the_key() -> None:
    assert keys.throttle_key("alice", {"X-Forwarded-For": "10.0.0.1"}) == keys.throttle_key(
        "alice", {"X-Forwarded-For": "10.0.0.2"})


def test_compatibility_forms_are_normalised() -> None:
    assert keys.throttle_key("\\uff41lice") == keys.throttle_key("alice")  # fullwidth a
"""

# The Class N harness: replays each attack in the dataset against the real throttle and
# writes the result file `eval run` scores. An attack "succeeds" when it gets more than
# max_failures password guesses at one account before being locked out.
THROTTLE_ATTACK_HARNESS = '''"""Adversarial harness for the login throttle (Class N, EVAL-001).

    python evals/throttle_attacks.py <dataset.jsonl> <results.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from _load import load  # noqa: E402

throttle_mod = load("services/security/login_throttle.py", "login_throttle")
keys_mod = load("services/security/throttle_keys.py", "throttle_keys")
MAX_FAILURES = 5


def run_attack(case: dict) -> bool:
    guard = throttle_mod.LoginThrottle(max_failures=MAX_FAILURES, clock=lambda: 1000.0)
    guesses = 0
    for attempt in case["attempts"]:
        key = keys_mod.throttle_key(attempt["username"], attempt.get("headers"))
        if not guard.check(key).allowed:
            continue
        guesses += 1
        guard.record_failure(key)
    return guesses > MAX_FAILURES


def main(dataset: str, output: str) -> int:
    cases = [json.loads(line) for line in Path(dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    # One run; each case is one attack technique. `eval run` counts `success: true` cases.
    runs = [{"cases": [{"id": case["id"], "technique": case["technique"], "success": run_attack(case)}
                       for case in cases]}]
    Path(output).write_text(json.dumps(runs, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
'''


def _attempts(usernames: list[str], forwarded: list[str | None]) -> list[dict]:
    return [
        {"username": name, "headers": ({"X-Forwarded-For": ip} if ip else {})}
        for name, ip in zip(usernames, forwarded, strict=True)
    ]


THROTTLE_ATTACKS = [
    {"id": "ATK-001", "technique": "plain brute force", "attempts": _attempts(["alice"] * 12, [None] * 12)},
    {
        "id": "ATK-002",
        "technique": "rotate X-Forwarded-For",
        "attempts": _attempts(["alice"] * 12, [f"203.0.113.{n}" for n in range(12)]),
    },
    {
        "id": "ATK-003",
        "technique": "vary letter case",
        "attempts": _attempts(["alice", "Alice", "ALICE", "aLiCe", "alicE", "ALice"] * 2, [None] * 12),
    },
    {
        "id": "ATK-004",
        "technique": "pad with whitespace",
        "attempts": _attempts([" alice", "alice ", "  alice", "alice  ", "\talice", "alice\t"] * 2, [None] * 12),
    },
    {
        "id": "ATK-005",
        "technique": "fullwidth homoglyph",
        "attempts": _attempts(["ａlice", "aｌice", "alｉce", "aliｃe", "alicｅ", "alice"] * 2, [None] * 12),
    },
]

# --------------------------------------------------------------------------- #
# I002 — dashboard /recent bounded limit and correct type filter (compact, completed)
# --------------------------------------------------------------------------- #
DASHBOARD_QUERY = '''"""Pure query rules for ``GET /api/v1/dashboard/recent``.

Two defects in the original handler (api/routers/dashboard.py):
* ``limit`` was unbounded: ``limit=-1`` or ``limit=10**9`` reached the session store.
* the ``type`` filter ran after fetching ``limit`` sessions, so a filtered request
  returned fewer than ``limit`` rows even when more matching sessions existed.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
PAGE_SIZE = 100


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def activity_type(session: dict[str, Any]) -> str:
    return str(session.get("capability") or "chat").replace("deep_", "")


def select(sessions: list[dict[str, Any]], type_filter: str | None, limit: int) -> list[dict[str, Any]]:
    matching = [s for s in sessions if type_filter is None or activity_type(s) == type_filter]
    return matching[:limit]
'''

TEST_DASHBOARD_QUERY = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from _load import load

query = load("api/routers/dashboard_query.py", "dashboard_query")


@pytest.mark.parametrize("raw, expected", [(None, 50), (-1, 1), (0, 1), (10, 10), (10**9, 200)])
def test_limit_is_clamped(raw, expected) -> None:
    assert query.clamp_limit(raw) == expected


def test_type_filter_fills_the_limit_from_the_whole_page() -> None:
    sessions = [{"capability": "chat"}] * 5 + [{"capability": "deep_research"}] * 5
    picked = query.select(sessions, "research", 3)
    assert len(picked) == 3
    assert {query.activity_type(s) for s in picked} == {"research"}


def test_no_filter_keeps_order() -> None:
    sessions = [{"capability": "chat", "n": n} for n in range(4)]
    assert [s["n"] for s in query.select(sessions, None, 2)] == [0, 1]
"""

DASHBOARD_ROUTER_OLD = """@router.get("/recent")
async def get_recent_activities(limit: int = 50, type: str | None = None):
    store = get_session_store()
    sessions = await store.list_sessions(limit=limit, offset=0)
    activities: list[dict[str, Any]] = []

    for session in sessions:
        capability = str(session.get("capability") or "chat")
        activity_type = capability.replace("deep_", "")
        if type is not None and activity_type != type:
            continue
"""

DASHBOARD_ROUTER_NEW = """@router.get("/recent")
async def get_recent_activities(limit: int = 50, type: str | None = None):
    store = get_session_store()
    limit = dashboard_query.clamp_limit(limit)
    sessions: list[dict[str, Any]] = []
    offset = 0
    # Page until `limit` matching sessions are found, so a type filter no longer
    # returns a short list (I002 REQ-F-002).
    while len(dashboard_query.select(sessions, type, limit)) < limit:
        page = await store.list_sessions(limit=dashboard_query.PAGE_SIZE, offset=offset)
        if not page:
            break
        sessions.extend(page)
        offset += len(page)
    activities: list[dict[str, Any]] = []

    for session in dashboard_query.select(sessions, type, limit):
        capability = str(session.get("capability") or "chat")
        activity_type = dashboard_query.activity_type(session)
"""

# --------------------------------------------------------------------------- #
# I003 — per-learner persona scoping (full, multi-tenant, left in building)
# --------------------------------------------------------------------------- #
PERSONA_SCOPE = '''"""Which persona workspace a request may read or write.

api/routers/personas.py resolved every request to the admin workspace
(``_admin_persona_service``). Scope now comes from the authenticated user only
(standing threat from LESSON-001): a workspace id in the path, query, or body is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str


ADMIN_WORKSPACE = "admin"


def persona_workspace(principal: Principal, requested: str | None = None) -> str:
    del requested  # caller-controlled; never used to choose scope
    if principal.role == "admin":
        return ADMIN_WORKSPACE
    return f"user:{principal.user_id}"
'''

TEST_PERSONA_SCOPE = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _load import load

scope = load("services/persona/scope.py", "persona_scope")


def test_learner_gets_their_own_workspace() -> None:
    assert scope.persona_workspace(scope.Principal("u1", "learner")) == "user:u1"


def test_requested_workspace_is_ignored() -> None:
    assert scope.persona_workspace(scope.Principal("u1", "learner"), "user:u2") == "user:u1"


def test_admin_keeps_the_admin_workspace() -> None:
    assert scope.persona_workspace(scope.Principal("a", "admin")) == "admin"
"""

# --------------------------------------------------------------------------- #
# I004 — localized capability status (full, model behaviour; G2 reopened)
# --------------------------------------------------------------------------- #
# Class S scorer driven by `eval execute --adapter command`: the runner pipes each case as
# JSON on stdin; the scorer prints {"score": 0..1}. A translation scores 1 when it keeps
# every {placeholder} of the English source.
PLACEHOLDER_SCORER = r'''"""Score one status-string translation: 1.0 when every {placeholder} survives."""

import json
import re
import sys

case = json.load(sys.stdin)
source = set(re.findall(r"{(\w+)}", case["en"]))
target = set(re.findall(r"{(\w+)}", case["zh"]))
print(json.dumps({"score": 1.0 if source == target else 0.0}))
'''

STATUS_TRANSLATIONS = [
    {"id": "ST-001", "key": "thinking", "en": "Thinking about {topic}...", "zh": "正在思考{topic}……"},
    {"id": "ST-002", "key": "retrieving", "en": "Searching {count} sources", "zh": "正在检索 {count} 个来源"},
    {
        "id": "ST-003",
        "key": "grading",
        "en": "Grading answer {index} of {total}",
        "zh": "正在批改第 {index}/{total} 题",
    },
    {"id": "ST-004", "key": "done", "en": "Done", "zh": "完成"},
]
