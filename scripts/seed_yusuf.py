#!/usr/bin/env python3
"""Reset the vault to the "Yusuf" demo: a man on a hard path, ~two months of journals.

# DEV-FIXTURE — real data through the real pipeline, never shipped.

This mirrors scripts/seed_john.py exactly in mechanism, only the person and their
arc differ. Every journal here is a **real entry** (``is_seeded=0``): it lands in
the L0 Markdown vault as the source of truth AND is indexed into SQLite + embedded
into ChromaDB, exactly the way a genuinely-written entry flows through
``memory.capture`` — but with hand-authored extraction (mood, emotions, themes,
summary) so no model call is needed. The Insights screens therefore derive from
these as real data (mood arc, growth comparison, connections graph).

Yusuf's arc: a young Muslim man trying to become disciplined and self-mastered —
pray all five (especially Fajr), quit pornography, train at the gym, control his
anger, read Quran and lower his gaze, be a stronger man for his mother. He fails a
LOT along the way: relapses at night on his phone, sleeps through Fajr, snaps at
his mother, spirals into shame and drops everything for days. But the spirals get
shorter and the wins more frequent — he stops falling off and starts *failing
forward*. The mood line is volatile (many dips) with a slow steadying by the end,
so growth analytics has a genuine early-vs-late signal to compare.

What it does (identical to seed_john): backs up profile+journals+eva.db into
``local_vault.bak-<timestamp>/``, wipes the old data, writes the backdated
entries + profile, and rebuilds the connections graph. Fully reversible.

Usage (from anywhere):
    python scripts/seed_yusuf.py
    python scripts/seed_yusuf.py --no-embed    # DB + Markdown only (skip ChromaDB)
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

from memory import db, graph, operations, profile, vault, vault_dir  # noqa: E402

log = logging.getLogger("eva.seed_yusuf")


def _emo(*pairs: tuple[str, float]) -> list[dict]:
    """Build the emotions list shape ([{name, intensity}, ...]) tersely."""
    return [{"name": name, "intensity": intensity} for name, intensity in pairs]


# ─────────────────────────────────────────────────────────────────────────────
# Yusuf's ~two months. Each tuple is:
#   (days_ago, (hour, minute), mood, themes, emotions, summary, body)
# `mood` is -5..5 (None for the one blank, un-scored day). `body` is the journal
# text; `summary` is the one line the extraction would have distilled (used by the
# mood tooltip + recall + graph). Times are spread across the day to fit each
# entry (a 1am relapse, a 5:50am Fajr, an evening halaqa ...).
# ─────────────────────────────────────────────────────────────────────────────
YUSUF_DAYS: list[tuple] = [
    (57, (23, 50), -2,
     ["nofap", "relapse", "shame", "phone", "masculinity"],
     _emo(("shame", 0.85), ("guilt", 0.7), ("resolve", 0.4)),
     "Relapsed again and decided, for the hundredth time, that this is the last time.",
     "It happened again tonight. Phone in bed, one thing led to another, and afterwards I just lay there in the dark feeling like less of a man. I keep saying I want to be disciplined, God-conscious, someone my future family could lean on — and then I fold the moment I'm alone and tired. Bismillah. Day zero. Again."),

    (56, (13, 10), -1,
     ["fajr", "prayer", "hope", "discipline"],
     _emo(("hope", 0.5), ("determination", 0.6), ("guilt", 0.4)),
     "Slept through Fajr on day one but made a real plan instead of quitting.",
     "Of course I slept through Fajr the very first morning. Woke to the guilt before I woke to the day. But I prayed Dhuhr on time and actually sat and made a plan: phone charges in the kitchen, alarm across the room, wudu before bed. Small things. I'm tired of grand intentions that die by noon."),

    (54, (22, 30), -3,
     ["relapse", "nofap", "shame", "scrolling"],
     _emo(("shame", 0.9), ("discouragement", 0.75), ("anger", 0.5)),
     "Broke the streak at three days — furious at how weak I felt.",
     "Three days. That's all I lasted. I told myself I'd just check one thing on my phone and an hour later I'd thrown it all away again. I'm so angry at myself I could scream. What kind of man can't govern his own hand? I don't even feel sad, just disgusted."),

    (52, (6, 5), 2,
     ["fajr", "prayer", "gym", "hope", "discipline"],
     _emo(("pride", 0.6), ("hope", 0.7), ("determination", 0.7)),
     "Caught Fajr on time and hit the gym after — the first day that felt like the man I want to be.",
     "Alarm went off, I actually got up, made wudu and prayed Fajr while it was still dark. Then I went straight to the gym before work. My body ached and my head was clear for the first time in weeks. This is it. This is the feeling. If I could bottle this morning I'd never relapse again."),

    (51, (14, 0), 1,
     ["quran", "gaze", "discipline"],
     _emo(("peace", 0.6), ("gratitude", 0.5)),
     "Read Quran at lunch and kept my gaze down all day — felt quietly strong.",
     "Read a page of Quran with the translation on my break instead of scrolling. Lowered my gaze on the train and it was hard and then it wasn't. Nobody saw it and it didn't feel dramatic, but I know I chose it. That's the kind of strength no one claps for."),

    (49, (23, 20), -2,
     ["anger", "family", "mother", "shame"],
     _emo(("anger", 0.7), ("guilt", 0.8)),
     "Snapped at my mother over nothing and felt like a hypocrite.",
     "Mum asked me to take out the bins twice and I bit her head off like a child. Here I am trying to be some disciplined, honourable man and I can't even keep my temper with the woman under whose feet paradise lies. I apologised but the look on her face is going to sit with me. The gap between who I'm pretending to be and who I actually am is humiliating."),

    (48, (5, 50), 1,
     ["fajr", "prayer", "brotherhood", "mosque"],
     _emo(("hope", 0.6), ("contentment", 0.6)),
     "Prayed Fajr at the masjid and Bilal pulled me into the brothers' circle.",
     "Dragged myself to the masjid for Fajr in the cold. Bilal was there and afterwards he sat with me and a few of the brothers for tea. Nobody preached. They just made space. I forget how much lighter I feel around men who are aiming at the same thing instead of laughing at it."),

    (46, (0, 30), -3,
     ["relapse", "nofap", "lust", "scrolling", "phone"],
     _emo(("shame", 0.9), ("guilt", 0.8), ("loneliness", 0.6)),
     "Late-night phone, lust, relapse — the exact trap I keep walking into.",
     "Same trap, same time of night. Everyone asleep, me awake with the phone, and the whole plan I made evaporated. It's always the phone. It's always after midnight. It's always when I feel alone. I can see the pattern so clearly and I still walk into it like I've learned nothing."),

    (45, (12, 0), None,
     ["reflection"],
     _emo(("discouragement", 0.5)),
     "A flat, foggy day I didn't really track.",
     "Not much to say. Tired in a way sleep doesn't fix. Didn't relapse, didn't pray with any presence either, just moved through the day grey and half-here. Some days you're not climbing or falling, you're just getting through. Allah knows."),

    (44, (16, 30), 0,
     ["work", "anger", "patience"],
     _emo(("frustration", 0.6), ("resolve", 0.4)),
     "Awful day at work; held my temper by a thread and counted that as something.",
     "My manager dumped a mess on me an hour before leaving and I felt the old heat rise up my neck. A month ago I'd have said something I'd regret. Today I breathed, said 'I'll sort it,' and walked to the stairwell to cool off. It wasn't graceful but I didn't lose it. Sabr is a muscle and mine is weak, but I used it."),

    (42, (5, 45), 3,
     ["fajr", "gym", "discipline", "hope"],
     _emo(("pride", 0.7), ("determination", 0.8)),
     "Three-day streak, Fajr on time, and a new deadlift PR. Momentum.",
     "Fajr done, gym done, and I pulled a weight today I couldn't have touched last month. Three days clean too. There's a rhythm forming — sleep early, phone away, up for Fajr, train, work, Quran at night. When I stack the days like this I feel like I'm becoming someone, not just resisting someone."),

    (41, (21, 15), 2,
     ["quran", "brotherhood", "gratitude", "mosque"],
     _emo(("gratitude", 0.7), ("peace", 0.6)),
     "Halaqa with the brothers left me full instead of empty.",
     "Went to the weekly halaqa. We read, we asked stupid questions, we laughed. Bilal walked me home after and we talked about how hard the nights are, and it turns out he fights the same battle I do. I'm not some uniquely broken case. Every man in that room is wrestling something."),

    (39, (23, 55), -4,
     ["relapse", "nofap", "shame", "discouragement"],
     _emo(("shame", 0.95), ("discouragement", 0.85)),
     "Six clean days, then a total collapse. This one really hurt.",
     "Six days. I was proud. I let myself feel proud and then tonight I threw it all in the bin. It hurts more this time because I'd started to believe I was changing. I feel like a fraud — praying Fajr in the morning and doing this at night. How can Allah look at a man this two-faced? I don't even want to write anymore."),

    (38, (13, 0), -2,
     ["shame", "prayer", "fajr", "discipline"],
     _emo(("guilt", 0.7), ("discouragement", 0.6)),
     "Shame hangover: skipped Fajr and the gym, wanted to disappear.",
     "The day after is always worse than the night itself. Slept through Fajr because I stayed up hating myself, skipped the gym, ate rubbish, avoided everyone. The relapse is a moment; the shame is what actually steals the week. I know this and I still let it drag me under."),

    (36, (18, 40), 0,
     ["brotherhood", "mosque", "hope"],
     _emo(("relief", 0.5), ("hope", 0.5)),
     "Bilal noticed I'd gone quiet and called — talking pulled me out of the pit.",
     "Bilal messaged, then rang when I didn't reply. I nearly ignored it out of shame but I picked up. Told him I'd fallen and gone dark. He didn't lecture, just said 'get up, make wudu, come to Isha, the door doesn't close on you.' So I went. First time all week I felt like a person instead of a failure."),

    (34, (5, 50), 2,
     ["fajr", "prayer", "discipline", "hope"],
     _emo(("determination", 0.7), ("hope", 0.6)),
     "Restarted properly — phone out of the bedroom, Fajr on time.",
     "New line in the sand, but a smaller, more honest one this time. Not 'never again forever' — just tonight the phone sleeps in the kitchen and tomorrow I pray Fajr. That's the whole promise. I kept it. The phone being out of arm's reach changes everything; half my battle is just geography."),

    (33, (7, 30), 3,
     ["gym", "masculinity", "discipline"],
     _emo(("pride", 0.7), ("contentment", 0.6)),
     "Gym before work, feeling physically capable and calm in my own skin.",
     "Trained hard this morning and caught myself in the mirror between sets — I actually look like someone who takes care of himself now. It's not vanity. A man should be strong enough to carry his own weight and someone else's. Being capable in my body makes me steadier in my head. Fewer stupid urges when I'm tired in the right way."),

    (31, (22, 50), -2,
     ["scrolling", "phone", "nofap", "lust"],
     _emo(("frustration", 0.7), ("anxiety", 0.5)),
     "Didn't relapse but scrolled right to the edge — that scared me.",
     "I didn't fall tonight but I walked all the way up to the cliff and looked over. Picked up the phone 'just to check something,' felt the pull start, and only stopped because I heard Mum cough in the next room. Not relapsing isn't the same as being safe. I let myself get close and that's its own kind of losing."),

    (30, (14, 20), 1,
     ["work", "patience", "anger"],
     _emo(("resolve", 0.6), ("pride", 0.4)),
     "Held my composure in a tense meeting — small, real win.",
     "Same colleague, same jabs, and this time I answered the point instead of the person. Kept my voice level. Afterwards he actually agreed with me. Anger used to feel like strength; today keeping it in felt like more strength. Maybe that's the whole thing I keep missing about being a man — it's control, not force."),

    (29, (20, 0), 2,
     ["quran", "gaze", "gratitude"],
     _emo(("gratitude", 0.7), ("peace", 0.6)),
     "A good deen day — Quran, lowered gaze, early night.",
     "Read more Quran than usual and it actually landed tonight. Lowered my gaze all day without it feeling like a fight. Phone away by ten. No fireworks, just a clean, quiet, God-conscious day. I want a hundred more of these ordinary ones. That's what a life is made of, not the dramatic days."),

    (27, (1, 10), -3,
     ["relapse", "nofap", "shame", "scrolling", "phone"],
     _emo(("shame", 0.9), ("guilt", 0.8)),
     "Relapse at 1am. Same trap: awake, alone, phone in hand.",
     "It's one in the morning and I did it again. I don't even have a new thing to say about it — that's the worst part. Night, alone, phone, tired, and I hand myself over. I'm so predictable to my own worst self. The plan works right up until the moment I let the phone into the room."),

    (26, (12, 30), -1,
     ["shame", "reflection", "patience"],
     _emo(("discouragement", 0.6), ("resolve", 0.5)),
     "For once I studied the pattern instead of just hating myself for it.",
     "Trying something different today: instead of drowning in shame I sat and looked at the relapse like data. Every single time it's after midnight, phone in reach, feeling low or lonely. That's not a moral mystery, it's a predictable chain. If I break one link — the phone in the room — I break the chain. Hating myself has never once fixed it. Maybe understanding it will."),

    (24, (5, 55), 2,
     ["fajr", "brotherhood", "mosque", "hope"],
     _emo(("hope", 0.7), ("contentment", 0.6)),
     "Fajr at the masjid with the brothers — a clean restart.",
     "Got up for Fajr at the masjid. Cold, dark, worth it. Bilal grinned when he saw me because he knows what showing up after a fall costs. Stood shoulder to shoulder in the rows and felt the shame lift a little. Allah keeps opening the door; the least I can do is keep walking back through it."),

    (23, (18, 0), 1,
     ["gym", "family", "mother"],
     _emo(("gratitude", 0.6), ("pride", 0.4)),
     "Apologised properly to Mum and trained after — a straighter day.",
     "Sat with Mum and actually apologised for how I've been snapping, not the mumbled kind, the real kind. She just put her hand on my head like when I was small. Then I went and trained. A man who can't be gentle with his own mother has no business calling himself strong. I want to protect her peace, not disturb it."),

    (22, (21, 40), 3,
     ["quran", "discipline", "gratitude"],
     _emo(("peace", 0.7), ("gratitude", 0.7)),
     "Best day in weeks — steady, present, thankful.",
     "Prayed all five on time today. All five. Read Quran, trained, was kind at home, phone away early. I keep waiting for the version of me that never slips, but tonight I think the point isn't perfection — it's that days like this are becoming possible at all. Alhamdulillah for a good one."),

    (20, (5, 45), 3,
     ["fajr", "gym", "discipline", "masculinity"],
     _emo(("pride", 0.7), ("determination", 0.8)),
     "Streak building again — Fajr, gym, focus. I feel like myself.",
     "Several days stacked clean now and the difference in how I carry myself is obvious. Up for Fajr, strong in the gym, patient at home. This is what I mean by masculine — not loud, not hard, just governed. A man who runs his own house, starting with the house of his own body and habits."),

    (19, (23, 30), -2,
     ["scrolling", "phone", "anxiety", "fajr"],
     _emo(("anxiety", 0.6), ("frustration", 0.5)),
     "Let the phone back into the room 'just for a minute' and stayed up too late.",
     "Got cocky. Told myself I'm strong enough now to have the phone by the bed. Didn't relapse but scrolled until half midnight and I already know Fajr is going to be a casualty. The rule wasn't the problem, my ego was. The moment I think I've beaten this is the moment I hand it the door key."),

    (17, (13, 0), -3,
     ["relapse", "nofap", "shame"],
     _emo(("shame", 0.9), ("discouragement", 0.7)),
     "Slipped again — but the spiral was shorter this time.",
     "Fell last night. Old me would've written off the whole week by now, skipped every prayer out of shame, gone completely dark. Instead I'm writing this at lunch having already prayed Dhuhr. It still hurts, I still feel like a hypocrite, but I'm not letting one night become seven. That's not nothing."),

    (16, (16, 10), 0,
     ["reflection", "patience", "hope"],
     _emo(("resolve", 0.5), ("hope", 0.4)),
     "Naming it 'failing forward' instead of just failing.",
     "I read a line somewhere: you don't have to be undefeated, you have to be undeterred. I've relapsed more times than I can count these two months. But I'm praying more than I was, training more, snapping less, and my collapses are shorter. If I only measure by the falls I'll quit. If I measure by the trend, I'm actually moving. Both are true and I get to choose which one I feed."),

    (15, (20, 20), 1,
     ["brotherhood", "quran", "gratitude", "mosque"],
     _emo(("gratitude", 0.6), ("peace", 0.5)),
     "Halaqa again — reminded I'm not walking this alone.",
     "Back at the halaqa. Told the brothers honestly that I'd slipped this week and instead of judgement I got three men saying 'me too, here's what helped.' There's a strength in a room full of men admitting weakness that I never found in pretending I had none. Brotherhood is half the deen for a reason."),

    (13, (5, 50), 2,
     ["fajr", "prayer", "discipline", "hope"],
     _emo(("determination", 0.7), ("hope", 0.6)),
     "Fajr on time and, crucially, no all-or-nothing thinking.",
     "Up for Fajr, phone in the kitchen where it belongs. The big shift lately isn't that I've stopped slipping — it's that a slip no longer detonates the whole project. I fall, I make wudu, I get back in the row. Consistency isn't a straight line, it's how fast you turn around."),

    (12, (7, 15), 3,
     ["gym", "masculinity", "discipline"],
     _emo(("pride", 0.7), ("contentment", 0.6)),
     "Strong session, provider mindset settling in.",
     "Good heavy session before work. I've been thinking about why I even want to be strong, disciplined, all of it. It's not to impress anyone. It's so that when life leans its weight on me — a wife one day, kids, my mother getting older — I don't buckle. I want to be a man people can stand behind. That starts now, with the boring reps."),

    (10, (0, 45), -2,
     ["scrolling", "nofap", "lust", "anger"],
     _emo(("frustration", 0.7), ("shame", 0.5)),
     "Near miss at midnight — angry, but I stopped and didn't spiral.",
     "Crept up to the edge again tonight. Phone, late, that familiar pull. I got close, felt the anger at myself rising, and this time I actually put the phone in the hallway mid-urge and did two rak'ah instead. Heart pounding, but I stopped. It's ugly progress but it's progress. A month ago that night ends the other way."),

    (9, (14, 0), 1,
     ["work", "patience", "family", "mother"],
     _emo(("resolve", 0.6), ("gratitude", 0.4)),
     "Patient at work and gentle at home in the same day.",
     "Kept my cool through a frustrating handover at work, then came home and actually listened to Mum's long story about her sister without sighing once. Little things. But the anger that used to leak out of me all day is quieter now. I think the deen and the discipline are the same project wearing different clothes."),

    (8, (19, 30), 2,
     ["quran", "gaze", "gratitude", "hope"],
     _emo(("gratitude", 0.7), ("hope", 0.6)),
     "Consistency is starting to feel possible, not just hoped-for.",
     "Read Quran after Maghrib and it felt like meeting an old friend. Lowered my gaze today without the usual war. I used to think change would arrive as a lightning bolt where I'd suddenly be fixed. It's not that. It's a thousand small correct choices, most of them boring, a few of them failed, all of them counting."),

    (6, (5, 45), 3,
     ["fajr", "brotherhood", "mosque", "discipline"],
     _emo(("pride", 0.7), ("peace", 0.6)),
     "Fajr in congregation to cap the steadiest stretch yet.",
     "Fajr at the masjid, standing between Bilal and an old uncle who's prayed every dawn for forty years. I looked at him and thought: that's decades of just showing up, most mornings tired, some mornings faithless, all mornings there. That's the whole secret. Not intensity. Presence, repeated. This is the best stretch I've had since I started."),

    (5, (22, 0), -1,
     ["relapse", "nofap", "shame", "reflection"],
     _emo(("guilt", 0.6), ("resolve", 0.5)),
     "Relapsed — but got straight up, prayed, and refused the week-long spiral.",
     "So I fell tonight. And I'm disappointed, genuinely. But look at what didn't happen: I didn't skip Isha, I didn't go dark on the brothers, I didn't decide the whole two months were a lie. I made wudu, prayed, and I'm writing this instead of doom-scrolling deeper. The relapse used to own the next seven days. Tonight it gets one hour."),

    (3, (6, 0), 2,
     ["fajr", "gym", "discipline", "hope"],
     _emo(("determination", 0.7), ("hope", 0.7)),
     "Right back on it the very next morning — Fajr and the gym.",
     "Up for Fajr the morning after a fall, which is the exact move the old me could never make. Trained after. The proof I'm changing isn't that I don't slip; it's how quickly I come back. Speed of return — that's the metric that actually matters, and mine has gone from days to hours."),

    (2, (13, 30), 2,
     ["patience", "anger", "family", "mother"],
     _emo(("contentment", 0.6), ("gratitude", 0.6)),
     "Real patience with Mum today — and I actually meant it.",
     "Mum was anxious and repeating herself and I felt the old irritation flicker — then it just... passed. I made her tea and sat with her. Two months ago that irritation would've become words. Being a man my family feels safe around is worth more than any streak. This is the point of all of it, really."),

    (1, (20, 45), 3,
     ["quran", "gratitude", "masculinity", "discipline"],
     _emo(("gratitude", 0.8), ("peace", 0.7), ("hope", 0.7)),
     "Still stumbling, but not the same man who started this. Failing forward.",
     "Looked back over these weeks tonight. So many falls. But the man writing this prays more, trains, keeps his temper, comes back fast, and leans on his brothers instead of hiding. I haven't arrived. I'm not sure you ever do. But I'm pointed the right way and I'm still walking, and after everything, that feels like the real victory. Not perfection — direction."),

    (0, (5, 50), 2,
     ["fajr", "prayer", "hope", "consistency"],
     _emo(("hope", 0.7), ("determination", 0.7)),
     "Made Fajr today. Not perfect — just showing up, which is the whole game.",
     "Fajr, on time, phone in the kitchen. That's the entire report for today and it's enough. I've stopped chasing the fantasy of a flawless version of me. I just want to be here tomorrow morning too, and the one after. Show up, fall, return, repeat. Ya Allah, keep me walking."),
]


# ─────────────────────────────────────────────────────────────────────────────
# Yusuf's profile (EVA_MEMORY_ARCHITECTURE §7.2). Shaped to match the journals
# above so Eva's context lands. Evidence ids are filled with REAL entry ids after
# the entries are written (see main()), each goal/pattern pointing at entries that
# genuinely carry its theme — the same honesty the graph keeps.
# ─────────────────────────────────────────────────────────────────────────────
def build_yusuf_profile(by_theme: dict[str, list[str]]) -> dict:
    """Build Yusuf's profile.json dict, citing real entry ids by theme as evidence."""

    def ev(*themes: str, limit: int = 3) -> list[str]:
        ids: list[str] = []
        for t in themes:
            ids.extend(by_theme.get(t, []))
        seen: list[str] = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        return seen[:limit]

    return {
        "schema_version": 2,
        "identity": {
            "stated_self": "a Muslim man trying to master himself — disciplined, God-conscious, and strong for the people who depend on him",
            "principles": ["discipline", "God-consciousness (taqwa)", "self-mastery", "responsibility"],
            "provenance": {
                "stated_self": {
                    "evidence": ev("masculinity", "discipline", "reflection"),
                    "source": "model", "last_seen": _iso_days_ago(1),
                },
                "principles": {
                    "evidence": ev("discipline", "fajr", "patience"),
                    "source": "model", "last_seen": _iso_days_ago(1),
                },
            },
        },
        "goals": [
            {
                "id": "g-2b3c4d5e-0001-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Pray all five salah on time, especially Fajr",
                "status": "active", "confidence": 0.86, "last_seen": _iso_days_ago(0),
                "evidence": ev("fajr", "prayer"), "source": "model",
            },
            {
                "id": "g-2b3c4d5e-0002-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Quit pornography and master his desires (nofap)",
                "status": "active", "confidence": 0.82, "last_seen": _iso_days_ago(5),
                "evidence": ev("nofap", "relapse", "lust"), "source": "model",
            },
            {
                "id": "g-2b3c4d5e-0003-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Train at the gym consistently and build physical strength",
                "status": "active", "confidence": 0.8, "last_seen": _iso_days_ago(3),
                "evidence": ev("gym", "masculinity"), "source": "model",
            },
            {
                "id": "g-2b3c4d5e-0004-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Control his anger and respond with patience (sabr)",
                "status": "active", "confidence": 0.74, "last_seen": _iso_days_ago(2),
                "evidence": ev("anger", "patience"), "source": "model",
            },
            {
                "id": "g-2b3c4d5e-0005-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Read Quran daily and lower his gaze",
                "status": "active", "confidence": 0.77, "last_seen": _iso_days_ago(1),
                "evidence": ev("quran", "gaze"), "source": "model",
            },
            {
                "id": "g-2b3c4d5e-0006-4e8d-9a11-1f0c2d3e4a5b",
                "text": "Be a stronger, gentler man for his mother and future family",
                "status": "active", "confidence": 0.75, "last_seen": _iso_days_ago(2),
                "evidence": ev("mother", "family", "masculinity"), "source": "model",
            },
        ],
        "patterns": [
            {
                "id": "p-6f7a8b9c-0001-4d55-8a99-7b8c9d0e1f2a",
                "text": "Relapses late at night when he is alone, tired, and scrolling on his phone",
                "type": "behavior", "confidence": 0.88, "last_seen": _iso_days_ago(5),
                "evidence": ev("relapse", "scrolling", "phone"), "source": "model",
            },
            {
                "id": "p-6f7a8b9c-0002-4d55-8a99-7b8c9d0e1f2a",
                "text": "Spirals into shame after a slip and abandons prayer and routine for days",
                "type": "behavior", "confidence": 0.8, "last_seen": _iso_days_ago(17),
                "evidence": ev("shame", "relapse"), "source": "model",
            },
            {
                "id": "p-6f7a8b9c-0003-4d55-8a99-7b8c9d0e1f2a",
                "text": "Sleeps through Fajr when he stays up late, which unravels the rest of the day",
                "type": "behavior", "confidence": 0.72, "last_seen": _iso_days_ago(19),
                "evidence": ev("fajr", "scrolling"), "source": "model",
            },
            {
                "id": "p-6f7a8b9c-0004-4d55-8a99-7b8c9d0e1f2a",
                "text": "Lets his temper flare with his mother when he feels like a failure",
                "type": "behavior", "confidence": 0.66, "last_seen": _iso_days_ago(2),
                "evidence": ev("anger", "mother"), "source": "model",
            },
            {
                "id": "p-6f7a8b9c-0005-4d55-8a99-7b8c9d0e1f2a",
                "text": "Gets overconfident after a good streak and lets the phone back into the bedroom",
                "type": "behavior", "confidence": 0.62, "last_seen": _iso_days_ago(19),
                "evidence": ev("phone", "scrolling"), "source": "model",
            },
        ],
        "relationships": [
            {
                "name": "Bilal", "type": "friend",
                "summary": "A brother from the masjid who keeps Yusuf accountable and pulls him back after every fall",
                "evidence": ev("brotherhood", "mosque"), "last_seen": _iso_days_ago(6),
            },
            {
                "name": "His mother", "type": "family",
                "summary": "Lives with her; his temper with her is his deepest recurring guilt, and her ease is what he most wants to protect",
                "evidence": ev("mother", "family"), "last_seen": _iso_days_ago(2),
            },
        ],
        "emotional_baseline": {
            # typical_mood is omitted here — it is code-derived from the written
            # entries' moods (operations.apply_typical_mood in main()), never
            # hand-authored, so the demo profile honours the R7.5 invariant.
            "known_triggers": ["being alone late at night", "the phone in the bedroom", "lack of sleep", "shame after a relapse", "work stress"],
            "what_helps": ["praying Fajr in congregation", "the gym", "calling Bilal", "reading Quran", "keeping the phone out of the bedroom"],
            "provenance": {
                "known_triggers": {
                    "evidence": ev("relapse", "phone", "shame"),
                    "source": "model", "last_seen": _iso_days_ago(5),
                },
                "what_helps": {
                    "evidence": ev("fajr", "gym", "brotherhood"),
                    "source": "model", "last_seen": _iso_days_ago(0),
                },
            },
        },
        "open_loops": [
            {
                "id": "o-0e1f2a3b-0001-4f77-8c11-9d0e1f2a3b4c",
                "description": "Breaking the late-night phone-to-relapse chain for good",
                "status": "updated", "opened": _iso_days_ago(57), "last_updated": _iso_days_ago(5),
                "evidence": ev("relapse", "phone", "nofap"),
            },
            {
                "id": "o-0e1f2a3b-0002-4f77-8c11-9d0e1f2a3b4c",
                "description": "Making Fajr in congregation a settled daily habit, not a good-week luxury",
                "status": "updated", "opened": _iso_days_ago(56), "last_updated": _iso_days_ago(0),
                "evidence": ev("fajr", "mosque"),
            },
        ],
        "watch_list": [
            {
                "pattern_id": "p-6f7a8b9c-0001-4d55-8a99-7b8c9d0e1f2a",
                "conflicting_goal_id": "g-2b3c4d5e-0002-4e8d-9a11-1f0c2d3e4a5b",
                "description": "Late-night phone scrolling works against both the nofap goal and catching Fajr",
                "evidence": ev("relapse", "phone", "fajr"),
            },
        ],
        "anchors": [],
    }


def _iso_days_ago(n: int) -> str:
    """A YYYY-MM-DD `n` days before today (local)."""
    return (date.today() - timedelta(days=n)).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Backup + wipe (identical mechanism to seed_john.py)
# ─────────────────────────────────────────────────────────────────────────────
def _backup_vault() -> Path | None:
    """Copy the current profile + journal Markdown + eva.db into a timestamped dir."""
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

    journal = vault.journal_dir()
    n_md = 0
    if journal.exists():
        for p in journal.glob("*.md"):
            p.unlink()
            n_md += 1
    log.info("removed %d journal Markdown file(s)", n_md)

    for name in ("profile.json", "profile.md"):
        p = vdir / name
        if p.exists():
            p.unlink()

    conn = db.get_or_create_db()
    try:
        for table in ("graph_edges", "graph_nodes", "chat_turns", "conversations", "digests", "entries"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    log.info("cleared SQLite index/extraction/mood/graph/chat rows")

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
# Write Yusuf's entries (identical mechanism to seed_john.py)
# ─────────────────────────────────────────────────────────────────────────────
def _write_entries(no_embed: bool) -> tuple[int, dict[str, list[str]]]:
    """Write every Yusuf entry as a real (is_seeded=0) entry. Returns (count, by_theme)."""
    today = date.today()
    by_theme: dict[str, list[str]] = {}
    seeded_for_embed: list[dict] = []
    count = 0

    conn = db.get_or_create_db()
    try:
        for days_ago, (hh, mm), mood, themes, emotions, summary, body in sorted(
            YUSUF_DAYS, key=lambda r: -r[0]
        ):
            d = today - timedelta(days=days_ago)
            when = datetime.combine(d, time(hh, mm))

            rec = vault.save_entry(body, "journal", when=when)
            db.insert_entry(
                conn, id=rec.id, date=rec.date, type=rec.type,
                text=rec.text, word_count=rec.word_count, created_at=rec.created_at,
                is_seeded=False,
            )
            source_hash = vault.source_hash(rec.text)
            db.create_pending_extraction(conn, rec.id, source_hash=source_hash)
            db.finalize_extraction(
                conn, rec.id,
                mood=mood, emotions=emotions, entities=[], themes=themes,
                events=[], stated_goals=[], behaviors=[], decisions=[],
                open_loops=[], self_judgments=[], summary=summary,
                extracted_at=rec.created_at, source_hash=source_hash,
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
            log.warning("skipped embedding Yusuf's summaries (entries still saved): %s", exc)

    return count, by_theme


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Reset the vault to the Yusuf demo persona (DEV-FIXTURE).")
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
            log.info("backed up current vault data -> %s", backup)
        else:
            log.info("nothing to back up (empty vault)")

    _wipe(args.no_embed)

    count, by_theme = _write_entries(args.no_embed)
    moods = [d[2] for d in YUSUF_DAYS if d[2] is not None]
    log.info(
        "wrote %d Yusuf journal(s) across the past ~2 months (mood %d..%d, %d blank day)",
        count, min(moods), max(moods), len(YUSUF_DAYS) - len(moods),
    )

    prof = profile.Profile.from_dict(build_yusuf_profile(by_theme))
    conn = db.get_or_create_db()
    try:
        prof = operations.apply_typical_mood(prof, db.mood_history(conn))
    finally:
        conn.close()
    saved = profile.save_profile(prof)
    log.info(
        "wrote Yusuf's profile: %d goal(s), %d pattern(s), %d relationship(s), typical_mood %s",
        len(saved.goals), len(saved.patterns), len(saved.relationships),
        saved.emotional_baseline.get("typical_mood"),
    )

    conn = db.get_or_create_db()
    try:
        n_nodes, n_edges = graph.store_graph(conn)
    finally:
        conn.close()
    log.info("built Yusuf's connections graph: %d node(s), %d edge(s)", n_nodes, n_edges)

    print(
        "\nDone. The vault is now Yusuf: ~2 months of real, backdated journal entries "
        "(browsable + editable + recall-able) and his profile. Insights derive from "
        "these as real data — no demo toggle needed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
