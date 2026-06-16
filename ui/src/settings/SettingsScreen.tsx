import { Icon } from "../components";
import { useSettings } from "./useSettings";

/**
 * SettingsScreen — Phase 8 brings the first real, wired control to Settings: the
 * Whisper (speech-to-text) model size. The rest of the store lands in Phase 10;
 * this screen shows the one knob this phase needs end-to-end and an honest note
 * about what's still coming, rather than a dead empty state.
 *
 * The dropdown reads its choices from the backend (`options`) and writes through
 * `PATCH /settings`. The backend's stt.py picks up the new size on the next
 * transcription, so switching here changes the model used for the very next
 * recording — no restart.
 */

/** A friendly label + one-line rationale for each whisper size. */
const SIZE_INFO: Record<string, { label: string; note: string }> = {
  "base.en": {
    label: "Base (English) — faster",
    note: "The default. Small and quick; accurate for clear English speech.",
  },
  "small.en": {
    label: "Small (English) — more accurate",
    note: "Slower and a little heavier, but better on strong accents or noisy rooms.",
  },
};

export function SettingsScreen() {
  const { settings, options, loading, error, saving, savedTick, setWhisperSize } =
    useSettings();

  const sizes = options?.whisper_model_size ?? [];
  const current = settings?.whisper_model_size ?? "base.en";
  const info = SIZE_INFO[current];

  return (
    <div className="settings">
      <section className="settings__group">
        <div className="settings__group-head">
          <span className="settings__group-icon" aria-hidden="true">
            <Icon name="mic" size={18} />
          </span>
          <div>
            <h2 className="settings__group-title">Voice</h2>
            <p className="settings__group-sub">
              Speak instead of typing — hold the mic in Chat or Journal. Everything
              is transcribed on this device.
            </p>
          </div>
        </div>

        <div className="settings__row">
          <div className="settings__row-label">
            <label htmlFor="whisper-size" className="settings__label">
              Speech recognition model
            </label>
            <p className="settings__hint">
              {loading
                ? "Loading…"
                : info?.note ?? "Choose the model Eva uses to turn your voice into text."}
            </p>
          </div>

          <div className="settings__control">
            <div className="settings__select-wrap">
              <select
                id="whisper-size"
                className="settings__select"
                value={current}
                disabled={loading || saving || sizes.length === 0}
                onChange={(e) => setWhisperSize(e.target.value)}
              >
                {sizes.map((s) => (
                  <option key={s} value={s}>
                    {SIZE_INFO[s]?.label ?? s}
                  </option>
                ))}
              </select>
              <span className="settings__select-chevron" aria-hidden="true">
                <Icon name="chevron-down" size={16} />
              </span>
            </div>
            <span className="settings__status" role="status">
              {saving ? "Saving…" : savedTick > 0 ? "Saved" : ""}
            </span>
          </div>
        </div>

        {error && <p className="settings__error">{error}</p>}
      </section>

      <p className="settings__footnote">
        Vault location, voice output, appearance, and model status arrive in Phase 10.
      </p>
    </div>
  );
}
