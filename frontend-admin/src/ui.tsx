import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "./api";
import { useI18n } from "./i18n";
import type { Key } from "./i18n";
import type { Appointment } from "./types";

// ---------- notices (toasts) ----------

type Notice = { id: number; kind: "ok" | "error"; text: string };
const NoticeCtx = createContext<((kind: Notice["kind"], text: string) => void) | null>(null);

export function NoticeProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);
  const notify = useCallback((kind: Notice["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setNotices((n) => [...n, { id, kind, text }]);
    setTimeout(() => setNotices((n) => n.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  return (
    <NoticeCtx.Provider value={notify}>
      {children}
      <div className="toasts" aria-live="polite">
        {notices.map((n) => (
          <div key={n.id} className={`toast ${n.kind}`}>
            {n.text}
          </div>
        ))}
      </div>
    </NoticeCtx.Provider>
  );
}

export function useNotice() {
  const ctx = useContext(NoticeCtx);
  if (!ctx) throw new Error("useNotice outside NoticeProvider");
  return ctx;
}

/** Turn any thrown error into a message for the user. Never shows raw server internals. */
export function useErrorText() {
  const { t } = useI18n();
  return useCallback(
    (err: unknown): string => {
      if (err instanceof ApiError) {
        if (err.status === 0) return t("err.network");
        if (err.status === 403) return t("err.forbidden");
        if (err.status === 422 && err.errors?.length) {
          const fields = err.errors.map((e) => String(e.loc?.[e.loc.length - 1] ?? "?")).join(", ");
          return t("err.validation", { details: fields });
        }
        return err.message;
      }
      return t("err.generic");
    },
    [t],
  );
}

// ---------- building blocks ----------

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const { t } = useI18n();
  return (
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  message,
  confirmLabel,
  danger,
  onConfirm,
  onClose,
}: {
  message: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Modal
      title={t("common.confirm")}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className={`btn ${danger ? "danger" : "primary"}`} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </>
      }
    >
      <p>{message}</p>
    </Modal>
  );
}

/**
 * A labelled form row. Use `plain` when the row holds buttons or a list (visitor picker, "reset" links): inside a
 * <label>, iPhones (Safari and Chrome) pass every tap on to the first control in it, which here clicked the
 * wrong button and undid the choice. A plain row is a <div>; the label text no longer focuses the box when tapped.
 */
export function Field({
  label,
  hint,
  error,
  children,
  plain,
}: {
  label: ReactNode;
  hint?: string;
  error?: string;
  children: ReactNode;
  plain?: boolean;
}) {
  const Row = plain ? "div" : "label";
  return (
    <Row className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && !error && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </Row>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <label className={`toggle ${disabled ? "disabled" : ""}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

/**
 * A whole-number box you can clear and retype. Binding a number input straight to a number turns an empty box
 * into 0 and leaves a stuck "0" in front of whatever you type ("090"). Here the text is kept as typed;
 * `onChange` gets the number, or null while the box is empty or not a number (`value` may then be null).
 * Leaving the box puts back the last valid number.
 */
export function NumberInput({
  value,
  onChange,
  min,
  max,
  step,
  disabled,
}: {
  value: number | null;
  onChange: (n: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  const lastValid = useRef(value ?? 0);

  // Follow changes made from outside (e.g. another unit picked), but never rewrite what is being typed.
  useEffect(() => {
    if (value === null) return;
    lastValid.current = value;
    setText((current) => (/^\d+$/.test(current) && Number(current) === value ? current : String(value)));
  }, [value]);

  return (
    <input
      type="number"
      inputMode="numeric"
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      value={text}
      onChange={(e) => {
        const next = e.target.value;
        setText(next);
        onChange(/^\d+$/.test(next) ? Number(next) : null);
      }}
      onBlur={() => {
        setText(String(lastValid.current));
        if (value === null) onChange(lastValid.current);
      }}
    />
  );
}

export function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  return <span className={`badge status-${status}`}>{t(`status.${status}` as Key)}</span>;
}

export function Banner({ kind = "warn", children }: { kind?: "warn" | "info"; children: ReactNode }) {
  return <div className={`banner ${kind}`}>{children}</div>;
}

export function Spinner() {
  const { t } = useI18n();
  return <div className="muted pad">{t("common.loading")}</div>;
}

/** Run an async action with a busy flag; returns [busy, run]. */
export function useBusy() {
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    try {
      return await fn();
    } finally {
      setBusy(false);
    }
  }, []);
  return useMemo(() => [busy, run] as const, [busy, run]);
}

/** True while the viewport matches the CSS media query (e.g. "(max-width: 820px)"). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMatches(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** Confirmed by the visitor, or still waiting for an answer while the visit is today or tomorrow. */
export function confirmState(a: Appointment, tz: string): "confirmed" | "awaiting" | null {
  if (a.status !== "scheduled") return null;
  if (a.confirmed_at) return "confirmed";
  if (Date.parse(a.start_at) <= Date.now()) return null;
  return localParts(a.start_at, tz).date <= todayInZone(tz, 1) ? "awaiting" : null;
}

export function ConfirmBadge({ appointment, tz }: { appointment: Appointment; tz: string }) {
  const { t } = useI18n();
  const state = confirmState(appointment, tz);
  if (!state) return null;
  return state === "confirmed" ? (
    <span className="badge confirm-yes">✓ {t("appt.confirmed")}</span>
  ) : (
    <span className="badge confirm-wait">? {t("appt.awaiting")}</span>
  );
}

// ---------- small helpers ----------

export const E164 = /^\+[1-9]\d{6,14}$/;
export const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function todayInZone(tz: string, offsetDays = 0): string {
  const d = new Date(Date.now() + offsetDays * 86400000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

/** Cabinet-local date (YYYY-MM-DD) and time (HH:MM) of a UTC instant. */
export function localParts(iso: string, tz: string): { date: string; time: string } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return { date: `${get("year")}-${get("month")}-${get("day")}`, time: `${get("hour")}:${get("minute")}` };
}

/** Split minutes into the largest unit that divides evenly (4320 -> 3 days). */
export function humanMinutes(minutes: number): { value: number; unit: "minutes" | "hours" | "days" } {
  if (minutes % 1440 === 0) return { value: minutes / 1440, unit: "days" };
  if (minutes % 60 === 0) return { value: minutes / 60, unit: "hours" };
  return { value: minutes, unit: "minutes" };
}
