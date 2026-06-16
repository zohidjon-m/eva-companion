# Eva — Demo Script (one page)

**Before you start:** `scripts/demo_reset.py --yes` (READY), then `scripts/demo_drills.py`
(all PASS). Launch with `./run_demo.sh`. Voice on, window at a comfortable size.
Each beat below has a **Fallback** for when the live machine misbehaves — use it
without apology and keep moving. Last resort for any beat: a pre-recorded clip.

| # | Beat | Do / say | You should see | Fallback |
|---|------|----------|----------------|----------|
| 1 | **Open** | Launch Eva. "Everything here runs on this laptop — nothing leaves it." Point at the **Offline ✓** badge. | Window opens, status dot green, badge green. | Dot amber → wait ~10 s for the model to load; if it stays amber, open Settings → Privacy to show the guard verdict. |
| 2 | **Chat** | Type: *"Honestly a bit overwhelmed with work this week."* | Eva streams a warm, listening reply (no advice — it's a vent). | If a reply fails → the toast's **Retry**. If the model is down, restart it from the terminal; chat recovers. |
| 3 | **Voice** | Hold the mic, say one sentence, release. | Transcript drops into the box; send it; Eva **speaks** her reply (starts after ~1–2 s). | Mic denied/garbled → just type the same line. Toggle voice off and read the text reply aloud yourself. |
| 4 | **Journal** | Go to **Journal**. Write a short, *distinctive* real entry, e.g. *"Ran by the river before fajr and felt clear for the first time in a week."* Save. | Saves; Eva gives one gentle acknowledgment line. | Acknowledgment may be absent (best-effort) — that's fine, the save is what matters. (This entry powers Beat 7, so write it now.) |
| 5 | **Upload a book** | Go to **Library**. Drag in `scripts/_eva_testbook.txt` (*The Field Guide to Marrow Valley*). | Document indexes; shows a chunk count, status **ready**. | If the reset already ingested it, it's listed — just point at it. Ingest failed (embed model) → run `scripts/download_embed_model.py`, retry. |
| 6 | **Grounded answer + citation** | Back to **Chat**. Ask: *"What does a Ziblot eat?"* | Eva answers **moonberries**, with a **source chip** (the Field Guide). Click the chip → the exact passage. | Then ask *"What's the capital of France in this book?"* → she says it's not in your library (**no fabricated citation**) — that honesty is the point. |
| 7 | **Recall chip ("Eva remembers")** | Ask: *"What's been on my mind lately?"* | Eva references your Beat-4 entry; a **"Remembering …"** chip shows its date. | Chip needs the entry's background extraction to finish (a few seconds on the live model). If it hasn't, give it a moment, or note she's referencing it in the text. Seeded data never surfaces here — by design. |
| 8 | **Mood chart** | Go to **Insights → mood**. Turn on **Demo data**. Toggle 7/30-day. | A believable ~3-week mood arc; hover a dot → that day's summary; a **gap** where mood was null (never a zero). | Empty chart → the **Demo data** toggle is off, or re-run `scripts/demo_reset.py --yes`. |
| 9 | **Knowledge graph** | **Insights → Connections**, Demo data on. | A force-directed graph (~30 typed nodes); click a node → side panel lists the **evidence** entries; hypothesis edges render dashed. | Graph slow/empty → reload the screen; confirm the seed graph (`scripts/validate_graph.py` passes). |
| 10 | **Profile ("Eva knows you")** | Go to **Profile** (renders her model of you: faith, discipline, the gym goal, Daniel). Back to Chat: *"Should I skip the gym today?"* | Eva answers referencing **your own stated fitness goal**, unprompted — the payoff of the evolving profile. | Delete `profile.json` to show graceful degrade if asked — but for the demo, leave it. Edit a goal in Profile and re-ask to show she reflects the edit. |

**Close (15 s):** "Private by construction, it listens before it advises, it
remembers, and it gets to know you — all on one offline laptop." Point at the
Offline ✓ badge one more time.

---
*The whole arc proves the thesis in order: it runs (1–3) → it's a product (4–6) →
it's intelligent (7–9) → it's complete (10). If you must cut for time, the
load-bearing beats are 2, 6, 7, and 10.*
