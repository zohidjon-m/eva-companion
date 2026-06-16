"""Eva's voice layer.

Group B of the component architecture (EVA_SYSTEM_DESIGN §5): speech-to-text
(Phase 8, this phase) and, later, text-to-speech + the sentence queue (Phase 9).

Both voice models are **lazy-loaded on first use, never at backend startup**
(CLAUDE.md; §4 memory budget): on an 8 GB M1 Air, loading faster-whisper and
Kokoro alongside the model server at boot would exhaust RAM. The first ``/stt``
request loads faster-whisper; it then stays resident.
"""
