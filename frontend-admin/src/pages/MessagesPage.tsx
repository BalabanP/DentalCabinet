import { Fragment, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import NotificationsTable from "../components/NotificationsTable";
import RepliesInbox from "../components/RepliesInbox";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { Appointment } from "../types";
import { Spinner, StatusBadge, todayInZone, useErrorText, useNotice } from "../ui";

/** Which email / SMS reminders were sent (or failed) for the appointments of a period. */
export default function MessagesPage() {
  const { t, dateTime } = useI18n();
  const { timezone, userById, usersLoaded } = useData();
  const notify = useNotice();
  const errorText = useErrorText();

  const [from, setFrom] = useState(() => todayInZone(timezone, -7));
  const [to, setTo] = useState(() => todayInZone(timezone, 7));
  const [items, setItems] = useState<Appointment[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    setItems(null);
    api
      .listAppointments(from, to)
      .then((r) => setItems([...r].sort((a, b) => b.start_at.localeCompare(a.start_at))))
      .catch((e) => {
        setItems([]);
        notify("error", errorText(e));
      });
  }, [from, to, notify, errorText]);

  useEffect(() => {
    if (from && to && from <= to) load();
  }, [load, from, to]);

  return (
    <div className="page">
      <RepliesInbox />
      <div className="page-head">
        <h1>{t("log.title")}</h1>
        <div className="head-actions">
          <label className="inline">
            {t("log.from")}
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label className="inline">
            {t("log.to")}
            <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
          </label>
        </div>
      </div>
      <p className="muted small">{t("log.hint")}</p>

      {items === null || !usersLoaded ? (
        <Spinner />
      ) : items.length === 0 ? (
        <div className="muted pad">{t("log.none")}</div>
      ) : (
        <div className="table-wrap">
          <table className="table clickable stack">
            <thead>
              <tr>
                <th>{t("log.col.when")}</th>
                <th>{t("log.col.visitor")}</th>
                <th>{t("log.col.status")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <Fragment key={a.appointment_id}>
                  <tr onClick={() => setOpen(open === a.appointment_id ? null : a.appointment_id)}>
                    <td className="primary">{dateTime(a.start_at, timezone)}</td>
                    <td data-label={t("log.col.visitor")}>{userById(a.user_id)?.name ?? "…"}</td>
                    <td data-label={t("log.col.status")}>
                      <StatusBadge status={a.status} />
                    </td>
                  </tr>
                  {open === a.appointment_id && (
                    <tr className="expanded">
                      <td colSpan={3}>
                        <NotificationsTable appointmentId={a.appointment_id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
