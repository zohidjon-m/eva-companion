import { useCallback, useEffect, useState } from "react";
import {
  fetchSettings,
  patchSettings,
  type Settings,
  type SettingsOptions,
} from "./api";

/**
 * useSettings — load the settings store and apply changes, kept here so the
 * Settings screen stays presentational.
 *
 * Phase 8 surfaces a single knob (the whisper STT model size). Saving is
 * optimistic-with-rollback: the dropdown updates immediately, the PATCH is sent,
 * and the value reverts if the backend rejects it — so the UI never drifts from
 * what's actually persisted. A `savedTick` bumps on each successful write so the
 * screen can flash a brief "Saved" confirmation.
 */

export type UseSettings = {
  settings: Settings | null;
  options: SettingsOptions | null;
  loading: boolean;
  error: string | null;
  saving: boolean;
  /** Increments on each successful save (drives the "Saved" flash). */
  savedTick: number;
  setWhisperSize: (size: string) => void;
};

export function useSettings(): UseSettings {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [options, setOptions] = useState<SettingsOptions | null>(null);
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
      })
      .catch(() => alive && setError("Couldn't load settings. Is the backend running?"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const setWhisperSize = useCallback(
    async (size: string) => {
      if (!settings || size === settings.whisper_model_size) return;
      const prev = settings;
      setSettings({ ...settings, whisper_model_size: size }); // optimistic
      setSaving(true);
      setError(null);
      try {
        const r = await patchSettings({ whisper_model_size: size });
        setSettings(r.settings);
        setOptions(r.options);
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

  return { settings, options, loading, error, saving, savedTick, setWhisperSize };
}
