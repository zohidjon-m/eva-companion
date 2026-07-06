"""L3 profile — what Eva understands about *you*.

# R9: evidence-backed L3 read/audit interface
# ─────────────────────────────────────────────────────────────────────────────
# This module is the read/audit contract for Eva's L3 profile. The consolidation
# loop writes ``profile.json`` using the §7.3 operation grammar; chat reads small
# evidence-backed slices from it, and the Profile screen shows the same claims
# with local evidence previews. ``profile.md`` remains the editable human view.
#
# The hard rule that makes that possible: ``profile.json`` conforms EXACTLY to
# EVA_MEMORY_ARCHITECTURE §7.2 — same fields, same types, same structure. The
# demo's hand-authored profile (see ``scripts/seed_profile.py``) is shaped the
# way the engine will eventually shape it on its own, so nothing downstream has
# to learn a new schema when the engine arrives.
#
# Two stores, one source of truth (§7.2):
#   * ``profile.json`` is the structured truth. :func:`get_profile` reads it;
#     :func:`retrieve_slices` selects evidence-backed claims for the chat prompt.
#   * ``profile.md`` is a human-readable *rendering* of the JSON, regenerated
#     after every write. When the user edits and saves the Markdown, the lenient
#     parser turns their edits into ``set_anchor`` corrections applied back to the
#     JSON (the future human-correction anchor). Anything it can't parse is left
#     unchanged and the user is warned — an edit never corrupts the profile.
#
# Graceful degradation is a hard requirement (Phase 13 "Done when"): if
# ``profile.json`` is absent or unreadable, :func:`get_profile` returns ``None``
# and :func:`get_slices` returns ``[]`` — Eva simply has no profile context that
# turn, and nothing crashes.
# ─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import db, vault, vault_dir

log = logging.getLogger("eva.memory.profile")

# The schema version this module reads/writes. Bumping §7.2 means bumping this
# and writing a migration (same discipline as the SQLite schema, §7.1).
#
# v1 → v2 (R7.5): identity and emotional_baseline gain a field-keyed ``provenance``
# dict (per-field ``evidence`` + ``source`` [+ ``last_seen``]), so every inferred
# field carries its own evidence — see EVA_MEMORY_ARCHITECTURE §7.2. The upgrade is
# lenient and happens on read (:meth:`Profile.from_dict`): a v1 profile's flat
# provenance list is dropped to an empty dict (the rebuild re-derives it), and the
# version is stamped forward, so old files never crash and self-heal on next save.
SCHEMA_VERSION = 2

# The identity/baseline fields the model may author (each gets a provenance entry)
# and the synthetic anchor-path sections they live under. ``typical_mood`` is
# code-owned (no verb writes it) but is still anchorable so a user correction to it
# survives a rebuild.
_IDENTITY_FIELDS = ("stated_self", "principles")
_BASELINE_FIELDS = ("typical_mood", "known_triggers", "what_helps")

# The valid range for typical_mood (§7.2 — the −5…+5 scale render_markdown shows).
# A hand-edit outside this is a typo, not a real correction, and is rejected.
MOOD_MIN, MOOD_MAX = -5, 5

# Writes regenerate both files; serialize them so two concurrent PUT /profile
# saves can't interleave a read and a write (FastAPI may use threads).
_write_lock = threading.Lock()

# R9 read-side gates. Prompt slices are intentionally small and evidence-backed:
# the model gets only a few relevant L3 facts, never the whole profile.
MIN_PROMPT_CONFIDENCE = 0.2
MAX_PROMPT_SLICES = 6
EVIDENCE_PREVIEW_CHARS = 180

_SLICE_PRIORITY = {
    "goal": 0,
    "value": 1,
    "identity": 2,
    "pattern": 3,
    "watch": 4,
    "open_loop": 5,
    "relationship": 6,
    "baseline": 7,
}


def _profile_json_path() -> Path:
    """Return the structured profile file (``<vault>/profile.json``)."""
    return vault_dir() / "profile.json"


def _profile_md_path() -> Path:
    """Return the human-readable rendering (``<vault>/profile.md``)."""
    return vault_dir() / "profile.md"


# ─────────────────────────────────────────────────────────────────────────────
# The profile object — a typed view of the §7.2 structure.
#
# Top-level keys are typed; nested claim objects stay as plain dicts so the demo
# stub never has to mirror every leaf field the real engine will eventually fill
# (confidence, decay, provenance). ``from_dict`` is lenient — a missing or
# mistyped section degrades to its empty default rather than raising — because a
# hand-edited or partial ``profile.json`` must never crash Eva's read path.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Profile:
    """A parsed ``profile.json`` (EVA_MEMORY_ARCHITECTURE §7.2).

    Holds the §7.2 top-level sections. The real L3 engine returns this same
    object from :func:`get_profile`, so any consumer written against it today
    keeps working unchanged when the engine replaces the stub.
    """

    schema_version: int = SCHEMA_VERSION
    identity: dict = field(default_factory=dict)
    goals: list[dict] = field(default_factory=list)
    patterns: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    emotional_baseline: dict = field(default_factory=dict)
    open_loops: list[dict] = field(default_factory=list)
    watch_list: list[dict] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        """Build a Profile from raw ``profile.json`` data, leniently.

        Each section falls back to an empty default if missing or of the wrong
        type, so a partial or slightly malformed file still yields a usable
        Profile (the read path must never crash on bad stored data).
        """

        def _list(key: str) -> list[dict]:
            value = data.get(key)
            return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []

        def _dict(key: str) -> dict:
            value = data.get(key)
            return value if isinstance(value, dict) else {}

        anchors_raw = data.get("anchors")
        anchors = [str(a) for a in anchors_raw] if isinstance(anchors_raw, list) else []
        version = data.get("schema_version")
        # v1 → v2 migration (upgrade-on-read): ensure identity/baseline carry a
        # field-keyed provenance dict, and stamp the version forward. A stored
        # version at or above the current one is preserved (never downgraded).
        identity = _dict("identity")
        baseline = _dict("emotional_baseline")
        _ensure_provenance(identity)
        _ensure_provenance(baseline)
        upgraded = version if isinstance(version, int) and version >= SCHEMA_VERSION else SCHEMA_VERSION
        return cls(
            schema_version=upgraded,
            identity=identity,
            goals=_list("goals"),
            patterns=_list("patterns"),
            relationships=_list("relationships"),
            emotional_baseline=baseline,
            open_loops=_list("open_loops"),
            watch_list=_list("watch_list"),
            anchors=anchors,
        )

    def to_dict(self) -> dict:
        """Serialise back to the exact §7.2 ``profile.json`` shape, in order."""
        return {
            "schema_version": self.schema_version,
            "identity": self.identity,
            "goals": self.goals,
            "patterns": self.patterns,
            "relationships": self.relationships,
            "emotional_baseline": self.emotional_baseline,
            "open_loops": self.open_loops,
            "watch_list": self.watch_list,
            "anchors": self.anchors,
        }


@dataclass(frozen=True)
class ProfileEvidenceRef:
    """A preview of one L0 entry that backs an L3 profile claim.

    R9 uses this as the audit payload for the Profile screen. ``available`` is
    false when the evidence pointer no longer resolves to a real, non-seeded
    entry; those pointers stay visible for audit but are never used for prompt
    injection.
    """

    id: str
    date: str | None = None
    type: str | None = None
    created_at: str | None = None
    preview: str = ""
    available: bool = False

    def to_dict(self) -> dict:
        """Return the JSON shape sent by ``GET /profile``."""
        return {
            "id": self.id,
            "date": self.date,
            "type": self.type,
            "created_at": self.created_at,
            "preview": self.preview,
            "available": self.available,
        }


@dataclass(frozen=True)
class ProfileSlice:
    """One evidence-backed L3 fact selected for a chat turn.

    ``text`` is the human claim text; ``prompt_text`` is the short second-person
    line handed to the model. Keeping both lets the UI/debug path show the claim
    plainly while the prompt stays in Eva's established voice.
    """

    id: str
    kind: str
    text: str
    prompt_text: str
    evidence_ids: list[str] = field(default_factory=list)
    source: str = "model"
    confidence: float | None = None
    status: str | None = None

    def to_dict(self) -> dict:
        """Return a compact JSON/debug representation of this prompt slice."""
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "prompt_text": self.prompt_text,
            "evidence_ids": self.evidence_ids,
            "source": self.source,
            "confidence": self.confidence,
            "status": self.status,
        }


def _ensure_provenance(section: dict) -> None:
    """Ensure ``section["provenance"]`` is a field-keyed dict (§7.2 v2 shape).

    The v1 shape stored provenance as a flat list of entry uids (or, on the
    baseline, a top-level ``evidence`` list); v2 keys evidence by field name inside
    ``provenance``. A non-dict (old list) or missing value becomes an empty dict —
    the rebuild re-derives per-field evidence from L1 — and the stale v1
    ``evidence`` key is dropped, so reading a v1 profile never crashes and migrates
    in place on the next save.
    """
    if not isinstance(section.get("provenance"), dict):
        section["provenance"] = {}
        section.pop("evidence", None)  # v1 baseline stored a flat evidence list here


def field_anchor_path(section: str, field: str) -> str:
    """The synthetic anchor path for a singleton field, e.g. ``identity.stated_self``.

    Identity/baseline fields carry no claim ``id``, so anchoring keys off these
    stable path strings in :attr:`Profile.anchors` (R7.5). ``section`` is
    ``"identity"`` or ``"baseline"``.
    """
    return f"{section}.{field}"


def is_field_anchored(profile: Profile, path: str) -> bool:
    """Whether a singleton identity/baseline field is user-anchored (R7.5).

    True when the synthetic ``path`` (``identity.stated_self`` etc.) is in
    :attr:`Profile.anchors`, or the field's provenance entry carries
    ``source == "user"``. The evidence gate in :mod:`memory.operations` and the
    rebuild both call this so the model can never overwrite a user correction to
    identity or the emotional baseline.
    """
    if path in profile.anchors:
        return True
    section, _, field = path.partition(".")
    holder = (
        profile.identity if section == "identity"
        else profile.emotional_baseline if section == "baseline"
        else {}
    )
    prov = holder.get("provenance") if isinstance(holder, dict) else None
    entry = prov.get(field) if isinstance(prov, dict) else None
    return isinstance(entry, dict) and entry.get("source") == "user"


# ─────────────────────────────────────────────────────────────────────────────
# Read interface (the seam): get_profile + get_slices.
# ─────────────────────────────────────────────────────────────────────────────


def get_profile() -> Profile | None:
    """Return the user's profile, or ``None`` if there is none to read.

    Reads ``<vault>/profile.json`` and parses it leniently into a :class:`Profile`.
    Returns ``None`` — never raises — when the file is absent, unreadable, or not
    a JSON object, so a missing/corrupt profile degrades to "Eva has no profile
    context this turn" rather than an error (Phase 13: delete profile.json → app
    degrades gracefully). This is the read half of the L3 seam; the engine fills
    the same file later and this function is unchanged.
    """
    path = _profile_json_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("profile: could not read %s (%s); treating as no profile", path, exc)
        return None
    if not isinstance(data, dict):
        log.warning("profile: %s is not a JSON object; treating as no profile", path)
        return None
    return Profile.from_dict(data)


# Tiny stopword set for the topic-relevance match below. Deliberately small: the
# point is only to drop the handful of words ("the", "a", "should") that would
# otherwise spuriously match every claim. Real semantic retrieval is the engine's
# job, not the stub's.
_STOPWORDS = frozenset(
    """
    a about an and are as at be but by do does for from had has have how i if in is it
    its me my of on or our should so that the their them then they this to was we
    what when which who why will with you your
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9']+")


def _topic_tokens(text: str) -> set[str]:
    """Lowercase content words of ``text`` (stopwords dropped), for matching."""
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _is_relevant(claim_text: str, topic_tokens: set[str]) -> bool:
    """Whether a claim shares a content word with the current message.

    R9 deliberately uses a small lexical overlap for all prompt candidates,
    including identity, goals, and baseline facts. It prevents whole-profile
    injection while keeping the eventual semantic retrieval seam narrow.
    """
    if not topic_tokens:
        return False
    return bool(_topic_tokens(claim_text) & topic_tokens)


def _preview(text: str, limit: int = EVIDENCE_PREVIEW_CHARS) -> str:
    """Return a compact one-line preview for evidence rows."""
    cleaned = " ".join((text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "..."


def _unique_ids(ids: list | tuple | None) -> list[str]:
    """Return stable, unique string ids from an evidence list."""
    out: list[str] = []
    for raw in ids or []:
        value = str(raw).strip()
        if value and value not in out:
            out.append(value)
    return out


def _row_ref(entry_id: str, row) -> ProfileEvidenceRef:
    """Build an evidence ref from an ``entries`` SQLite row."""
    if row is None:
        return ProfileEvidenceRef(id=entry_id, available=False)
    if int(row["is_seeded"] or 0):
        return ProfileEvidenceRef(
            id=entry_id,
            date=row["date"],
            type=row["type"],
            created_at=row["created_at"],
            preview="",
            available=False,
        )
    return ProfileEvidenceRef(
        id=entry_id,
        date=row["date"],
        type=row["type"],
        created_at=row["created_at"],
        preview=_preview(row["text"]),
        available=True,
    )


def _vault_ref(entry_id: str) -> ProfileEvidenceRef:
    """Build an evidence ref by scanning L0 Markdown when SQLite has no row."""
    turn = vault.find_entry(entry_id)
    if turn is None:
        return ProfileEvidenceRef(id=entry_id, available=False)
    return ProfileEvidenceRef(
        id=entry_id,
        date=turn.date,
        type=turn.type,
        created_at=turn.created_at,
        preview=_preview(turn.text),
        available=True,
    )


def _evidence_refs(evidence_ids: list[str], conn=None) -> list[ProfileEvidenceRef]:
    """Resolve evidence ids to preview refs, marking missing/seeded rows unavailable."""
    ids = _unique_ids(evidence_ids)
    if not ids:
        return []

    own_conn = conn is None
    if own_conn:
        conn = db.get_or_create_db()
    refs: list[ProfileEvidenceRef] = []
    try:
        for entry_id in ids:
            row = db.get_entry(conn, entry_id)
            refs.append(_row_ref(entry_id, row) if row is not None else _vault_ref(entry_id))
    finally:
        if own_conn:
            conn.close()
    return refs


def _valid_evidence_ids(evidence_ids: list[str], conn=None) -> list[str]:
    """Return evidence ids that resolve to real, non-seeded L0/L1 entries."""
    return [ref.id for ref in _evidence_refs(evidence_ids, conn) if ref.available]


def _field_provenance(section: dict, field: str) -> dict:
    """Return the provenance entry for a singleton profile field."""
    prov = section.get("provenance") if isinstance(section, dict) else None
    entry = prov.get(field) if isinstance(prov, dict) else None
    return entry if isinstance(entry, dict) else {}


def _field_source(profile: Profile, section: str, field: str) -> str:
    """Return ``user`` for anchored singleton fields, otherwise provenance source."""
    path = field_anchor_path(section, field)
    if is_field_anchored(profile, path):
        return "user"
    holder = profile.identity if section == "identity" else profile.emotional_baseline
    return str(_field_provenance(holder, field).get("source") or "model")


def _field_evidence(section: dict, field: str) -> list[str]:
    """Return the evidence ids attached to a singleton field."""
    return _unique_ids(_field_provenance(section, field).get("evidence"))


def _claim_confidence(claim: dict) -> float | None:
    """Return a claim confidence as float, or ``None`` if absent/unparseable."""
    value = claim.get("confidence")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _source(claim: dict) -> str:
    """Return the claim source, defaulting absent old claims to model-owned."""
    return str(claim.get("source") or "model")


def _claim_is_promptable(
    *,
    source: str,
    evidence_ids: list[str],
    conn,
    confidence: float | None = None,
    stale: bool = False,
    needs_review: bool = False,
) -> tuple[bool, list[str]]:
    """Apply the R9 anti-hallucination gates for prompt slices."""
    if stale or needs_review:
        return False, []
    if source != "user" and confidence is not None and confidence < MIN_PROMPT_CONFIDENCE:
        return False, []
    valid = _valid_evidence_ids(evidence_ids, conn)
    return source == "user" or bool(valid), valid


def _append_slice(
    slices: list[ProfileSlice],
    conn,
    *,
    id: str,
    kind: str,
    text: str,
    prompt_text: str,
    evidence_ids: list[str],
    source: str,
    confidence: float | None = None,
    status: str | None = None,
    stale: bool = False,
    needs_review: bool = False,
) -> None:
    """Append one prompt slice if it passes evidence and quality gates."""
    clean_text = text.strip()
    if not clean_text:
        return
    ok, valid_ids = _claim_is_promptable(
        source=source,
        evidence_ids=evidence_ids,
        conn=conn,
        confidence=confidence,
        stale=stale,
        needs_review=needs_review,
    )
    if not ok:
        return
    slices.append(
        ProfileSlice(
            id=id,
            kind=kind,
            text=clean_text,
            prompt_text=prompt_text.strip(),
            evidence_ids=valid_ids if source != "user" else _unique_ids(evidence_ids),
            source=source,
            confidence=confidence,
            status=status,
        )
    )


def _rank_slices(slices: list[ProfileSlice]) -> list[ProfileSlice]:
    """Rank and cap prompt slices using R9's small-context policy."""
    return sorted(
        slices,
        key=lambda s: (
            _SLICE_PRIORITY.get(s.kind, 99),
            -(s.confidence if s.confidence is not None else 0.0),
            -len(s.evidence_ids),
            s.id,
        ),
    )[:MAX_PROMPT_SLICES]


def retrieve_slices(topic: str, *, advice_mode: bool = False) -> list[ProfileSlice]:
    """Return typed, evidence-backed L3 slices relevant to one chat topic.

    R9 replaces the old whole-profile prompt seam with a small retrieval step:
    only claims sharing content words with ``topic`` are candidates, model-owned
    claims must cite at least one real non-seeded entry, and low-confidence/stale
    review items stay out of the prompt. ``advice_mode`` gives relevant goals and
    values first rank so advice can be tailored without injecting unrelated facts.
    """
    prof = get_profile()
    if prof is None:
        return []

    tokens = _topic_tokens(topic or "")
    if not tokens:
        return []

    conn = db.get_or_create_db()
    try:
        slices: list[ProfileSlice] = []

        stated_self = str(prof.identity.get("stated_self") or "").strip()
        if stated_self and _is_relevant(stated_self, tokens):
            _append_slice(
                slices,
                conn,
                id="identity.stated_self",
                kind="identity",
                text=stated_self,
                prompt_text=f"They describe themselves as: {stated_self}.",
                evidence_ids=_field_evidence(prof.identity, "stated_self"),
                source=_field_source(prof, "identity", "stated_self"),
            )

        principles = [str(p).strip() for p in prof.identity.get("principles", []) if str(p).strip()]
        principles_text = _join(principles) if principles else ""
        if principles_text and _is_relevant(principles_text, tokens):
            _append_slice(
                slices,
                conn,
                id="identity.principles",
                kind="value",
                text=principles_text,
                prompt_text=f"Principles they hold to: {principles_text}.",
                evidence_ids=_field_evidence(prof.identity, "principles"),
                source=_field_source(prof, "identity", "principles"),
            )

        for goal in prof.goals:
            text = str(goal.get("text") or "").strip()
            status = str(goal.get("status") or "active")
            if text and status == "active" and _is_relevant(text, tokens):
                _append_slice(
                    slices,
                    conn,
                    id=str(goal.get("id") or f"goal:{text.lower()}"),
                    kind="goal",
                    text=text,
                    prompt_text=f"A goal of theirs: {text}.",
                    evidence_ids=_unique_ids(goal.get("evidence")),
                    source=_source(goal),
                    confidence=_claim_confidence(goal),
                    status=status,
                    stale=bool(goal.get("stale")),
                    needs_review=bool(goal.get("needs_review")),
                )

        baseline = prof.emotional_baseline
        helps = [str(h).strip() for h in baseline.get("what_helps", []) if str(h).strip()]
        helps_text = _join(helps) if helps else ""
        if helps_text and _is_relevant(helps_text, tokens):
            _append_slice(
                slices,
                conn,
                id="baseline.what_helps",
                kind="baseline",
                text=helps_text,
                prompt_text=f"What tends to help them: {helps_text}.",
                evidence_ids=_field_evidence(baseline, "what_helps"),
                source=_field_source(prof, "baseline", "what_helps"),
            )

        triggers = [str(t).strip() for t in baseline.get("known_triggers", []) if str(t).strip()]
        triggers_text = _join(triggers) if triggers else ""
        if triggers_text and _is_relevant(triggers_text, tokens):
            _append_slice(
                slices,
                conn,
                id="baseline.known_triggers",
                kind="baseline",
                text=triggers_text,
                prompt_text=f"What tends to weigh on them: {triggers_text}.",
                evidence_ids=_field_evidence(baseline, "known_triggers"),
                source=_field_source(prof, "baseline", "known_triggers"),
            )

        for pattern in prof.patterns:
            text = str(pattern.get("text") or "").strip()
            if text and _is_relevant(text, tokens):
                _append_slice(
                    slices,
                    conn,
                    id=str(pattern.get("id") or f"pattern:{text.lower()}"),
                    kind="pattern",
                    text=text,
                    prompt_text=f"Something they've noticed about themselves: {text}.",
                    evidence_ids=_unique_ids(pattern.get("evidence")),
                    source=_source(pattern),
                    confidence=_claim_confidence(pattern),
                    status=str(pattern.get("type") or "") or None,
                    stale=bool(pattern.get("stale")),
                    needs_review=bool(pattern.get("needs_review")),
                )

        for rel in prof.relationships:
            name = str(rel.get("name") or "").strip()
            summary = str(rel.get("summary") or "").strip()
            blob = f"{name} {summary}".strip()
            if name and _is_relevant(blob, tokens):
                text = f"{name} - {summary}" if summary else name
                _append_slice(
                    slices,
                    conn,
                    id=f"relationship:{name.lower()}",
                    kind="relationship",
                    text=text,
                    prompt_text=f"About {name} ({rel.get('type', 'someone')} of theirs): {summary}.",
                    evidence_ids=_unique_ids(rel.get("evidence")),
                    source=_source(rel),
                    status=str(rel.get("type") or "") or None,
                )

        for loop in prof.open_loops:
            desc = str(loop.get("description") or "").strip()
            status = str(loop.get("status") or "open")
            if desc and status != "resolved" and _is_relevant(desc, tokens):
                _append_slice(
                    slices,
                    conn,
                    id=str(loop.get("id") or f"loop:{desc.lower()}"),
                    kind="open_loop",
                    text=desc,
                    prompt_text=f"An open loop on their mind: {desc}.",
                    evidence_ids=_unique_ids(loop.get("evidence")),
                    source=_source(loop),
                    status=status,
                    stale=bool(loop.get("stale")),
                    needs_review=bool(loop.get("needs_review")),
                )

        for item in prof.watch_list:
            desc = str(item.get("description") or "").strip()
            if desc and _is_relevant(desc, tokens):
                wid = f"watch:{item.get('pattern_id', '')}:{item.get('conflicting_goal_id', '')}"
                _append_slice(
                    slices,
                    conn,
                    id=wid,
                    kind="watch",
                    text=desc,
                    prompt_text=f"A tension worth gently holding: {desc}.",
                    evidence_ids=_unique_ids(item.get("evidence")),
                    source=_source(item),
                )

        if advice_mode:
            return sorted(
                slices,
                key=lambda s: (
                    0 if s.kind in {"goal", "value"} else 1,
                    _SLICE_PRIORITY.get(s.kind, 99),
                    -(s.confidence if s.confidence is not None else 0.0),
                    -len(s.evidence_ids),
                    s.id,
                ),
            )[:MAX_PROMPT_SLICES]

        return _rank_slices(slices)
    finally:
        conn.close()


def get_slices(topic: str) -> list[str]:
    """Return prompt-ready profile fragments for legacy callers/tests.

    New R9 code should call :func:`retrieve_slices` for typed slices. This wrapper
    keeps older callers on the same evidence-backed selection rules as chat.
    """
    return [s.prompt_text for s in retrieve_slices(topic)]


def format_slices(fragments: list[ProfileSlice] | list[str]) -> str:
    """Render profile fragments into the ``{profile_slices}`` prompt slot text.

    Each fragment becomes a bullet so the model reads them as discrete facts about
    the person, not prose to quote back. Returns ``""`` for an empty list.
    """
    if not fragments:
        return ""
    lines = [f.prompt_text if isinstance(f, ProfileSlice) else str(f) for f in fragments]
    return "\n".join(f"- {line}" for line in lines if line.strip())


def slices_for_prompt(topic: str, *, advice_mode: bool = False) -> str:
    """Convenience: the formatted ``{profile_slices}`` text for one chat turn.

    Kept for compatibility with older call sites. It now delegates to
    :func:`retrieve_slices`, so prompt text and typed audit metadata share the same
    R9 evidence and relevance gates.
    """
    return format_slices(retrieve_slices(topic, advice_mode=advice_mode))


def _join(items: list[str]) -> str:
    """Join a short list into readable prose: ``a``, ``a and b``, ``a, b and c``."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"


# ─────────────────────────────────────────────────────────────────────────────
# profile.md rendering — the human-readable view of profile.json (§7.2).
#
# This is the canonical rendering: profile.md is regenerated from profile.json on
# every save, so the file the user reads (and edits) is always a faithful view of
# the structured truth. The parser below is the exact inverse for the fields a
# human would reasonably correct.
# ─────────────────────────────────────────────────────────────────────────────

# Section headings. Used both to render and to parse, so the two can never drift.
_H_IDENTITY = "Who you are"
_H_GOALS = "Your goals"
_H_PATTERNS = "Patterns Eva has noticed"
_H_RELATIONSHIPS = "People who matter"
_H_BASELINE = "Your emotional baseline"
_H_LOOPS = "Open loops"
_H_WATCH = "Things to keep an eye on"

_MD_PREAMBLE = (
    "_Eva's private, evolving picture of you. You can edit this directly — your "
    "changes are kept as your own corrections and Eva won't overwrite them._"
)


def render_markdown(profile: Profile) -> str:
    """Render a :class:`Profile` to the human-readable ``profile.md`` text.

    Deterministic and round-trippable: the headings and bullet shapes here are
    exactly what :func:`parse_markdown` reads back, so a render → edit → parse
    cycle preserves everything the user didn't touch. Empty sections are omitted
    so the document only shows what Eva actually knows.
    """
    out: list[str] = ["# Your profile", "", _MD_PREAMBLE, ""]

    # Who you are
    stated = str(profile.identity.get("stated_self") or "").strip()
    principles = [str(p).strip() for p in profile.identity.get("principles", []) if str(p).strip()]
    if stated or principles:
        out += [f"## {_H_IDENTITY}", ""]
        if stated:
            out += [f"You see yourself as **{stated}**.", ""]
        if principles:
            out += [f"Principles you hold to: {', '.join(principles)}.", ""]

    # Your goals
    goals = [str(g.get("text") or "").strip() for g in profile.goals]
    goals = [g for g in goals if g]
    if goals:
        out += [f"## {_H_GOALS}", ""] + [f"- {g}" for g in goals] + [""]

    # Patterns Eva has noticed
    patterns = [str(p.get("text") or "").strip() for p in profile.patterns]
    patterns = [p for p in patterns if p]
    if patterns:
        out += [f"## {_H_PATTERNS}", ""] + [f"- {p}" for p in patterns] + [""]

    # People who matter
    rels = []
    for r in profile.relationships:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        rtype = str(r.get("type") or "").strip()
        summary = str(r.get("summary") or "").strip()
        head = f"**{name}**" + (f" ({rtype})" if rtype else "")
        rels.append(f"- {head} — {summary}" if summary else f"- {head}")
    if rels:
        out += [f"## {_H_RELATIONSHIPS}", ""] + rels + [""]

    # Your emotional baseline
    baseline = profile.emotional_baseline
    base_lines = []
    mood = baseline.get("typical_mood")
    if isinstance(mood, int):
        base_lines.append(f"- Typical mood: {mood:+d} (on a −5…+5 scale)")
    triggers = [str(t).strip() for t in baseline.get("known_triggers", []) if str(t).strip()]
    if triggers:
        base_lines.append(f"- What tends to set you off: {', '.join(triggers)}")
    helps = [str(h).strip() for h in baseline.get("what_helps", []) if str(h).strip()]
    if helps:
        base_lines.append(f"- What helps you: {', '.join(helps)}")
    if base_lines:
        out += [f"## {_H_BASELINE}", ""] + base_lines + [""]

    # Open loops
    loops = [str(l.get("description") or "").strip() for l in profile.open_loops]
    loops = [l for l in loops if l]
    if loops:
        out += [f"## {_H_LOOPS}", ""] + [f"- {l}" for l in loops] + [""]

    # Things to keep an eye on (watch list) — shown for transparency.
    watch = [str(w.get("description") or "").strip() for w in profile.watch_list]
    watch = [w for w in watch if w]
    if watch:
        out += [f"## {_H_WATCH}", ""] + [f"- {w}" for w in watch] + [""]

    return "\n".join(out).rstrip() + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# profile.md → profile.json — the lenient parser (§7.2 human-correction anchor).
#
# The user edits profile.md and saves; this turns their edits into corrections on
# the structured profile. Two §7.2 disciplines hold here:
#   * **Lenient.** A section it can't line up (a heading removed, bullets
#     added/removed so they no longer match the stored claims) is LEFT UNCHANGED
#     and a warning is returned. An edit can correct text; it can't corrupt the
#     profile or silently drop a claim.
#   * **Edits become anchors.** A claim whose text the user changed is marked
#     ``source="user"`` and its id is added to ``anchors`` — the model's update
#     engine may not later weaken/strengthen/overwrite it (§7.2, §7.3 set_anchor).
#     Identity and baseline fields carry no id, so a corrected field registers a
#     synthetic path anchor instead (``identity.stated_self``, ``baseline.known_triggers``,
#     …) and its provenance ``source`` becomes ``user`` (R7.5). Relationships still
#     take the user's edit without an anchor (they carry neither id nor path).
# ─────────────────────────────────────────────────────────────────────────────

# R9 structured profile audit payloads.


def _evidence_status(source: str, refs: list[ProfileEvidenceRef], evidence_ids: list[str]) -> str:
    """Return a compact evidence state for one visible profile claim."""
    if not evidence_ids:
        return "user" if source == "user" else "none"
    available = sum(1 for ref in refs if ref.available)
    if available == len(evidence_ids):
        return "available"
    if available:
        return "partial"
    return "missing"


def _claim_payload(
    *,
    id: str,
    kind: str,
    text: str,
    source: str,
    evidence_ids: list[str],
    anchored: bool,
    conn=None,
    confidence: float | None = None,
    status: str | None = None,
) -> dict | None:
    """Build the structured claim payload used by the Profile screen."""
    clean = text.strip()
    if not clean:
        return None
    refs = _evidence_refs(evidence_ids, conn)
    return {
        "id": id,
        "kind": kind,
        "text": clean,
        "source": source,
        "confidence": confidence,
        "status": status,
        "anchored": anchored,
        "evidence_status": _evidence_status(source, refs, evidence_ids),
        "evidence_ids": evidence_ids,
        "evidence": [ref.to_dict() for ref in refs],
    }


def _claim_is_anchored(profile: Profile, claim: dict) -> bool:
    """Return whether an id-bearing claim is protected by a user correction."""
    return claim.get("source") == "user" or claim.get("id") in profile.anchors


def _section(id: str, title: str, claims: list[dict | None]) -> dict | None:
    """Return a section payload when it contains at least one visible claim."""
    kept = [claim for claim in claims if claim is not None]
    if not kept:
        return None
    return {"id": id, "title": title, "claims": kept}


def profile_sections(profile: Profile | None = None) -> list[dict]:
    """Return structured profile claims grouped for the audit UI.

    This is the visible half of R9: every claim carries the evidence ids it rests
    on, preview rows for the entries that still resolve, and an evidence status so
    missing pointers remain auditable instead of silently disappearing.
    """
    prof = profile or get_profile()
    if prof is None:
        return []

    conn = db.get_or_create_db()
    try:
        return _profile_sections_with_conn(prof, conn)
    finally:
        conn.close()


def _profile_sections_with_conn(prof: Profile, conn) -> list[dict]:
    """Build profile sections while reusing one SQLite connection."""
    sections: list[dict] = []

    def claim(**kwargs) -> dict | None:
        return _claim_payload(conn=conn, **kwargs)

    stated = str(prof.identity.get("stated_self") or "").strip()
    principles = [str(p).strip() for p in prof.identity.get("principles", []) if str(p).strip()]
    maybe = _section(
        "identity",
        _H_IDENTITY,
        [
            claim(
                id="identity.stated_self",
                kind="identity",
                text=stated,
                source=_field_source(prof, "identity", "stated_self"),
                evidence_ids=_field_evidence(prof.identity, "stated_self"),
                anchored=is_field_anchored(prof, "identity.stated_self"),
            ),
            claim(
                id="identity.principles",
                kind="value",
                text=_join(principles) if principles else "",
                source=_field_source(prof, "identity", "principles"),
                evidence_ids=_field_evidence(prof.identity, "principles"),
                anchored=is_field_anchored(prof, "identity.principles"),
            ),
        ],
    )
    if maybe:
        sections.append(maybe)

    maybe = _section(
        "goals",
        _H_GOALS,
        [
            claim(
                id=str(g.get("id") or f"goal:{i}"),
                kind="goal",
                text=str(g.get("text") or ""),
                source=_source(g),
                confidence=_claim_confidence(g),
                status=str(g.get("status") or "active"),
                evidence_ids=_unique_ids(g.get("evidence")),
                anchored=_claim_is_anchored(prof, g),
            )
            for i, g in enumerate(prof.goals)
        ],
    )
    if maybe:
        sections.append(maybe)

    maybe = _section(
        "patterns",
        _H_PATTERNS,
        [
            claim(
                id=str(p.get("id") or f"pattern:{i}"),
                kind="pattern",
                text=str(p.get("text") or ""),
                source=_source(p),
                confidence=_claim_confidence(p),
                status=str(p.get("type") or "") or None,
                evidence_ids=_unique_ids(p.get("evidence")),
                anchored=_claim_is_anchored(prof, p),
            )
            for i, p in enumerate(prof.patterns)
        ],
    )
    if maybe:
        sections.append(maybe)

    rel_claims = []
    for rel in prof.relationships:
        name = str(rel.get("name") or "").strip()
        summary = str(rel.get("summary") or "").strip()
        rel_claims.append(
            claim(
                id=f"relationship:{name.lower()}",
                kind="relationship",
                text=f"{name} - {summary}" if summary else name,
                source=_source(rel),
                status=str(rel.get("type") or "") or None,
                evidence_ids=_unique_ids(rel.get("evidence")),
                anchored=False,
            )
        )
    maybe = _section("relationships", _H_RELATIONSHIPS, rel_claims)
    if maybe:
        sections.append(maybe)

    baseline = prof.emotional_baseline
    mood = baseline.get("typical_mood")
    triggers = [str(t).strip() for t in baseline.get("known_triggers", []) if str(t).strip()]
    helps = [str(h).strip() for h in baseline.get("what_helps", []) if str(h).strip()]
    maybe = _section(
        "baseline",
        _H_BASELINE,
        [
            claim(
                id="baseline.typical_mood",
                kind="baseline",
                text=f"Typical mood: {mood:+d}" if isinstance(mood, int) else "",
                source=_field_source(prof, "baseline", "typical_mood"),
                evidence_ids=_field_evidence(baseline, "typical_mood"),
                anchored=is_field_anchored(prof, "baseline.typical_mood"),
            ),
            claim(
                id="baseline.known_triggers",
                kind="baseline",
                text=_join(triggers) if triggers else "",
                source=_field_source(prof, "baseline", "known_triggers"),
                evidence_ids=_field_evidence(baseline, "known_triggers"),
                anchored=is_field_anchored(prof, "baseline.known_triggers"),
            ),
            claim(
                id="baseline.what_helps",
                kind="baseline",
                text=_join(helps) if helps else "",
                source=_field_source(prof, "baseline", "what_helps"),
                evidence_ids=_field_evidence(baseline, "what_helps"),
                anchored=is_field_anchored(prof, "baseline.what_helps"),
            ),
        ],
    )
    if maybe:
        sections.append(maybe)

    maybe = _section(
        "open_loops",
        _H_LOOPS,
        [
            claim(
                id=str(loop.get("id") or f"loop:{i}"),
                kind="open_loop",
                text=str(loop.get("description") or ""),
                source=_source(loop),
                status=str(loop.get("status") or "open"),
                evidence_ids=_unique_ids(loop.get("evidence")),
                anchored=_claim_is_anchored(prof, loop),
            )
            for i, loop in enumerate(prof.open_loops)
        ],
    )
    if maybe:
        sections.append(maybe)

    maybe = _section(
        "watch",
        _H_WATCH,
        [
            claim(
                id=f"watch:{w.get('pattern_id', '')}:{w.get('conflicting_goal_id', '')}",
                kind="watch",
                text=str(w.get("description") or ""),
                source=_source(w),
                evidence_ids=_unique_ids(w.get("evidence")),
                anchored=False,
            )
            for w in prof.watch_list
        ],
    )
    if maybe:
        sections.append(maybe)

    return sections


def profile_payload(*, markdown: str | None = None, warnings: list[str] | None = None) -> dict:
    """Return the ``GET/PUT /profile`` payload with Markdown plus audit sections."""
    prof = get_profile()
    if prof is None:
        payload = {"present": False, "markdown": None, "sections": []}
    else:
        payload = {
            "present": True,
            "markdown": markdown if markdown is not None else read_markdown(),
            "sections": profile_sections(prof),
        }
    if warnings is not None:
        payload["warnings"] = warnings
    return payload


def evidence_detail(entry_id: str) -> dict | None:
    """Return the full local entry text for a profile evidence pointer.

    Resolves through SQLite first and falls back to scanning Markdown in L0. The
    Profile screen uses this when the user expands an evidence row; no network or
    model call is involved.
    """
    conn = db.get_or_create_db()
    try:
        row = db.get_entry(conn, entry_id)
    finally:
        conn.close()

    original = vault.original_revision(entry_id)
    if row is not None:
        return {
            "id": row["id"],
            "date": row["date"],
            "type": row["type"],
            "created_at": row["created_at"],
            "text": row["text"],
            "has_revisions": original is not None,
            "original_text": original.text if original is not None else None,
        }

    turn = vault.find_entry(entry_id)
    if turn is None:
        return None
    return {
        "id": turn.id,
        "date": turn.date,
        "type": turn.type,
        "created_at": turn.created_at,
        "text": turn.text,
        "has_revisions": original is not None,
        "original_text": original.text if original is not None else None,
    }


# A "## Heading" line.
_HEADING_RE = re.compile(r"^##\s+(.*?)\s*$")
# A "- bullet" line.
_BULLET_RE = re.compile(r"^[-*]\s+(.*?)\s*$")
# The bolded stated-self inside the identity section.
_STATED_RE = re.compile(r"\*\*(.+?)\*\*")
# A relationship bullet: "**Name** (type) — summary"  (type + summary optional).
_REL_RE = re.compile(r"^\*\*(?P<name>.+?)\*\*\s*(?:\((?P<type>.*?)\))?\s*(?:[—–-]\s*(?P<summary>.*))?$")


def _split_sections(md: str) -> dict[str, list[str]]:
    """Split Markdown into ``{heading: [content lines]}`` by ``## `` headings.

    Lines before the first heading (the title + preamble) are ignored. Heading
    text is matched case-insensitively against the known section names by the
    callers, so light cosmetic edits to a heading don't lose its section.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            current = m.group(1).strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _bullets(lines: list[str]) -> list[str]:
    """Return the non-empty bullet texts within a section's lines, in order."""
    out = []
    for line in lines:
        m = _BULLET_RE.match(line)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return out


def parse_markdown(md: str, base: Profile) -> tuple[Profile, list[str]]:
    """Parse edited ``profile.md`` into a corrected :class:`Profile`.

    Applies the user's edits on top of ``base`` (the current structured profile),
    field by field, leniently: a section whose bullets no longer line up 1:1 with
    the stored claims is left unchanged and a warning is appended. Returns the new
    Profile plus the list of human-readable warnings (empty when everything parsed
    cleanly). Pure — it does not touch disk; :func:`save_profile` persists.
    """
    sections = _split_sections(md)
    warnings: list[str] = []

    # Mutable working copies (the dataclass is frozen; we rebuild at the end).
    identity = dict(base.identity)
    goals = [dict(g) for g in base.goals]
    patterns = [dict(p) for p in base.patterns]
    relationships = [dict(r) for r in base.relationships]
    baseline = dict(base.emotional_baseline)
    open_loops = [dict(l) for l in base.open_loops]
    watch_list = [dict(w) for w in base.watch_list]
    anchors = list(base.anchors)

    # Deep-copy the provenance dicts so anchoring a field edit here can't mutate the
    # base profile's provenance in place (§7.2 v2 field-keyed provenance).
    identity["provenance"] = {
        k: dict(v) if isinstance(v, dict) else v
        for k, v in (identity.get("provenance") or {}).items()
    }
    baseline["provenance"] = {
        k: dict(v) if isinstance(v, dict) else v
        for k, v in (baseline.get("provenance") or {}).items()
    }

    def _anchor(claim: dict) -> None:
        """Mark an id-bearing claim as a user correction (§7.2 set_anchor)."""
        claim["source"] = "user"
        cid = claim.get("id")
        if cid and cid not in anchors:
            anchors.append(cid)

    def _anchor_field(section_dict: dict, section: str, field: str) -> None:
        """Mark a singleton identity/baseline field as a user correction (R7.5).

        Registers the synthetic anchor path and stamps the field's provenance
        ``source = "user"`` so the model's update engine leaves it alone thereafter.
        """
        path = field_anchor_path(section, field)
        if path not in anchors:
            anchors.append(path)
        prov = section_dict.setdefault("provenance", {})
        entry = prov.get(field)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["source"] = "user"
        prov[field] = entry

    def _get_section(*names: str) -> list[str] | None:
        for n in names:
            if n.lower() in sections:
                return sections[n.lower()]
        return None

    # ── Identity ──────────────────────────────────────────────────────────────
    id_lines = _get_section(_H_IDENTITY)
    if id_lines is not None:
        text = "\n".join(id_lines)
        m = _STATED_RE.search(text)
        if m:
            new_self = m.group(1).strip()
            old_self = str(identity.get("stated_self") or "").strip()
            identity["stated_self"] = new_self
            if new_self != old_self:
                _anchor_field(identity, "identity", "stated_self")
        for line in id_lines:
            low = line.strip().lower()
            if low.startswith("principles you hold to:"):
                rest = line.split(":", 1)[1]
                new_principles = _csv(rest)
                old_principles = [str(p).strip() for p in identity.get("principles", []) if str(p).strip()]
                identity["principles"] = new_principles
                if new_principles != old_principles:
                    _anchor_field(identity, "identity", "principles")

    # ── List sections that map 1:1 by position ────────────────────────────────
    _apply_bullets(_get_section(_H_GOALS), goals, "text", "goals", _anchor, warnings)
    _apply_bullets(_get_section(_H_PATTERNS), patterns, "text", "patterns", _anchor, warnings)
    _apply_bullets(_get_section(_H_LOOPS), open_loops, "description", "open loops", _anchor, warnings)
    _apply_bullets(_get_section(_H_WATCH), watch_list, "description", "watch list", _anchor, warnings)

    # ── Relationships: match by name, update the summary ──────────────────────
    rel_lines = _get_section(_H_RELATIONSHIPS)
    if rel_lines is not None:
        by_name = {str(r.get("name", "")).strip().lower(): r for r in relationships}
        for bullet in _bullets(rel_lines):
            rm = _REL_RE.match(bullet)
            if not rm:
                warnings.append("A line under “People who matter” wasn't understood and was left as-is.")
                continue
            name = (rm.group("name") or "").strip()
            target = by_name.get(name.lower())
            if target is None:
                warnings.append(f"“{name}” isn't someone on your profile yet, so that line was left as-is.")
                continue
            summary = (rm.group("summary") or "").strip()
            if summary and summary != str(target.get("summary") or "").strip():
                target["summary"] = summary  # relationships carry no claim id → no anchor

    # ── Emotional baseline ─────────────────────────────────────────────────────
    base_lines = _get_section(_H_BASELINE)
    if base_lines is not None:
        for line in base_lines:
            low = line.lower()
            if "typical mood" in low:
                mm = re.search(r"-?[+]?\d+", line)
                if mm:
                    try:
                        new_mood = int(mm.group().replace("+", ""))
                    except ValueError:
                        continue
                    if not (MOOD_MIN <= new_mood <= MOOD_MAX):
                        warnings.append(
                            f"Typical mood must be between {MOOD_MIN} and +{MOOD_MAX}, "
                            "so that change was left as-is."
                        )
                        continue
                    if new_mood != baseline.get("typical_mood"):
                        baseline["typical_mood"] = new_mood
                        _anchor_field(baseline, "baseline", "typical_mood")
            elif "set you off" in low or "trigger" in low:
                new_triggers = _csv(line.split(":", 1)[-1])
                old_triggers = [str(t).strip() for t in baseline.get("known_triggers", []) if str(t).strip()]
                baseline["known_triggers"] = new_triggers
                if new_triggers != old_triggers:
                    _anchor_field(baseline, "baseline", "known_triggers")
            elif "helps you" in low or "what helps" in low:
                new_helps = _csv(line.split(":", 1)[-1])
                old_helps = [str(h).strip() for h in baseline.get("what_helps", []) if str(h).strip()]
                baseline["what_helps"] = new_helps
                if new_helps != old_helps:
                    _anchor_field(baseline, "baseline", "what_helps")

    updated = Profile(
        schema_version=base.schema_version,
        identity=identity,
        goals=goals,
        patterns=patterns,
        relationships=relationships,
        emotional_baseline=baseline,
        open_loops=open_loops,
        watch_list=watch_list,
        anchors=anchors,
    )
    return updated, warnings


def _apply_bullets(lines, claims, text_key, label, anchor, warnings) -> None:
    """Update a list section's claim texts from edited bullets, by position.

    Leniency rule: the edit is applied only when the bullet count matches the
    stored claim count (a clean 1:1 in-place edit). If the user added or removed a
    bullet, the section is left entirely unchanged and a warning is recorded —
    the stub doesn't try to guess which claim a new/removed line maps to (that is
    the real engine's job via the operation grammar).
    """
    if lines is None:
        return
    bullets = _bullets(lines)
    if not claims and not bullets:
        return
    if len(bullets) != len(claims):
        warnings.append(
            f"Your “{label}” list changed length, so it was left as-is — "
            "edit the wording of existing items to have Eva pick up your changes."
        )
        return
    for claim, new_text in zip(claims, bullets):
        if new_text and new_text != str(claim.get(text_key) or "").strip():
            claim[text_key] = new_text
            anchor(claim)


def _csv(text: str) -> list[str]:
    """Split a comma/and-separated phrase into a clean list of items.

    Tolerant of a trailing period and an Oxford-or-not "and", so "prayer,
    exercise and rest." → ``["prayer", "exercise", "rest"]``.
    """
    cleaned = text.strip().rstrip(".")
    parts = re.split(r",|\band\b", cleaned)
    return [p.strip() for p in parts if p.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# Persistence — write both files together (json is truth, md is its rendering).
# ─────────────────────────────────────────────────────────────────────────────


def save_profile(profile: Profile) -> Profile:
    """Persist a profile: write ``profile.json`` and regenerate ``profile.md``.

    The JSON is the source of truth; the Markdown is always rewritten from it so
    the two never drift (§7.2). Writes are serialized and the vault directory is
    created if needed. Returns the saved profile for convenience.
    """
    with _write_lock:
        vdir = vault_dir()
        vdir.mkdir(parents=True, exist_ok=True)
        _profile_json_path().write_text(
            json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _profile_md_path().write_text(render_markdown(profile), encoding="utf-8")
        log.info(
            "profile: saved (%d goal(s), %d pattern(s), %d anchor(s))",
            len(profile.goals), len(profile.patterns), len(profile.anchors),
        )
        return profile


def read_markdown() -> str | None:
    """Return the current ``profile.md`` text, or ``None`` if there is no profile.

    Prefers the on-disk ``profile.md`` (the human-edited rendering). If only
    ``profile.json`` exists (e.g. an engine wrote it but no Markdown was rendered
    yet), it is rendered on the fly so the Profile screen always has something
    faithful to show. Returns ``None`` when there is no profile at all — the
    screen then shows its "no profile yet" state instead of crashing.
    """
    if get_profile() is None:
        return None
    md_path = _profile_md_path()
    if md_path.exists():
        try:
            return md_path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("profile: could not read %s (%s); re-rendering from JSON", md_path, exc)
    profile = get_profile()
    return render_markdown(profile) if profile is not None else None


def save_markdown(md: str) -> tuple[str, list[str]]:
    """Apply edited ``profile.md`` text and persist both files (PUT /profile).

    Parses ``md`` against the current profile (the lenient §7.2 sync), saves the
    result, and returns the freshly re-rendered Markdown plus any warnings about
    sections that couldn't be applied. Raises :class:`NoProfileError` when there
    is no profile to edit — there is nothing to anchor corrections onto.
    """
    base = get_profile()
    if base is None:
        raise NoProfileError("there is no profile to edit")
    updated, warnings = parse_markdown(md, base)
    save_profile(updated)
    # Return the canonical re-rendering (not the user's raw text) so the screen
    # reflects exactly what was stored — including any line it left unchanged.
    return render_markdown(updated), warnings


class NoProfileError(Exception):
    """Raised by :func:`save_markdown` when there is no profile to edit."""
