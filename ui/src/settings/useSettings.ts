import { useCallback, useEffect, useState } from "react";
import {
  fetchSettings,
  patchSettings,
  type Settings,
  type SettingsOptions,
  type SettingsRanges,
} from "./api";

/**
 * useSettings — load the settings store and apply changes, kept here so the
 * Settings screen stays presentational.
 *
 * Phase 8 surfaced one knob (the whisper STT size); Phase 10 adds the voice
 * speed. Saving is optimistic-with-rollback: the control updates immediately,
 * the PATCH is sent, and the value reverts if the backend rejects it — so the UI
 * never drifts from what's actually persisted. A `savedTick` bumps on each
 * successful write so the screen can flash a brief "Saved" confirmation.
 *
 * Note: the voice on/off toggle is NOT owned here — it lives in VoiceContext (so
 * the top-bar toggle and the Settings toggle stay in sync), which persists it to
 * the same store. This hook owns the rest of the settings bundle.
 */

export type UseSettings = {
  settings: Settings | null;
  options: SettingsOptions | null;
  ranges: SettingsRanges | null;
  vaultPath: string | null;
  loading: boolean;
  error: string | null;
  saving: boolean;
  /** Increments on each successful save (drives the "Saved" flash). */
  savedTick: number;
  setWhisperSize: (size: string) => void;
  setVoiceSpeed: (speed: number) => void;
};

export function useSettings(): UseSettings {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [options, setOptions] = useState<SettingsOptions | null>(null);
  const [ranges, setRanges] = useState<SettingsRanges | null>(null);
  const [vaultPath, setVaultPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedTick, setSavedTick] = useState(0);

  useEffect(() => {
    let alive = true;
    fetchSettings()
      .then((r) => {
        if (!alive) return;
        setSettings(r.settings);
        setOptions(r.options);
        setRanges(r.ranges);
        setVaultPath(r.vault_path);
      })
      .catch(() => alive && setError("Couldn't load settings. Is the backend running?"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  // One optimistic-with-rollback writer shared by every control. The control
  // moves immediately; if the backend rejects the change the value snaps back.
  const apply = useCallback(
    async (patch: Partial<Settings>) => {
      if (!settings) return;
      const prev = settings;
      setSettings({ ...settings, ...patch }); // optimistic
      setSaving(true);
      setError(null);
      try {
        const r = await patchSettings(patch);
        setSettings(r.settings);
        setOptions(r.options);
        setRanges(r.ranges);
        setVaultPath(r.vault_path);
        setSavedTick((t) => t + 1);
      } catch {
        setSettings(prev); // rollback on failure
        setError("Couldn't save that change. Please try again.");
      } finally {
        setSaving(false);
      }
    },
    [settings],
  );

  const setWhisperSize = useCallback(
    (size: string) => {
      if (settings && size !== settings.whisper_model_size) apply({ whisper_model_size: size });
    },
    [settings, apply],
  );

  const setVoiceSpeed = useCallback(
    (speed: number) => {
      if (settings && speed !== settings.voice_speed) apply({ voice_speed: speed });
    },
    [settings, apply],
  );

  return {
    settings,
    options,
    ranges,
    vaultPath,
    loading,
    error,
    saving,
    savedTick,
    setWhisperSize,
    setVoiceSpeed,
  };
}
