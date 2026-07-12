"""Eval harness CLI.

Slice 1 exposes one command — the context-window inspector:

    python -m eval.run --inspect "I snapped at my mother again tonight"

It runs the real pipeline over the message and prints, for that turn: the chosen
intent and its stratum (rule = deterministic, model = the ambiguous residue), the
listen-first gate state, every retrieved item with its distance and whether it was
kept or dropped, the per-slot token cost, and the verbatim assembled system prompt.
That answers, for any input, *where Eva took the info from and what reached the
window* — with no model call unless the intent is ambiguous.

Flags:
    --mode friend|coach|mentor   persona mode (default friend)
    --intent LABEL               inject a fixed intent (deterministic; skips the classifier)
    --model                      also stream a reply (needs a configured provider)
    --show-prompt / --no-prompt  include/omit the verbatim window (default: show)
    --json                       emit the raw Trace as JSON instead of the report
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from eval.trace import SlotTrace, Trace, trace_turn

# ANSI dimming, disabled when stdout isn't a TTY so piped/redirected output is clean.
_TTY = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def _dim(text: str) -> str:
    return _c(text, "2")


def _bold(text: str) -> str:
    return _c(text, "1")


def _render_slot(slot: SlotTrace) -> list[str]:
    """One slot's block: header + kept/dropped candidate lines."""
    dropped = [c for c in slot.candidates if not c.kept]
    head = (
        f"  {_bold(slot.name):<20} "
        f"kept {len(slot.kept)}  ·  ~{slot.approx_tokens} tok  ·  {slot.rendered_chars} chars"
    )
    if slot.gated_off:
        head += _dim("   [gated off — listen-first]")
    lines = [head]

    for item in slot.kept:
        dist = f"d={item.distance:.3f}" if item.distance is not None else "    -    "
        lines.append(f"      {_c('[+]', '32')} {dist}  {item.label}")
    # Show candidates that were pulled but did NOT survive the threshold/top-k.
    for item in dropped:
        dist = f"d={item.distance:.3f}" if item.distance is not None else "    -    "
        lines.append(_dim(f"      [-] {dist}  {item.label}"))
    if not slot.kept and not dropped:
        lines.append(_dim("      (nothing)"))
    return lines


def _report(trace: Trace, *, show_prompt: bool) -> str:
    p = trace.provenance
    gate = "FIRED" if trace.corpus_gate_fired else "bypassed (listen-first)"
    method_note = {
        "rule": "deterministic",
        "model": "ambiguous → model fallback (stochastic stratum)",
        "forced": "injected",
    }.get(trace.intent_method, trace.intent_method)

    out: list[str] = []
    out.append("")
    out.append(_bold(f'  input   "{trace.input}"'))
    out.append(
        f"  intent  {_bold(trace.intent_label)}  "
        f"[{trace.intent_method} — {method_note}]     mode={trace.mode}"
    )
    out.append(f"  corpus gate  {gate}")
    out.append("")
    out.append(_bold("  CONTEXT WINDOW — what reached the model, and from where"))
    out.append("")
    for slot in trace.slots:
        out.extend(_render_slot(slot))
        out.append("")

    lat = "  ".join(f"{k}={v}" for k, v in trace.step_latency_ms.items())
    out.append(f"  ~{trace.total_prompt_tokens} prompt tokens (approx)   ·   {lat}")
    out.append(
        _dim(
            f"  provenance  vault={p.vault_hash}  profile={p.profile_snapshot_hash}  "
            f"chroma={p.chroma_store_hash}"
        )
    )
    out.append(
        _dim(
            "  thresholds  "
            + "  ".join(f"{k}={v}" for k, v in p.thresholds.items())
        )
    )

    if trace.reply is not None:
        out.append("")
        out.append(_bold("  REPLY"))
        out.append(f"  {trace.reply}")
        if trace.citations:
            out.append(_dim(f"  citations: {len(trace.citations)}"))

    if show_prompt:
        out.append("")
        out.append(_bold("  -- VERBATIM SYSTEM PROMPT " + "-" * 40))
        out.append(trace.system_prompt)
        out.append(_bold("  " + "-" * 66))

    out.append("")
    return "\n".join(out)


async def _inspect(args: argparse.Namespace) -> int:
    trace = await trace_turn(
        args.inspect,
        mode=args.mode,
        force_intent=args.intent,
        run_model=args.model,
    )
    if args.json:
        print(trace.model_dump_json(indent=2))
    else:
        print(_report(trace, show_prompt=args.show_prompt))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; the report uses a couple of non-latin1
    # glyphs (·, —). Force UTF-8 so output never dies on an encode error.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(prog="eval.run", description="Eva eval harness")
    parser.add_argument("--inspect", metavar="MESSAGE", help="trace one turn and print it")
    parser.add_argument("--mode", default="friend", choices=("friend", "coach", "mentor"))
    parser.add_argument("--intent", default=None, help="inject a fixed intent label")
    parser.add_argument("--model", action="store_true", help="also stream a reply")
    parser.add_argument(
        "--show-prompt",
        dest="show_prompt",
        action="store_true",
        default=True,
        help="include the verbatim system prompt (default)",
    )
    parser.add_argument(
        "--no-prompt", dest="show_prompt", action="store_false", help="omit the system prompt"
    )
    parser.add_argument("--json", action="store_true", help="emit the raw Trace as JSON")
    args = parser.parse_args(argv)

    if not args.inspect:
        parser.print_help()
        return 2
    return asyncio.run(_inspect(args))


if __name__ == "__main__":
    raise SystemExit(main())
