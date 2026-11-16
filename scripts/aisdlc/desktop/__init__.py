"""Read-only desktop bridge for AISDLC Studio.

Everything here projects state the runner already owns. Projections call the runner's
own readers and services and never re-derive a lifecycle rule. The only writes happen in
:mod:`aisdlc.desktop.operations`, behind an allowlist and a preview/confirm token, by
invoking the runner's CLI exactly as a person would.
"""

from __future__ import annotations

PROTOCOL_VERSION = "1.0"

__all__ = ["PROTOCOL_VERSION"]
