"""Evidence verification helpers for high-impact L3/L4 statements.

R10 keeps analytics local and fail-closed: code may compute numbers, but a
high-impact statement only reaches the user when cited evidence exists and an
available local/injected verifier says the statement is supported.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("eva.memory.verification")

ModelCaller = Callable[..., Awaitable[str]]
SyncVerifier = Callable[[str, list[str]], bool | None]

YESNO_MAX_TOKENS = 8


def parse_yes_no(raw: str) -> bool | None:
    """Parse a bounded verifier answer into True, False, or undecided."""
    head = str(raw or "").strip().lower()[:16]
    if "yes" in head:
        return True
    if "no" in head:
        return False
    return None


def verify_claim_with_callable(
    claim: str,
    evidence: list[str],
    verifier: SyncVerifier | None = None,
) -> bool | None:
    """Synchronously verify a claim with an injected verifier, failing closed."""
    clean_evidence = [str(e).strip() for e in evidence if str(e).strip()]
    if not str(claim).strip() or not clean_evidence or verifier is None:
        return None
    try:
        return verifier(str(claim).strip(), clean_evidence)
    except Exception as exc:  # noqa: BLE001 - verification failure drops the claim.
        log.warning("sync verification failed: %s", exc)
        return None


async def verify_claim_supported(
    claim: str,
    evidence: list[str],
    call_model: ModelCaller | None,
) -> bool | None:
    """Ask a local/injected model whether evidence supports a high-impact claim."""
    clean_claim = str(claim or "").strip()
    clean_evidence = [str(e).strip() for e in evidence if str(e).strip()]
    if not clean_claim or not clean_evidence or call_model is None:
        return None
    prompt = render_verification_prompt(clean_claim, clean_evidence)
    try:
        raw = await call_model(prompt, temperature=0.0, max_tokens=YESNO_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 - undecided means "do not surface".
        log.warning("claim verification model call failed: %s", exc)
        return None
    return parse_yes_no(raw)


def render_verification_prompt(claim: str, evidence: list[str]) -> str:
    """Render the bounded yes/no prompt shared by analytics verification gates."""
    bullets = "\n".join(f"- {item}" for item in evidence)
    return (
        f"High-impact claim about the person: {claim}\n\n"
        f"Evidence entries:\n{bullets}\n\n"
        "Is this claim still supported by this evidence? "
        "Answer with only 'yes' or 'no'."
    )
