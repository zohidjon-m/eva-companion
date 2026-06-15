"""L1 extraction — turn one journal entry into a strict, validated record.

One bounded model call per saved entry, using the pre-approved
``backend/prompts/extract_entry.md`` prompt (validated against the real gemma
model in the Phase-2 pre-step). The model is reached over the llama-server
OpenAI-compatible endpoint on :11500 — but that call sits behind the injectable
``ModelCaller`` seam so all the parsing, validation, retry, and fallback logic is
unit-testable without a running server.

Contract (EVA_MEMORY_ARCHITECTURE §7.1):
  * First attempt, then on parse/validation failure ONE retry at temperature 0.3.
  * If the retry also fails, return a ``null_stored`` result (NULL fields) — the
    caller stores it and never blocks the save.
  * Every failure is logged.

The pre-step found temp 0.3 gives clean, consistently parseable JSON (10/10),
while temp 1.0 drifts; so both the first attempt and the retry run at 0.3.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("eva.memory.extract")

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "extract_entry.md"
_PLACEHOLDER = "{{ENTRY_TEXT}}"

# Extraction decode settings. Both attempts run at 0.3 — the pre-step's clean temp.
EXTRACT_TEMPERATURE = 0.3
RETRY_TEMPERATURE = 0.3
MAX_TOKENS = 900

ENTITY_TYPES = {"person", "place", "project"}
LOOP_STATUS = {"open", "updated", "resolved"}

# A ModelCaller takes the rendered prompt + decode params and returns raw model
# text. The default talks to llama-server; tests inject a fake.
ModelCaller = Callable[..., Awaitable[str]]


@dataclass
class ExtractionResult:
    """The outcome of extracting one entry.

    ``status`` is ``done`` (all fields populated) or ``null_stored`` (model failed
    twice; fields are empty/NULL but the entry is still safely saved). ``raw`` and
    ``errors`` retain the model output and failure reasons for logging/debugging.
    """

    status: str  # "done" | "null_stored"
    mood: int | None = None
    emotions: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    themes: list = field(default_factory=list)
    events: list = field(default_factory=list)
    stated_goals: list = field(default_factory=list)
    behaviors: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    open_loops: list = field(default_factory=list)
    self_judgments: list = field(default_factory=list)
    summary: str | None = None
    extracted_at: str | None = None
    raw: list = field(default_factory=list)
    errors: list = field(default_factory=list)


# ── prompt rendering ─────────────────────────────────────────────────────────
def render_prompt(entry_text: str) -> str:
    """Fill the extraction prompt template with one entry's text."""
    template = PROMPT_PATH.read_text()
    return template.replace(_PLACEHOLDER, entry_text.strip())


# ── parsing & validation (pure, model-independent — the bulk of the tests) ────
def extract_json_object(text: str) -> dict:
    """Return the first balanced ``{...}`` object in ``text`` as a dict.

    Tolerant of stray prose or code fences around the JSON (a small model
    sometimes adds them) by scanning for the first brace-balanced span and parsing
    only that. Raises ``ValueError`` if no parseable object is found.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unbalanced JSON braces in model output")


# Tolerate the one drift the real model showed at high temp: hyphenated keys.
_KEY_ALIASES = {
    "self-judgments": "self_judgments",
    "stated-goals": "stated_goals",
    "open-loops": "open_loops",
}


def _normalize_keys(obj: dict) -> dict:
    return {_KEY_ALIASES.get(k, k): v for k, v in obj.items()}


def _as_str_list(value) -> list:
    """Coerce a value into a clean list of non-empty strings."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        s = item if isinstance(item, str) else (str(item) if item is not None else "")
        if s.strip():
            out.append(s.strip())
    return out


def coerce_and_validate(obj: dict) -> dict:
    """Coerce a parsed object into the canonical L1 record, or raise ValueError.

    Drops malformed sub-items rather than failing the whole extraction, but a
    missing/empty ``summary`` is fatal (the summary is what gets embedded for
    recall, so an extraction without one is not ``done``). Returns a dict with all
    eleven L1 keys present and well-typed.
    """
    obj = _normalize_keys(obj)

    summary = obj.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 10:
        raise ValueError("missing or too-short summary")

    mood = obj.get("mood")
    if isinstance(mood, bool) or not isinstance(mood, int):
        mood = None
    elif not -5 <= mood <= 5:
        mood = max(-5, min(5, mood))

    emotions = []
    for em in obj.get("emotions") or []:
        if isinstance(em, dict) and isinstance(em.get("name"), str):
            try:
                inten = float(em.get("intensity", 0.0))
            except (TypeError, ValueError):
                inten = 0.0
            emotions.append({"name": em["name"].strip().lower(),
                             "intensity": max(0.0, min(1.0, inten))})

    entities = []
    for ent in obj.get("entities") or []:
        if isinstance(ent, dict) and isinstance(ent.get("name"), str) and ent.get("type") in ENTITY_TYPES:
            entities.append({
                "name": ent["name"].strip(),
                "type": ent["type"],
                "normalized": str(ent.get("normalized", ent["name"])).strip().lower(),
            })

    stated_goals = []
    for g in obj.get("stated_goals") or []:
        if isinstance(g, dict) and isinstance(g.get("text"), str) and g["text"].strip():
            stated_goals.append({"text": g["text"].strip(), "is_new": bool(g.get("is_new", False))})

    open_loops = []
    for lp in obj.get("open_loops") or []:
        if isinstance(lp, dict) and isinstance(lp.get("description"), str) and lp["description"].strip():
            status = lp.get("status")
            open_loops.append({
                "description": lp["description"].strip(),
                "status": status if status in LOOP_STATUS else "open",
            })

    return {
        "mood": mood,
        "emotions": emotions,
        "entities": entities,
        "themes": _as_str_list(obj.get("themes")),
        "events": _as_str_list(obj.get("events")),
        "stated_goals": stated_goals,
        "behaviors": _as_str_list(obj.get("behaviors")),
        "decisions": _as_str_list(obj.get("decisions")),
        "open_loops": open_loops,
        "self_judgments": _as_str_list(obj.get("self_judgments")),
        "summary": summary.strip(),
    }


def parse_extraction(raw: str) -> dict:
    """Parse + validate one raw model output into a canonical L1 record.

    Raises ``ValueError`` on any unrecoverable problem (no JSON, bad JSON, no
    summary). This is the single function the bad-output unit tests exercise.
    """
    return coerce_and_validate(extract_json_object(raw))


# ── the model call (the only part that needs a server) ───────────────────────
async def _llama_server_call(prompt: str, *, temperature: float, max_tokens: int) -> str:
    """Default ModelCaller: reach the model through the shared :mod:`llm.client`.

    Extraction is a *background* job, so it goes through ``complete_chat`` with
    ``priority=False`` — a real-time chat turn always takes the model lock ahead of
    it (EVA_SYSTEM_DESIGN §8). Routing through ``llm.client`` keeps a single
    model-access path for the whole backend. Gemma has no system role, so the
    instruction+entry prompt is sent as one user message; ``top_p``/``top_k`` are
    left at the server defaults (``None``) to preserve the parseable output the
    Phase-2 pre-step validated at temperature 0.3.
    """
    from llm import client

    return await client.complete_chat(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temp=temperature,
        top_p=None,
        top_k=None,
        priority=False,
        stop=["<end_of_turn>", "<eos>"],
    )


# ── orchestration: attempt → retry@0.3 → null_stored ─────────────────────────
async def extract_entry(text: str, *, call_model: ModelCaller | None = None) -> ExtractionResult:
    """Extract one entry into a validated :class:`ExtractionResult`.

    Runs the model once, then retries once at temperature 0.3 if the output does
    not parse-and-validate. After a second failure, returns a ``null_stored``
    result — the entry is still saved by the caller; only its structure is absent.
    Never raises on model/parse failure; only the caller's storage layer can.
    """
    call = call_model or _llama_server_call
    raws: list = []
    errors: list = []

    for attempt, temp in enumerate((EXTRACT_TEMPERATURE, RETRY_TEMPERATURE), start=1):
        try:
            raw = await call(render_prompt(text), temperature=temp, max_tokens=MAX_TOKENS)
        except Exception as e:  # network/server error — counts as a failed attempt
            errors.append(f"attempt {attempt}: model call failed: {e}")
            log.warning("extraction attempt %d model call failed: %s", attempt, e)
            continue
        raws.append(raw)
        try:
            fields = parse_extraction(raw)
        except ValueError as e:
            errors.append(f"attempt {attempt}: parse failed: {e}")
            log.warning("extraction attempt %d parse failed: %s", attempt, e)
            continue
        return ExtractionResult(
            status="done",
            extracted_at=datetime.now().isoformat(timespec="seconds"),
            raw=raws,
            errors=errors,
            **fields,
        )

    log.error("extraction failed twice; storing null_stored. errors=%s", errors)
    return ExtractionResult(status="null_stored", raw=raws, errors=errors)
