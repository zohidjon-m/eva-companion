"""Eva's voice layer.

Group B of the component architecture (EVA_SYSTEM_DESIGN §5): speech-to-text
(:mod:`voice.stt`, Phase 8) and text-to-speech + the streaming sentence queue
(:mod:`voice.tts` and :mod:`voice.sentence_queue`, Phase 9).

Both voice models are **lazy-loaded on first use, never at backend startup**
(CLAUDE.md; §4 memory budget): on an 8 GB M1 Air, loading faster-whisper and
Kokoro alongside the model server at boot would exhaust RAM. The first ``/stt``
request loads faster-whisper; the first voiced chat turn loads Kokoro; each then
stays resident.
"""
