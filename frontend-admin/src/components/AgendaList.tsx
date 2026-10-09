import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { Appointment } from "../types";
import { Spinner, StatusBadge, localParts, todayInZone, useErrorText, useNotice } from "../ui";

const STEP_DAYS = 14;
const MAX_DAYS = 60; // the server limits a range to 62 days

function addDays(isoDate: string, n: number): string {
  return new Date(Date.parse(`${isoDate}T00:00:00Z`) + n * 86400000).toISOString().slice(0, 10);
}

/** Phone-friendly list of upcoming appointments, grouped by day. */
export default function AgendaList({
  reloadKey,
  showCancelled,
  onOpen,
}: {
  reloadKey: number;
  showCancelled: boolean;
  onOpen: (a: Appointment) => void;
}) {
  const { t, lang, time } = useI18n();
  const { timezone, userById, usersLoaded } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const [days, setDays] = useState(STEP_DAYS);
  const [items, setItems] = useState<Appointment[] | null>(null);

  const today = todayInZone(timezone);

  useEffect(() => {
    let stale = false;
    api
      .listAppointments(today, addDays(today, days - 1))
      .then((r) => !stale && setItems(r))
      .catch((e) => {
        if (stale) return;
        setItems([]);
        notify("error", errorText(e));
      });
    return () => {
      stale = true;
    };
  }, [today, days, reloadKey, notify, errorText]);

  const groups = useMemo(() => {
    const byDay = new Map<string, Appointment[]>();
    for (const a of (items ?? []).filter((x) => showCancelled || x.status !== "cancelled")) {
      const day = localParts(a.start_at, timezone).date;
      byDay.set(day, [...(byDay.get(day) ?? []), a]);
    }
    return [...byDay.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, list]) => [day, list.sort((x, y) => x.start_at.localeCompare(y.start_at))] as const);
  }, [items, showCancelled, timezone]);

  const heading = (day: string): string => {
    const label = new Intl.DateTimeFormat(lang === "ro" ? "ro-RO" : "en-GB", {
      weekday: "long",
      day: "numeric",
      month: "long",
      timeZone: "UTC",
    }).format(new Date(`${day}T12:00:00Z`));
    if (day === today) return `${t("cal.today")} · ${label}`;
    if (day === addDays(today, 1)) return `${t("cal.tomorrow")} · ${label}`;
    return label;
  };

  if (items === null || !usersLoaded) return <Spinner />;

  return (
    <div className="agenda">
      {groups.length === 0 && <div className="muted pad">{t("cal.noEvents")}</div>}
      {groups.map(([day, list]) => (
        <section key={day}>
          <h3 className={`agenda-day ${day === today ? "today" : ""}`}>{heading(day)}</h3>
          {list.map((a) => {
            const user = userById(a.user_id);
            const end = new Date(Date.parse(a.start_at) + a.duration_minutes * 60000).toISOString();
            return (
              <button key={a.appointment_id} className={`agenda-item status-${a.status}`} onClick={() => onOpen(a)}>
                <span className="agenda-time">
                  <strong>{time(a.start_at, timezone)}</strong>
                  <span className="muted small">{time(end, timezone)}</span>
                </span>
                <span className="agenda-main">
                  <strong>{user?.name ?? "…"}</strong>
                  <span className="muted small">
                    {a.duration_minutes} {t("common.min")}
                    {user?.phone ? ` · ${user.phone}` : ""}
                  </span>
                </span>
                {a.status !== "scheduled" && <StatusBadge status={a.status} />}
              </button>
            );
          })}
        </section>
      ))}
      {days < MAX_DAYS && (
        <button className="btn" onClick={() => setDays((d) => Math.min(MAX_DAYS, d + STEP_DAYS))}>
          {t("cal.more")}
        </button>
      )}
    </div>
  );
}
