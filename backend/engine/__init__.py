"""Eva conversation engine — the read-loop state machine (R6).

The chat turn is modelled as a small, ordered pipeline over a :class:`TurnState`
(EVA_SYSTEM_DESIGN §5.11 / §7.1):

    classify → assemble_context → check_in → reason → check_out → persist

Each step is a plain function that reads/writes the shared ``TurnState``; the
``/chat`` websocket owns socket I/O (frame parsing, capture, conversation
bookkeeping, session history, the ``emit`` send-lock, voice) and drives the steps
in order. Splitting it this way makes the discipline testable in isolation and
gives the later L3 engine (R7) and full guardrails (Phase 20) a clean seam:

  * ``classify`` decides intent — and therefore whether retrieval is even allowed.
  * ``assemble_context`` gathers the context window (recent L1 episodes, relevance
    recall, profile slices) and, only for a retrieving intent, corpus passages.
  * ``check_in`` is the input guardrail seam (crisis-care today) and finalizes the
    guarded model input.
  * ``reason`` streams the reply.
  * ``check_out`` is the output guardrail seam (no-invented-citations today).
  * ``persist`` hands the finished reply back to the caller to record.
"""

from __future__ import annotations

from .turn import (
    TurnState,
    assemble_context,
    check_in,
    check_out,
    classify,
    persist,
    reason,
)

__all__ = [
    "TurnState",
    "classify",
    "assemble_context",
    "check_in",
    "reason",
    "check_out",
    "persist",
]
