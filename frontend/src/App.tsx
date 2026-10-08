import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import { HttpError, getHealth, streamChat } from "./api";
import type { ChatMessage, Health, Item } from "./types";

const SUGGESTIONS = [
  "Who is booked in the next days?",
  "Find me a free 30-minute slot next Monday",
  "Search for visitors named Pop",
  "Show the notes of Ana Pop",
];

const TOOL_LABELS: Record<string, string> = {
  search_visitors: "Searching visitors",
  get_visitor: "Reading visitor",
  get_patient_notes: "Reading patient notes",
  list_appointments: "Listing appointments",
  get_appointment: "Reading appointment",
  find_free_slots: "Checking free slots",
  get_appointment_notifications: "Checking delivery log",
  get_reminder_settings: "Reading reminder settings",
  get_working_hours: "Reading working hours",
};

const WRITE_LABELS: Record<string, string> = {
  create_visitor: "Create visitor",
  update_visitor: "Update visitor",
  delete_visitor: "Delete visitor",
  set_patient_notes: "Replace patient notes",
  book_appointment: "Book appointment",
  update_appointment: "Change appointment",
  cancel_appointment: "Cancel appointment",
};

let counter = 0;
const newId = () => `i${++counter}`;

export default function App({ onSignOut }: { onSignOut?: () => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [health, setHealth] = useState<Health | null | undefined>(undefined);

  // Model-facing conversation. Lives in refs: it changes without needing a re-render.
  const history = useRef<ChatMessage[]>([]);
  const pending = useRef<string[]>([]);
  const decided = useRef<Record<string, boolean>>({});
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getHealth().then(setHealth);
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  const run = useCallback(async (messages: ChatMessage[], decisions?: Record<string, boolean>) => {
    setBusy(true);
    const controller = new AbortController();
    abort.current = controller;
    pending.current = [];
    let current: string | null = null; // id of the assistant bubble currently streaming

    try {
      for await (const ev of streamChat({ messages, decisions }, controller.signal)) {
        switch (ev.type) {
          case "text": {
            if (current === null) {
              const id = newId();
              current = id;
              setItems((p) => [...p, { kind: "assistant", id, text: ev.delta }]);
            } else {
              const id = current;
              setItems((p) =>
                p.map((i): Item => (i.kind === "assistant" && i.id === id ? { ...i, text: i.text + ev.delta } : i)),
              );
            }
            break;
          }
          case "tool_start": {
            // Text written before a tool call is a guess made before looking things up: hide it.
            const stale = current;
            current = null;
            setItems((p) => [
              ...p.filter((i) => !(stale !== null && i.kind === "assistant" && i.id === stale)),
              { kind: "tool", id: ev.id, name: ev.name, status: "running" },
            ]);
            break;
          }
          case "tool_result":
            current = null;
            setItems((p) =>
              p.map((i): Item => {
                if (i.id !== ev.id) return i;
                if (i.kind === "tool") return { ...i, status: ev.ok ? "ok" : "error" };
                if (i.kind === "confirm") return { ...i, status: ev.declined ? "declined" : ev.ok ? "ok" : "error" };
                return i;
              }),
            );
            break;
          case "confirm": {
            const stale = current;
            current = null;
            pending.current.push(ev.id);
            setItems((p) => [
              ...p.filter((i) => !(stale !== null && i.kind === "assistant" && i.id === stale)),
              { kind: "confirm", id: ev.id, name: ev.name, summary: ev.summary, args: ev.args, status: "waiting" },
            ]);
            break;
          }
          case "done":
            history.current = [...messages, ...ev.messages];
            break;
          case "error":
            pending.current = [];
            setItems((p) => [...p, { kind: "error", id: newId(), text: ev.message }]);
            break;
        }
      }
    } catch (err) {
      if (err instanceof HttpError && err.status === 401 && onSignOut) {
        onSignOut(); // session expired: back to the sign-in page
      } else if (!controller.signal.aborted) {
        const text = err instanceof Error ? err.message : "unknown error";
        setItems((p) => [...p, { kind: "error", id: newId(), text: `Could not reach the chat server (${text}).` }]);
      }
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }, [onSignOut]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;
      setInput("");
      setItems((p) => [...p, { kind: "user", id: newId(), text: trimmed }]);
      history.current = [...history.current, { role: "user", content: trimmed }];
      void run(history.current);
    },
    [busy, run],
  );

  const decide = useCallback(
    (id: string, approve: boolean) => {
      decided.current[id] = approve;
      setItems((p) =>
        p.map((i): Item => (i.kind === "confirm" && i.id === id ? { ...i, status: approve ? "approved" : "rejected" } : i)),
      );
      if (pending.current.every((pid) => pid in decided.current)) {
        const decisions = { ...decided.current };
        decided.current = {};
        void run(history.current, decisions);
      }
    },
    [run],
  );

  const reset = () => {
    abort.current?.abort();
    history.current = [];
    pending.current = [];
    decided.current = {};
    setItems([]);
    setBusy(false);
  };

  const waiting = items.some((i) => i.kind === "confirm" && i.status === "waiting");
  const locked = busy || waiting;

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  return (
    <div className="app">
      <header>
        <div>
          <h1>Cabinet Assistant</h1>
          <p className="sub">Visitors, appointments and notes. Every change asks for your approval.</p>
        </div>
        <div className="header-actions">
          <button className="ghost" onClick={reset} disabled={items.length === 0}>
            New chat
          </button>
          {onSignOut && (
            <button className="ghost" onClick={onSignOut}>
              Sign out
            </button>
          )}
        </div>
      </header>

      {health === null && (
        <div className="banner warn">The chat server is not reachable. Start it on port 8100 (see README).</div>
      )}
      {health && !health.api_key_set && (
        <div className="banner warn">ANTHROPIC_API_KEY is not set in chat-server/.env, so the model cannot answer yet.</div>
      )}

      <main>
        {items.length === 0 && (
          <div className="empty">
            <p>Ask in plain language. Try:</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)} disabled={locked}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {items.map((item) => {
          switch (item.kind) {
            case "user":
              return (
                <div key={item.id} className="row user">
                  <div className="bubble">{item.text}</div>
                </div>
              );
            case "assistant":
              return (
                <div key={item.id} className="row">
                  <div className="bubble assistant">
                    <ReactMarkdown>{item.text}</ReactMarkdown>
                  </div>
                </div>
              );
            case "tool":
              return (
                <div key={item.id} className={`tool ${item.status}`}>
                  {item.status === "running" ? "⏳" : item.status === "ok" ? "✓" : "⚠"}{" "}
                  {TOOL_LABELS[item.name] ?? item.name}
                  {item.status === "running" ? "…" : ""}
                </div>
              );
            case "confirm":
              return (
                <div key={item.id} className={`card ${item.status}`}>
                  <div className="card-title">{WRITE_LABELS[item.name] ?? item.name}</div>
                  <div className="card-summary">{item.summary}</div>
                  <details>
                    <summary>Details</summary>
                    <pre>{JSON.stringify(item.args, null, 2)}</pre>
                  </details>
                  {item.status === "waiting" ? (
                    <div className="actions">
                      <button className="primary" onClick={() => decide(item.id, true)}>
                        Approve
                      </button>
                      <button className="ghost" onClick={() => decide(item.id, false)}>
                        Reject
                      </button>
                    </div>
                  ) : (
                    <div className="status">
                      {{
                        approved: "Approved, applying…",
                        rejected: "Rejected",
                        ok: "Done ✓",
                        error: "Failed: the assistant will explain",
                        declined: "Rejected",
                        waiting: "",
                      }[item.status]}
                    </div>
                  )}
                </div>
              );
            case "error":
              return (
                <div key={item.id} className="banner warn">
                  {item.text}
                </div>
              );
          }
        })}
        {busy && <div className="typing">Working…</div>}
        <div ref={bottom} />
      </main>

      <footer>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder={waiting ? "Approve or reject the change above first" : "Message the assistant… (Enter to send)"}
          rows={2}
          disabled={locked}
        />
        {busy ? (
          <button className="ghost" onClick={() => abort.current?.abort()}>
            Stop
          </button>
        ) : (
          <button className="primary" onClick={() => send(input)} disabled={locked || !input.trim()}>
            Send
          </button>
        )}
      </footer>
    </div>
  );
}
