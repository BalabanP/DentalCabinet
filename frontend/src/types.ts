// OpenAI chat-format messages: the chat server is stateless, so the browser keeps them and sends them back.
export type ToolCall = {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
};

export type ChatMessage = {
  role: "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
};

// Server-Sent Events from POST /chat
export type ServerEvent =
  | { type: "text"; delta: string }
  | { type: "tool_start"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; ok: boolean; declined?: boolean }
  | { type: "confirm"; id: string; name: string; summary: string; args: Record<string, unknown> }
  | { type: "done"; messages: ChatMessage[]; pending: boolean }
  | { type: "error"; message: string };

export type Health = { ok: boolean; model: string; api_key_set: boolean; patient_notes: boolean };

// What the screen shows (separate from the model-facing history)
export type Item =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string }
  | { kind: "tool"; id: string; name: string; status: "running" | "ok" | "error" }
  | {
      kind: "confirm";
      id: string;
      name: string;
      summary: string;
      args: Record<string, unknown>;
      status: "waiting" | "approved" | "rejected" | "ok" | "error" | "declined";
    }
  | { kind: "error"; id: string; text: string };
