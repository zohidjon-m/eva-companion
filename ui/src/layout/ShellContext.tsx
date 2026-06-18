import { createContext, useContext } from "react";

/**
 * ShellContext — the sliver of shell state that a section needs to read but that
 * the shell owns. Right now it's just whether the chat history rail is open.
 *
 * Sections are rendered through a registry (`SECTIONS`) with no props, so a
 * context is the lightest way to let ChatScreen react to the sidebar's "press
 * Chat again to tuck the past-chats rail away" gesture without threading a prop
 * through the registry's call signature.
 */

export type ShellState = {
  /** Whether the chat history (past conversations) rail is shown. */
  chatRailOpen: boolean;
};

const ShellContext = createContext<ShellState>({ chatRailOpen: true });

export const ShellProvider = ShellContext.Provider;

/** Read the shell state from inside a section. */
export function useShell(): ShellState {
  return useContext(ShellContext);
}
