#!/usr/bin/env python3
"""Pre-Phase-2 test harness for backend/prompts/extract_entry.md.

Runs a set of realistic journal entries through the REAL gemma-4-E2B-it-qat model
(via llama-cpp-python, the same engine eva_1 uses) and checks that the model's
output is (a) parseable as JSON and (b) conformant to the L1 `extractions` schema
from EVA_MEMORY_ARCHITECTURE.md §7.1.

This is a throwaway verification tool for the Phase-2 pre-step, NOT Phase-2 code.
It exists to confirm the prompt yields consistently parseable JSON before any
extraction pipeline is built on top of it.

Usage:
    python scripts/test_extract_prompt.py                 # prompt-only, temp 0.3
    python scripts/test_extract_prompt.py --temp 1.0      # chat-default temp
    python scripts/test_extract_prompt.py --json-mode     # constrained JSON grammar
    python scripts/test_extract_prompt.py --n 5           # first N entries only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Resolve paths relative to the repo, but allow the model to live in the sibling
# eva_1 checkout (that is where the only local GGUF currently is).
REPO = Path(__file__).resolve().parents[1]
PROMPT_PATH = REPO / "backend" / "prompts" / "extract_entry.md"
MODEL_CANDIDATES = [
    REPO / "models" / "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf",
    Path.home() / "eva_1" / "models" / "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf",
]

# ── 10 realistic journal entries, deliberately varied ────────────────────────
ENTRIES = [
    # 1 — anxiety, anticipation, prep behavior, an open loop (the interview)
    "Can't sleep. The interview at Meridian is at 9am and my brain won't shut up. "
    "I went through the system design questions twice and re-read my notes on their "
    "product but I still feel like I'm going to freeze the second they ask me "
    "something I don't know. I want to come across as calm and curious, not desperate. "
    "Whatever happens I just need to get through tomorrow.",

    # 2 — pure venting, irritation, no goals/decisions
    "I am so done with Kevin leaving his crusty pans in the sink. Three days now. "
    "I'm not his mother. I shouldn't have to leave a passive-aggressive note but here "
    "we are. Honestly it's such a small thing and yet it makes me irrationally angry "
    "every single time I walk into that kitchen.",

    # 3 — positive, gratitude, a recurring decision
    "Such a good day. Sam and I drove up to Eagle Ridge and did the full loop, maybe "
    "eight miles, and the light coming through the pines near the top was unreal. We "
    "barely talked, just walked, and it was exactly what I needed. We decided we're "
    "going to do one real hike every month this year. I feel lucky to have someone who "
    "recharges me instead of draining me.",

    # 4 — terse, low emotional signal (tests near-neutral / null mood + empty arrays)
    "Tired. Didn't really do much today. Watched some TV. Meh.",

    # 5 — grief, loneliness, anniversary, a self-judgment
    "It's three years today since Grandma Rose passed. I didn't call Mom and I feel "
    "bad about that — I think I was avoiding it because I knew we'd both cry. I made "
    "her lemon cake from the recipe card she wrote out for me, and the kitchen smelled "
    "exactly like her house. I miss her more than I expected to this year, not less.",

    # 6 — fresh resolution framed as identity (new stated goal)
    "I think I've figured something out. I keep saying I'm a writer but I haven't "
    "written anything real in months. Starting tomorrow I'm going to write for thirty "
    "minutes every morning before I touch my phone, no exceptions. Not to publish, just "
    "to become the kind of person who actually does the thing instead of just "
    "identifying with it.",

    # 7 — relationship conflict, unresolved (open loop), mixed emotion
    "Priya and I had that argument again about money and it ended the same way — her "
    "going quiet and me pretending it's fine. It isn't fine. I hate that we keep "
    "circling the same drain and neither of us says the real thing. I love her and I "
    "still went to bed angry. I don't know how to bring it up tomorrow without it "
    "blowing up again.",

    # 8 — mixed valence: pride + guilt, work project going well
    "Finished my first 10k this morning, actually ran the whole thing without walking, "
    "and I'm genuinely proud of that. Then I undid it by inhaling half a pizza and a "
    "tub of ice cream tonight because I 'earned it.' The Aurora launch at work is "
    "finally on track though — we shipped the beta and the early numbers look strong. "
    "Weird day. Up and down.",

    # 9 — mundane logistics, to-do flavored (tests null/neutral mood, place entities)
    "Errands. Dropped the car at Pep Boys for the brakes, dentist at 2 (no cavities), "
    "picked up groceries at Safeway and finally returned that package. Need to email "
    "the landlord about the lease renewal before Friday.",

    # 10 — long, rambling, many themes (stress test for breadth + summary)
    "Heavy night, lots on my mind. The money thing is back — rent went up again and "
    "I ran the numbers and we're basically breaking even every month with nothing left "
    "over, which terrifies me when I think about Dad's hospital bills coming. He sounded "
    "weaker on the phone today and Mom won't admit how bad it's getting. I prayed for "
    "the first time in a while, which surprised me; I don't even know if I believe but "
    "it felt like something to do with the fear. I did go for a run to clear my head and "
    "that helped a little. I want to be the person my family can lean on, the steady one, "
    "but right now I just feel like I'm holding water in my hands. I keep telling myself "
    "to make a real budget and I keep not doing it.",
]

REQUIRED_KEYS = [
    "mood", "emotions", "entities", "themes", "events", "stated_goals",
    "behaviors", "decisions", "open_loops", "self_judgments", "summary",
]
ENTITY_TYPES = {"person", "place", "project"}
LOOP_STATUS = {"open", "updated", "resolved"}


def extract_json(text: str):
    """Pull the first balanced {...} object out of the model's raw text.

    Tolerant of accidental code fences or stray prose so we can measure how close
    the raw output is, even when it is not perfectly clean.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("no '{' in output")
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
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("unbalanced braces")


def validate_schema(obj) -> list[str]:
    """Return a list of schema problems; empty list means fully conformant."""
    problems: list[str] = []
    if not isinstance(obj, dict):
        return ["top-level value is not an object"]

    for k in REQUIRED_KEYS:
        if k not in obj:
            problems.append(f"missing key: {k}")
    extra = [k for k in obj if k not in REQUIRED_KEYS]
    if extra:
        problems.append(f"unexpected keys: {extra}")

    mood = obj.get("mood", "MISSING")
    if mood is not None and mood != "MISSING":
        if not isinstance(mood, int) or isinstance(mood, bool) or not (-5 <= mood <= 5):
            problems.append(f"mood not int in -5..5: {mood!r}")

    for em in obj.get("emotions") or []:
        if not (isinstance(em, dict) and "name" in em and "intensity" in em):
            problems.append(f"emotion malformed: {em!r}")
            continue
        if not isinstance(em["intensity"], (int, float)) or not (0.0 <= em["intensity"] <= 1.0):
            problems.append(f"emotion intensity out of 0..1: {em!r}")

    for ent in obj.get("entities") or []:
        if not (isinstance(ent, dict) and {"name", "type", "normalized"} <= set(ent)):
            problems.append(f"entity malformed: {ent!r}")
            continue
        if ent["type"] not in ENTITY_TYPES:
            problems.append(f"entity type not in {ENTITY_TYPES}: {ent['type']!r}")

    for g in obj.get("stated_goals") or []:
        if not (isinstance(g, dict) and "text" in g and "is_new" in g):
            problems.append(f"stated_goal malformed: {g!r}")
        elif not isinstance(g["is_new"], bool):
            problems.append(f"stated_goal.is_new not bool: {g!r}")

    for lp in obj.get("open_loops") or []:
        if not (isinstance(lp, dict) and "description" in lp and "status" in lp):
            problems.append(f"open_loop malformed: {lp!r}")
        elif lp["status"] not in LOOP_STATUS:
            problems.append(f"open_loop.status not in {LOOP_STATUS}: {lp['status']!r}")

    for key in ("themes", "events", "behaviors", "decisions", "self_judgments"):
        v = obj.get(key)
        if v is not None and (not isinstance(v, list) or any(not isinstance(x, str) for x in v)):
            problems.append(f"{key} not a list[str]: {v!r}")

    summ = obj.get("summary")
    if summ is not None and (not isinstance(summ, str) or len(summ.strip()) < 20):
        problems.append(f"summary missing/too short: {summ!r}")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--temp", type=float, default=0.3)
    ap.add_argument("--json-mode", action="store_true",
                    help="constrain decoding to valid JSON via response_format")
    ap.add_argument("--n", type=int, default=len(ENTRIES))
    ap.add_argument("--max-tokens", type=int, default=900)
    args = ap.parse_args()

    model_path = next((p for p in MODEL_CANDIDATES if p.exists()), None)
    if model_path is None:
        print("ERROR: no GGUF found. Looked in:", *MODEL_CANDIDATES, sep="\n  ")
        return 2

    prompt_template = PROMPT_PATH.read_text()
    entries = ENTRIES[: args.n]

    from llama_cpp import Llama

    print(f"Loading model: {model_path}")
    t0 = time.time()
    llm = Llama(
        model_path=str(model_path),
        n_ctx=8192,
        n_gpu_layers=-1,          # full Metal offload on Apple Silicon
        chat_format="gemma",      # Gemma has no system role; fold instructions into user turn
        verbose=False,
    )
    print(f"Loaded in {time.time() - t0:.1f}s | temp={args.temp} json_mode={args.json_mode}\n")

    response_format = {"type": "json_object"} if args.json_mode else None
    report_lines: list[str] = []
    n_parse_ok = n_schema_ok = 0

    for i, entry in enumerate(entries, 1):
        prompt = prompt_template.replace("{{ENTRY_TEXT}}", entry.strip())
        t = time.time()
        out = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=args.max_tokens,
            temperature=args.temp,
            stop=["<end_of_turn>", "<eos>"],
            response_format=response_format,
        )
        raw = out["choices"][0]["message"]["content"]
        dt = time.time() - t

        status = ok_parse = ok_schema = None
        problems: list[str] = []
        try:
            obj = extract_json(raw)
            ok_parse = True
            n_parse_ok += 1
            problems = validate_schema(obj)
            ok_schema = not problems
            if ok_schema:
                n_schema_ok += 1
            status = "OK" if ok_schema else "PARSED-BUT-SCHEMA"
        except Exception as e:  # noqa: BLE001
            ok_parse = False
            status = f"PARSE-FAIL: {e}"

        flag = "✓" if ok_schema else ("~" if ok_parse else "✗")
        print(f"[{i:2}/{len(entries)}] {flag} {status}  ({dt:.1f}s, {len(raw)} chars)")
        if problems:
            for p in problems:
                print(f"        - {p}")

        report_lines.append(f"### Entry {i} — {status} ({dt:.1f}s)\n")
        report_lines.append("ENTRY:\n" + entry.strip() + "\n")
        report_lines.append("RAW OUTPUT:\n" + raw.strip() + "\n")
        if problems:
            report_lines.append("SCHEMA PROBLEMS:\n" + "\n".join(f"- {p}" for p in problems) + "\n")
        report_lines.append("\n---\n")

    n = len(entries)
    print("\n" + "=" * 60)
    print(f"JSON-parseable : {n_parse_ok}/{n}")
    print(f"Schema-valid   : {n_schema_ok}/{n}")
    print("=" * 60)

    tag = f"temp{args.temp}{'_json' if args.json_mode else ''}"
    report_path = REPO / "scripts" / f"extract_prompt_report_{tag}.md"
    header = (f"# extract_entry.md test report\n\n"
              f"model: {model_path.name}\ntemp: {args.temp}\njson_mode: {args.json_mode}\n"
              f"parseable: {n_parse_ok}/{n} | schema-valid: {n_schema_ok}/{n}\n\n---\n\n")
    report_path.write_text(header + "\n".join(report_lines))
    print(f"\nFull report: {report_path}")
    return 0 if n_schema_ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
