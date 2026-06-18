/**
 * chat/api.ts — REST calls for the chat *history* (the live turn-by-turn stream
 * is the WebSocket in useChat; this is just the sidebar's read path).
 *
 * Conversations are persisted server-side (both the user's and Eva's turns), so
 * the Chat screen can list past conversations and reopen one. All loopback, no
 * network beyond this machine.
 */

const BASE = "http://127.0.0.1:8000";

/** One row in the conversation history rail. */
export type ConversationSummary = {
  id: string;
  title: string | null;
  started_at: string;
  last_at: string;
  turn_count: number;
};

/** One stored turn of a reopened conversation. */
export type ConversationTurn = {
  role: "user" | "eva";
  text: string;
  created_at: string;
};

export type Conversation = {
  id: string;
  title: string | null;
  started_at: string;
  last_at: string;
  turns: ConversationTurn[];
};

/** List conversations, most recently active first. Empty list on any failure. */
export async function fetchConversations(): Promise<ConversationSummary[]> {
  const res = await fetch(`${BASE}/chat/conversations`);
  if (!res.ok) throw new Error(`GET /chat/conversations ${res.status}`);
  const data = (await res.json()) as { conversations: ConversationSummary[] };
  return data.conversations ?? [];
}

/** Fetch one conversation's full transcript, or null if it no longer exists. */
export async function fetchConversation(id: string): Promise<Conversation | null> {
  const res = await fetch(`${BASE}/chat/conversation/${encodeURIComponent(id)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GET /chat/conversation ${res.status}`);
  return (await res.json()) as Conversation;
}

/** Delete a conversation (and its turns). Returns whether it existed. */
export async function deleteConversation(id: string): Promise<boolean> {
  const res = await fetch(`${BASE}/chat/conversation/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`DELETE /chat/conversation ${res.status}`);
  const data = (await res.json()) as { deleted: boolean };
  return data.deleted;
}
