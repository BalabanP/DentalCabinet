import { getIdToken } from "./auth";
import { AUTH_ENABLED, CHAT_URL, HEALTH_URL } from "./config";
import type { ChatMessage, Health, ServerEvent } from "./types";

export class HttpError extends Error {
  constructor(public status: number) {
    super(`the server returned ${status}`);
  }
}

async function authHeaders(): Promise<Record<string, string>> {
  if (!AUTH_ENABLED) return {};
  const token = await getIdToken();
  if (!token) throw new HttpError(401);
  return { Authorization: `Bearer ${token}` };
}

export async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(HEALTH_URL, { headers: await authHeaders() });
    return res.ok ? ((await res.json()) as Health) : null;
  } catch {
    return null;
  }
}

/**
 * POST the chat request and yield the Server-Sent Events (EventSource cannot POST).
 * Locally the events arrive as they are produced; on AWS the whole answer arrives at once.
 */
export async function* streamChat(
  body: { messages: ChatMessage[]; decisions?: Record<string, boolean> },
  signal: AbortSignal,
): AsyncGenerator<ServerEvent> {
  const res = await fetch(CHAT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new HttpError(res.status);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) yield JSON.parse(line.slice(6)) as ServerEvent;
      }
    }
  }
}
