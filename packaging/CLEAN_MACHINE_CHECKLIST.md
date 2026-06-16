# Clean-machine packaging verification (Phase 15)

Verify the packaged Eva end-to-end on a **fresh macOS user account** (or a clean
machine) — the only honest test that the bundle carries everything it needs and
doesn't lean on something only your dev account has. Do this once before the demo.

> Why a clean account: your dev account has Rust, a Python venv, model caches, and
> environment variables the installed app must not depend on. A new account has
> none of that — it is the audience's machine, minus the audience.

## 0. Build the bundle (dev account)
```sh
packaging/build_macos.sh
```
Produces `.app` + `.dmg` under `ui/src-tauri/target/release/bundle/`. Requires
Rust (`cargo`) — the script stops with install instructions if it's missing.

## 1. First launch on the clean account
- [ ] Copy the `.dmg` to the clean account; install by dragging Eva to Applications.
- [ ] Launch Eva. Gatekeeper may warn on an unsigned build → right-click → Open
      (note: code-signing/notarization is a separate, deferred packaging step).
- [ ] The window opens; the status dot resolves (green when the backend answers).
- [ ] No model present yet → the **first-run setup screen** appears with copyable
      commands, not an error. (Don't paper over a crash here — if it crashes,
      packaging is incomplete; fix before the demo.)

## 2. First-run model + voice download (the one permitted network moment)
- [ ] Follow the setup screen to fetch the Gemma GGUF, the embedding model, and
      the voices. This is the **only** time the app is allowed online.
- [ ] After download, `/health` reports `model_present: true`; the dot goes green.
- [ ] (Faster path for a borrowed machine: pre-place `models/gemma-…gguf` and copy
      a warmed `<vault>/models/` cache so the demo needs no download at all.)

## 3. Seed the demo state
```sh
backend/.venv/bin/python scripts/demo_reset.py --yes
```
- [ ] Prints `READY` with ✓ for mood, graph, profile, and net guard.

## 4. Run the failure drills
```sh
backend/.venv/bin/python scripts/demo_drills.py
```
- [ ] All automated drills `PASS`. Verify the manual (✎) mic-denied row live by
      clicking **Deny** on the macOS mic prompt → calm message, typing still works.

## 5. Run the demo script — twice, cold
Follow **DEMO_SCRIPT.md** beat for beat, start to finish, **two full times**:
- [ ] **Run A — normal.** All 10 beats land; each beat's fallback is known.
- [ ] **Run B — Wi-Fi off.** Turn Wi-Fi **off** first. Every feature still works
      (this is the real offline proof). The Offline ✓ badge stays green; the
      privacy panel verdict reads "guard active".

## 6. One deliberate failure per category (prove the soft-fail on stage hardware)
- [ ] **Model down:** quit the model server mid-session → chat shows a graceful
      "couldn't reply" with Retry; recovers when it restarts.
- [ ] **Mic denied:** deny the mic → message + fall back to typing.
- [ ] **Wi-Fi off:** already covered by Run B.
- [ ] **Huge PDF:** drag a >50 MB file into Library → clear "over the 50 MB limit",
      no freeze.
- [ ] **Rapid-fire:** send several messages fast → each handled in order, no crash.

## Done when
You have completed two full clean-account demo runs (one Wi-Fi-off) and one
deliberate failure per category, **without improvising**. That is the Phase-15
"run the demo cold, twice in a row" bar. Then tag `demo-v1`.
