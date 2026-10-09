import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "./api";
import { useI18n } from "./i18n";
import type { Key } from "./i18n";

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

export function Field({ label, hint, error, children }: { label: ReactNode; hint?: string; error?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && !error && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </label>
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
