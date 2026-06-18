#!/usr/bin/env python3
"""Reset the vault to the "John" demo: a fresh person, ~a month of real journals.

This replaces the earlier demo persona entirely. Unlike ``seed_demo.py`` (which
wrote DB-only, ``is_seeded=1`` rows for the mood chart), every journal here is a
**real entry** (``is_seeded=0``): it lands in the L0 Markdown vault as the source
of truth AND is indexed into SQLite + embedded into ChromaDB, exactly the way a
genuinely-written entry flows through ``memory.capture``. So the entries are
browsable, recall-able, and editable — and the Insights screens derive from them
as real data, with no demo toggle needed.

What it does, in order:

  1. **Backs up** the current vault's data (profile + journal Markdown + eva.db)
     into ``local_vault.bak-<timestamp>/`` so the reset is reversible.
  2. **Wipes** the old data: every journal day-file, ``profile.{json,md}``, all
     index/extraction/mood/graph/chat rows, and the journal vector collection.
     (Both ``is_seeded=0`` and the old ``is_seeded=1`` demo rows go — this is a
     clean slate, not a merge.)
  3. Writes **John's profile** (``profile.json`` + ``profile.md``) via the L3 seam.
  4. Writes **~30 backdated journal entries** spread across the past month, each
     at its own time of day, with hand-authored extraction data (mood, emotions,
     themes, summary) so the entries look like they came through the real
     pipeline — no model call required.

John's arc: a man buried in his phone (9h/day, doomscrolling, takeout, isolation,
bad sleep) who, over a month, deletes the apps, starts walking then running,
cooks real food, reconnects with friends and his sister, and reads again. The
mood line trends up with believable dips and one blank day.

Usage (from anywhere):
    backend/.venv/bin/python scripts/seed_john.py
    backend/.venv/bin/python scripts/seed_john.py --no-embed   # skip ChromaDB
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Run from anywhere: make the backend package importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import db, graph, profile, vault, vault_dir  # noqa: E402

log = logging.getLogger("eva.seed_john")


def _emo(*pairs: tuple[str, float]) -> list[dict]:
    """Build the emotions list shape ([{name, intensity}, …]) tersely."""
    return [{"name": name, "intensity": intensity} for name, intensity in pairs]


# ─────────────────────────────────────────────────────────────────────────────
# John's month. Each tuple is:
#   (days_ago, (hour, minute), mood, themes, emotions, summary, body)
# `mood` is -5..5 (None for the one blank, un-scored day). `body` is the journal
# entry itself (first person, what John wrote); `summary` is the short line the
# extraction would have distilled (used by the mood tooltip + recall + graph).
# Times are spread across the day to fit each entry (1am doomscroll, 7am walk …).
# ─────────────────────────────────────────────────────────────────────────────
JOHN_DAYS: list[tuple[int, tuple[int, int], int | None, list[str], list[dict], str, str]] = [
    (33, (1, 14), -3, ["screen time", "sleep"], _emo(("anxiety", 0.6), ("fatigue", 0.7)),
     "Another night lost to the phone — nine hours of screen time and nothing to show for it.",
     "It's 1am again and I'm still scrolling. Screen Time says nine hours today. Nine. I can't remember a single thing I looked at, but here I am, eyes burning, telling myself just one more video. I feel like I'm watching my own life through a window."),
    (31, (23, 40), -2, ["food", "screen time", "exercise"], _emo(("guilt", 0.5), ("numbness", 0.5)),
     "Skipped the gym, ordered takeout again, watched YouTube until 2am.",
     "Third takeout this week. I had every intention of going to the gym and instead I lay on the couch and let autoplay decide my evening. The food was gone in ten minutes and I didn't even taste it. Something feels off and I keep numbing it instead of looking at it."),
    (30, (7, 5), -2, ["screen time", "habits"], _emo(("frustration", 0.6), ("numbness", 0.4)),
     "Checked my phone before my feet even hit the floor — two hours gone before breakfast.",
     "I reached for the phone before I was even properly awake. Next thing I knew it was two hours later and I hadn't eaten, hadn't moved, just lay there thumbing through other people's mornings. What a way to start a day."),
    (29, (22, 30), -3, ["friends", "screen time", "mood"], _emo(("loneliness", 0.7), ("envy", 0.5)),
     "Felt invisible — no real conversation in days, just watching everyone else's lives.",
     "I realised tonight I haven't had a real conversation with anyone in maybe a week. Just texts and feeds. Everyone online looks like they're out living and I'm in here watching them do it. It's a strange kind of lonely, surrounded by a thousand people I'll never speak to."),
    (28, (20, 50), -3, ["friends", "screen time"], _emo(("loneliness", 0.6), ("guilt", 0.5)),
     "Cancelled on the coworkers' drinks to stay in and watch nothing in particular.",
     "They asked me to come for drinks and I said I was tired. I wasn't tired. I just couldn't face people, so I stayed home and stared at a screen for four hours. I think I'm starting to disappear and no one's noticed, least of all me."),
    (27, (0, 40), -1, ["screen time", "habits"], _emo(("frustration", 0.6), ("resolve", 0.4)),
     "142 phone unlocks in one day. Something has to change.",
     "I finally looked at the numbers properly. 142 unlocks. Nine hours. I did the maths and that's more than half my waking life spent looking at a slab of glass. I'm not okay with this. I don't want to look back on this year and find it was mostly spent here. Something has to give."),
    (26, (21, 15), 0, ["screen time", "habits"], _emo(("restlessness", 0.6), ("hope", 0.4)),
     "Deleted Instagram and TikTok off my phone. My hands keep reaching for nothing.",
     "I did it — deleted the worst offenders. Instagram, TikTok, gone. The strangest part is my hand keeps reaching for the phone and opening to the empty space where they were, like a tongue going to a missing tooth. It's uncomfortable. I think that discomfort is the point."),
    (24, (18, 20), 1, ["walking", "exercise", "nature"], _emo(("calm", 0.6), ("relief", 0.4)),
     "First walk in months — just 20 minutes around the block, and the air felt good.",
     "Went for a walk after work, no podcast, no phone, just me and the street. Only twenty minutes but I noticed things — a garden I'd never seen, the light going gold on the rooftops. My head was quieter when I got back than it's been in weeks."),
    (23, (0, 55), -1, ["screen time", "habits"], _emo(("shame", 0.6), ("frustration", 0.4)),
     "Caved and reinstalled one of the apps at midnight. The old groove is strong.",
     "I reinstalled it. Told myself I'd just check one thing and lost another hour. I feel stupid and a bit ashamed. But I'm trying to see it as data, not failure — the pull is real and strong, and pretending it isn't won't help. Deleted it again before bed."),
    (22, (19, 30), 0, ["sleep", "screen time", "habits"], _emo(("hope", 0.5), ("determination", 0.4)),
     "Bought a cheap alarm clock so the phone can charge outside the bedroom.",
     "Small experiment: an actual alarm clock, three pounds, so the phone charges in the hallway tonight instead of next to my head. If it's not within arm's reach maybe I won't start and end the day with it. We'll see if I actually leave it out there."),
    (21, (20, 10), 1, ["cooking", "food"], _emo(("pride", 0.5), ("calm", 0.5)),
     "Cooked an actual dinner — pasta and a salad — instead of ordering in.",
     "Made dinner from scratch for the first time in I don't know how long. Just pasta and a salad, nothing clever, but I chopped things and stood at the stove and it smelled like a home. Ate it at the table instead of the couch. Felt almost like taking care of myself."),
    (20, (21, 40), 2, ["friends", "connection"], _emo(("warmth", 0.6), ("relief", 0.5)),
     "Texted Marcus after about a year. He replied right away and we made plans.",
     "I finally messaged Marcus. It's been close to a year, which is embarrassing for a friendship that used to be daily. He wrote back within a minute — 'mate, where have you BEEN' — and we're getting dinner next week. Funny how the thing I dreaded most took ten seconds and felt like setting something down."),
    (19, (22, 25), 1, ["reading", "habits"], _emo(("focus", 0.5), ("calm", 0.5)),
     "Picked up the book that's been on my nightstand for a year. Read 30 pages.",
     "Read tonight. Actually read, thirty pages, the book that's been gathering dust by the bed since last summer. My attention kept skittering at first, reaching for a phone that wasn't there, but it settled. By the end I'd forgotten the time. I'd missed this and didn't know it."),
    (18, (23, 10), None, ["rest"], _emo(("tired", 0.5)),
     "A flat, blurry day. Didn't do much, didn't really track it.",
     "Nothing much to say about today. A grey, in-between sort of day. I didn't scroll all that much but I didn't do much else either. Just tired in a way that isn't really about sleep. Some days are just for getting through, I suppose."),
    (17, (12, 30), 1, ["cooking", "food", "walking"], _emo(("calm", 0.5), ("pride", 0.4)),
     "Walked to the farmers market and cooked with real vegetables. Felt almost wholesome.",
     "Walked to the market this morning, twenty-five minutes each way, and came back with actual vegetables instead of a delivery bag. Spent the afternoon cooking a big stew. There's something steadying about a slow task you do with your hands. The flat smells incredible."),
    (16, (7, 0), 2, ["walking", "screen time", "focus"], _emo(("calm", 0.6), ("energy", 0.5)),
     "Morning walk before work, phone left at home. Clear head all morning.",
     "Tried something new — walked before work and left the phone on the table. Just thirty minutes. I felt almost anxious without it at first, then weirdly free. My head was clear all morning, the kind of clear I used to take for granted. I think the mornings might be the key to this."),
    (15, (16, 45), -1, ["work", "screen time", "mood"], _emo(("stress", 0.6), ("awareness", 0.4)),
     "Stressful day; caught myself scrolling for an hour to numb out — but I noticed it.",
     "Rough day at work, a deadline moved and a meeting that went nowhere. I looked up and I'd been scrolling for an hour, that old anaesthetic. But here's the difference — I noticed. A month ago I wouldn't have. I put it down and made a cup of tea instead. Small win inside a bad day."),
    (14, (20, 30), 2, ["friends", "connection"], _emo(("joy", 0.7), ("gratitude", 0.6)),
     "Dinner with Marcus — laughed for hours and didn't touch my phone once.",
     "Dinner with Marcus. Three hours, two coffees after, and I genuinely did not think about my phone the whole time. We picked up like no time had passed. I'd forgotten what it feels like to laugh until your face hurts. Walking home I felt full in a way takeout never makes me."),
    (13, (19, 0), 2, ["cooking", "food", "habits"], _emo(("satisfaction", 0.6), ("calm", 0.5)),
     "Cooked a big batch of soup for the week. Starting to like this version of me.",
     "Batch-cooked a pot of soup so I've got lunches sorted all week. A month ago this would've sounded impossibly boring and now it feels like a quiet kind of pride. I'm starting to actually like the person doing these things. That's new."),
    (12, (7, 30), 1, ["running", "exercise"], _emo(("pride", 0.5), ("fatigue", 0.5)),
     "First proper run — couch-to-5k week one. My lungs hated me, but I did it.",
     "Started a running plan. Week one, day one. I managed maybe ninety seconds at a time before my lungs staged a protest, and I'm sure I looked ridiculous, but I finished it. The walk part felt like victory and the run part felt like dying, and somehow I want to do it again."),
    (11, (8, 0), 3, ["sleep", "screen time"], _emo(("calm", 0.6), ("energy", 0.6)),
     "Slept eight hours for the first time in ages. Screen time down to three hours.",
     "Eight hours. Real, unbroken sleep, the phone charging out in the hall where it's lived for two weeks now. I woke up before the alarm actually feeling like a person. Checked the weekly screen time out of curiosity — three hours a day, down from nine. I read that number twice."),
    (10, (21, 50), 2, ["reading", "screen time"], _emo(("calm", 0.6), ("contentment", 0.5)),
     "Phone stayed in a drawer all evening while I read. Didn't even miss it.",
     "Put the phone in a drawer after dinner and read until I was sleepy. The remarkable thing is I didn't miss it — didn't even think about it. A month ago an evening like this would have felt like deprivation and tonight it just felt like rest."),
    (9, (18, 15), 0, ["work", "exercise", "habits"], _emo(("frustration", 0.4), ("acceptance", 0.5)),
     "Missed two days of walks — work swallowed me. Not beating myself up over it.",
     "Work ate the last two days whole and the walks didn't happen. The old me would've used that as proof I'd failed and quit the whole thing. Today I just noted it and planned tomorrow's walk. Missing two days isn't falling off; it's just two days. Back at it in the morning."),
    (8, (19, 20), 2, ["family", "connection"], _emo(("warmth", 0.6), ("gratitude", 0.5)),
     "Called my sister Sarah and talked for an hour. Realised how much I'd been hiding.",
     "Rang Sarah, no reason, just to talk. We were on the phone an hour. She said she'd been worried, that I'd gone quiet for months, and hearing that landed hard. I hadn't realised how far I'd pulled back from everyone. It was good to hear her voice. I'll do it more."),
    (7, (22, 40), 3, ["reading"], _emo(("pride", 0.6), ("joy", 0.5)),
     "Finished the book — first one in two years. Started another the same night.",
     "Finished it. The whole book, cover to cover, the first one in maybe two years. I sat with the last page a minute, then got up and pulled another off the shelf and started it. There's a hunger coming back that the scrolling had buried. I'd missed having a mind that stays in one place."),
    (6, (7, 45), 2, ["running", "cooking", "reading", "habits"], _emo(("calm", 0.6), ("contentment", 0.5)),
     "Run, cook, read — the rhythm that's holding me together now.",
     "There's a shape to my days now: a run or a walk in the morning, something cooked in the evening, a book before bed. None of it is dramatic. But it's the scaffolding that the phone used to be, and it holds me up instead of hollowing me out. I trust it a little more each day."),
    (5, (20, 0), 3, ["friends", "cooking", "connection"], _emo(("joy", 0.7), ("pride", 0.6)),
     "Group dinner — Marcus brought friends and I cooked for everyone.",
     "Had people over. Marcus and two of his friends I'd never met, and I cooked for all of them — the big stew, bread, the whole thing. The flat was loud and warm and I was at the centre of it instead of hiding from it. Someone asked for the recipe. I can't remember the last time I felt this useful and this seen."),
    (4, (17, 30), 1, ["work", "screen time", "walking"], _emo(("anxiety", 0.5), ("resolve", 0.5)),
     "Anxious work week; the old urge to vanish into the phone came back. Went for a walk instead.",
     "Hard week, and I felt the old undertow — the urge to just dissolve into the screen and not come up. The difference now is I recognise the feeling for what it is. So I laced up and walked it out instead, forty minutes, until the knot in my chest loosened. The pull doesn't vanish. I'm just getting better at answering it differently."),
    (3, (21, 30), 3, ["screen time", "habits"], _emo(("pride", 0.6), ("gratitude", 0.6)),
     "Average screen time this week: 2h40m, down from nine. Hard to believe.",
     "Checked the weekly report. Two hours forty a day. A month ago it was nine. I genuinely had to look twice. That's six hours a day handed back to me — for walking, cooking, Marcus, Sarah, books, sleep. It doesn't feel like willpower anymore. It just feels like my life fits me better."),
    (2, (11, 0), 2, ["running", "reading", "nature"], _emo(("calm", 0.6), ("contentment", 0.6)),
     "Long Saturday run by the lake, then read in the park. No screens till evening.",
     "A proper Saturday. Ran by the lake — I can do three kilometres without stopping now, which would've been a fantasy a month ago — then sat in the park and read until the light went. Didn't look at my phone until after dinner. It's becoming the default instead of the discipline."),
    (1, (22, 0), 3, ["reflection", "growth", "habits"], _emo(("gratitude", 0.7), ("hope", 0.6)),
     "Looked back at where I was a month ago and barely recognise him.",
     "I read back through this from the start tonight. The man at the top, the one with nine hours of screen time and no one to call, I barely recognise him — and it was four weeks ago. Same flat, same job, same problems, mostly. But I'm living it instead of watching it scroll past. I want to remember this feeling the next time it gets hard, because it will."),
]


# ─────────────────────────────────────────────────────────────────────────────
# John's profile (EVA_MEMORY_ARCHITECTURE §7.2). Shaped to match the journals
# above so Eva's context lands: the goals he's stated, the patterns the entries
# show, the people he reconnected with, what helps and what trips him. Evidence
# ids are filled with REAL entry ids after the entries are written (see main()).
# ─────────────────────────────────────────────────────────────────────────────
def build_john_profile(by_theme: dict[str, list[str]]) -> dict:
    """Build John's profile.json dict, citing real entry ids by theme as evidence.

    ``by_theme`` maps a theme to the entry ids that carry it, so each goal/pattern
    points at entries that genuinely mention it — the same honesty the graph keeps.
    """

    def ev(*themes: str, limit: int = 3) -> list[str]:
        ids: list[str] = []
        for t in themes:
            ids.extend(by_theme.get(t, []))
        # De-dupe preserving order, cap so the evidence stays illustrative.
        seen: list[str] = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        return seen[:limit]

    return {
        "schema_version": 1,
        "identity": {
            "stated_self": "someone trying to live more deliberately and less online",
            "principles": ["presence", "discipline", "connection"],
            "provenance": ev("screen time", "reflection"),
        },
        "goals": [
            {
                "id": "g-1a2b3c4d-0001-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Cut down screen time and stay off the phone late at night",
                "status": "active", "confidence": 0.88, "last_seen": _iso_days_ago(3),
                "evidence": ev("screen time"), "source": "model",
            },
            {
                "id": "g-1a2b3c4d-0002-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Exercise regularly — build up from walks to running",
                "status": "active", "confidence": 0.8, "last_seen": _iso_days_ago(2),
                "evidence": ev("running", "walking", "exercise"), "source": "model",
            },
            {
                "id": "g-1a2b3c4d-0003-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Cook real food at home instead of ordering takeout",
                "status": "active", "confidence": 0.76, "last_seen": _iso_days_ago(5),
                "evidence": ev("cooking", "food"), "source": "model",
            },
            {
                "id": "g-1a2b3c4d-0004-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Read books again",
                "status": "active", "confidence": 0.74, "last_seen": _iso_days_ago(2),
                "evidence": ev("reading"), "source": "model",
            },
            {
                "id": "g-1a2b3c4d-0005-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Reconnect with friends and family",
                "status": "active", "confidence": 0.79, "last_seen": _iso_days_ago(5),
                "evidence": ev("friends", "family", "connection"), "source": "model",
            },
        ],
        "patterns": [
            {
                "id": "p-5e6f7a8b-0001-4d55-8a99-7b8c9d0e1f2a",
                "text": "Reaches for the phone to numb out when bored, lonely, or stressed",
                "type": "behavior", "confidence": 0.82, "last_seen": _iso_days_ago(4),
                "evidence": ev("screen time", "mood"), "source": "model",
            },
            {
                "id": "p-5e6f7a8b-0002-4d55-8a99-7b8c9d0e1f2a",
                "text": "Withdraws from people and cancels plans when his mood dips",
                "type": "behavior", "confidence": 0.7, "last_seen": _iso_days_ago(8),
                "evidence": ev("friends", "family"), "source": "model",
            },
            {
                "id": "p-5e6f7a8b-0003-4d55-8a99-7b8c9d0e1f2a",
                "text": "Drops his new routines first when work stress spikes",
                "type": "behavior", "confidence": 0.66, "last_seen": _iso_days_ago(9),
                "evidence": ev("work", "exercise"), "source": "model",
            },
        ],
        "relationships": [
            {
                "name": "Marcus", "type": "friend",
                "summary": "Close old friend John had drifted from; reconnecting has been a turning point",
                "evidence": ev("friends", "connection"), "last_seen": _iso_days_ago(5),
            },
            {
                "name": "Sarah", "type": "family",
                "summary": "His sister; had been worried about how quiet he'd gone. They're talking again",
                "evidence": ev("family"), "last_seen": _iso_days_ago(8),
            },
        ],
        "emotional_baseline": {
            "typical_mood": 2,
            "known_triggers": ["late-night scrolling", "work stress", "boredom", "loneliness"],
            "what_helps": ["a walk or a run", "cooking", "calling a friend", "reading", "sleep"],
            "evidence": ev("walking", "running", "sleep"),
        },
        "open_loops": [
            {
                "id": "o-9d0e1f2a-0001-4f77-8c11-9d0e1f2a3b4c",
                "description": "Building a steady morning routine that replaces the phone",
                "status": "updated", "opened": _iso_days_ago(26), "last_updated": _iso_days_ago(3),
                "evidence": ev("screen time", "walking", "habits"),
            },
        ],
        "watch_list": [
            {
                "pattern_id": "p-5e6f7a8b-0001-4d55-8a99-7b8c9d0e1f2a",
                "conflicting_goal_id": "g-1a2b3c4d-0001-4e8d-9a11-1f0c2d3e4a5b",
                "description": "Scrolling late at night works against both the screen-time goal and his sleep",
                "evidence": ev("screen time", "sleep"),
            },
        ],
        "anchors": [],
    }


def _iso_days_ago(n: int) -> str:
    """A YYYY-MM-DD `n` days before today (local)."""
    return (date.today() - timedelta(days=n)).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Backup + wipe
# ─────────────────────────────────────────────────────────────────────────────
def _backup_vault() -> Path | None:
    """Copy the current profile + journal Markdown + eva.db into a timestamped dir.

    Returns the backup directory (or None if there was nothing to back up). We copy
    only the user's irreplaceable data, not the big model/chroma dirs, so the
    backup is quick and small while still being a full restore of journals+profile.
    """
    vdir = vault_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = vdir.parent / f"local_vault.bak-{stamp}"
    copied = False
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("profile.json", "profile.md", "eva.db"):
        src = vdir / name
        if src.exists():
            shutil.copy2(src, dest / name)
            copied = True
    journal = vdir / "journal"
    if journal.exists() and any(journal.iterdir()):
        shutil.copytree(journal, dest / "journal", dirs_exist_ok=True)
        copied = True
    if not copied:
        dest.rmdir()
        return None
    return dest


def _wipe(no_embed: bool) -> None:
    """Delete all journal data: Markdown, profile, DB rows, and vectors."""
    vdir = vault_dir()

    # 1. L0 Markdown day files.
    journal = vault.journal_dir()
    n_md = 0
    if journal.exists():
        for p in journal.glob("*.md"):
            p.unlink()
            n_md += 1
    log.info("removed %d journal Markdown file(s)", n_md)

    # 2. Profile (will be rewritten as John's).
    for name in ("profile.json", "profile.md"):
        p = vdir / name
        if p.exists():
            p.unlink()

    # 3. L1/L2 SQLite rows. Deleting entries cascades to extractions + mood_series;
    #    the graph + chat tables are cleared explicitly. Both is_seeded 0 and 1 go.
    conn = db.get_or_create_db()
    try:
        for table in ("graph_edges", "graph_nodes", "chat_turns", "conversations", "digests", "entries"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    log.info("cleared SQLite index/extraction/mood/graph/chat rows")

    # 4. ChromaDB journal vectors (best-effort — skipped with --no-embed).
    if not no_embed:
        try:
            from memory import vector
            col = vector._get_collection()  # noqa: SLF001 — the seam we need
            existing = col.get()
            ids = existing.get("ids") or []
            if ids:
                col.delete(ids=ids)
            log.info("cleared %d journal vector(s) from ChromaDB", len(ids))
        except Exception as exc:  # noqa: BLE001 — embeddings are best-effort
            log.warning("could not clear ChromaDB vectors (continuing): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Write John's entries
# ─────────────────────────────────────────────────────────────────────────────
def _write_entries(no_embed: bool) -> tuple[int, dict[str, list[str]]]:
    """Write every John entry as a real (is_seeded=0) entry. Returns (count, by_theme).

    Each entry: L0 Markdown (via vault.save_entry with a backdated timestamp), then
    the L1 index row + a finished extraction + the mood point, then a best-effort
    L2 embedding — mirroring memory.capture for a genuinely-written entry, but with
    hand-authored extraction data so no model is needed.
    """
    today = date.today()
    by_theme: dict[str, list[str]] = {}
    seeded_for_embed: list[dict] = []
    count = 0

    conn = db.get_or_create_db()
    try:
        # Oldest first, so created_at order matches calendar order.
        for days_ago, (hh, mm), mood, themes, emotions, summary, body in sorted(
            JOHN_DAYS, key=lambda r: -r[0]
        ):
            d = today - timedelta(days=days_ago)
            when = datetime.combine(d, time(hh, mm))

            rec = vault.save_entry(body, "journal", when=when)
            db.insert_entry(
                conn, id=rec.id, date=rec.date, type=rec.type,
                text=rec.text, word_count=rec.word_count, created_at=rec.created_at,
                is_seeded=False,
            )
            db.create_pending_extraction(conn, rec.id)
            db.finalize_extraction(
                conn, rec.id,
                mood=mood, emotions=emotions, entities=[], themes=themes,
                events=[], stated_goals=[], behaviors=[], decisions=[],
                open_loops=[], self_judgments=[], summary=summary,
                extracted_at=rec.created_at,
            )
            db.upsert_mood_series(
                conn, entry_id=rec.id, date=rec.date,
                mood=mood, emotions=emotions, is_seeded=False,
            )
            for t in themes:
                by_theme.setdefault(t, []).append(rec.id)
            seeded_for_embed.append(
                {"entry_id": rec.id, "date": rec.date, "summary": summary,
                 "mood": mood, "themes": themes}
            )
            count += 1
    finally:
        conn.close()

    if not no_embed:
        try:
            from memory import vector
            for s in seeded_for_embed:
                vector.embed_summary(
                    entry_id=s["entry_id"], date=s["date"], summary=s["summary"],
                    mood=s["mood"], themes=s["themes"], is_seeded=False,
                )
            log.info("embedded %d summaries into ChromaDB (is_seeded=0)", len(seeded_for_embed))
        except Exception as exc:  # noqa: BLE001 — embedding is a bonus, not required
            log.warning("skipped embedding John's summaries (entries still saved): %s", exc)

    return count, by_theme


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Reset the vault to the John demo persona.")
    parser.add_argument(
        "--no-embed", action="store_true",
        help="Skip all ChromaDB work (clearing + embedding). DB + Markdown only.",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip backing up the current vault before wiping (not recommended).",
    )
    args = parser.parse_args()

    if not args.no_backup:
        backup = _backup_vault()
        if backup:
            log.info("backed up current vault data → %s", backup)
        else:
            log.info("nothing to back up (empty vault)")

    _wipe(args.no_embed)

    count, by_theme = _write_entries(args.no_embed)
    moods = [d[2] for d in JOHN_DAYS if d[2] is not None]
    log.info(
        "wrote %d John journal(s) across the past month (mood %d..%d, %d blank day)",
        count, min(moods), max(moods), len(JOHN_DAYS) - len(moods),
    )

    saved = profile.save_profile(profile.Profile.from_dict(build_john_profile(by_theme)))
    log.info(
        "wrote John's profile: %d goal(s), %d pattern(s), %d relationship(s)",
        len(saved.goals), len(saved.patterns), len(saved.relationships),
    )

    # Derive the Connections graph from the same extractions (is_seeded=0).
    conn = db.get_or_create_db()
    try:
        n_nodes, n_edges = graph.store_graph(conn)
    finally:
        conn.close()
    log.info("built John's connections graph: %d node(s), %d edge(s)", n_nodes, n_edges)

    print(
        "\nDone. The vault is now John: ~a month of real, backdated journal entries "
        "(browsable + editable + recall-able) and his profile. Insights derive from "
        "these as real data — no demo toggle needed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
