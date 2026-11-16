---
description: Assemble a two-minute decision packet for one human gate
argument-hint: <intent-id> <G1|G2|G3|G4|G5>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob, Grep
---

Assemble the Gate **$2** packet for Intent **$1**.

Packet: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" gate-packet $1 $2`

Present this to the human approver. Your job is preparation, never the decision.

Gate *count* is not what makes this framework expensive — gate *preparation* is. A reviewer
who has to reconstruct what changed, which controls apply, and what is still outstanding
will either rubber-stamp the gate or avoid it. Both failures end the same way.

So when you present it:

1. **Lead with the delta.** If this gate was presented before, the reviewer has already read
   the artifacts once. What changed since then is the only new information.
2. **State the outstanding checks plainly.** Everything the runner already knows is
   incomplete is in the packet. The reviewer should never be the one to discover a missing
   artifact or an unverified Task.
3. **Surface conformance gaps first.** A Task whose visible suite passes while its held-out
   suite fails is not a flaky test — it is an implementation that satisfies its own tests
   rather than the requirement. Say so in those words.
4. **Report the cost.** The packet carries the verification tax so far. A gate that is
   expensive and never catches anything is a candidate for decay review, and that
   conversation only happens if somebody can see the number.

Do not approve, imply approval, or infer approval from the absence of objections. Record the
decision only after a person makes it, with `/aisdlc:gate`.
