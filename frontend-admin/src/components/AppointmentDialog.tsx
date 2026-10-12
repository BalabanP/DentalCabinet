import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { Appointment } from "../types";
import {
  Banner,
  ConfirmBadge,
  ConfirmDialog,
  Field,
  humanMinutes,
  localParts,
  Modal,
  StatusBadge,
  useBusy,
  useErrorText,
  useNotice,
} from "../ui";
import NotificationsTable from "./NotificationsTable";
import SlotPicker from "./SlotPicker";

/** View an appointment; reschedule, complete, mark no-show or cancel it; see reminders and messages. */
export default function AppointmentDialog({
  appointment,
  onClose,
  onChanged,
}: {
  appointment: Appointment;
  onClose: () => void;
  onChanged: (a: Appointment) => void;
}) {
  const { t, dateTime } = useI18n();
  const { timezone, userById } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const [busy, run] = useBusy();

  const visitor = userById(appointment.user_id);
  const initial = localParts(appointment.start_at, timezone);
  const [date, setDate] = useState(initial.date);
  const [time, setTime] = useState(initial.time);
  const [note, setNote] = useState(appointment.internal_note ?? "");
  const [conflict, setConflict] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const active = appointment.status === "scheduled";
  const moved = date !== initial.date || time !== initial.time;

  const apply = (action: () => Promise<Appointment>, successKey: "appt.moved" | "appt.updated" | "appt.cancelled" | "appt.markedConfirmed") =>
    run(async () => {
      try {
        const updated = await action();
        notify("ok", t(successKey));
        onChanged(updated);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409 && moved) setConflict(true);
        else notify("error", errorText(err));
      }
    });

  const reschedule = (allowOverlap: boolean) =>
    apply(
      () => api.updateAppointment(appointment.appointment_id, { start_at: `${date}T${time}:00`, allow_overlap: allowOverlap || undefined }),
      "appt.moved",
    );

  return (
    <>
      <Modal title={t("appt.title")} onClose={onClose} wide>
        <div className="detail-head">
          <div>
            <h3>{visitor?.name ?? "…"}</h3>
            <div className="muted">
              {dateTime(appointment.start_at, timezone)} · {appointment.duration_minutes} {t("common.min")}
            </div>
          </div>
          <StatusBadge status={appointment.status} />
        </div>
        {appointment.cancelled_via === "sms" && <Banner kind="info">{t("appt.cancelledSms")}</Banner>}
        {active && (
          <div className="confirm-row">
            <ConfirmBadge appointment={appointment} tz={timezone} />
            {appointment.confirmed_at ? (
              <span className="muted small">
                {t(appointment.confirmed_via === "sms" ? "appt.confirmedSms" : "appt.confirmedStaff", {
                  when: dateTime(appointment.confirmed_at, timezone),
                })}
              </span>
            ) : (
              <button
                className="btn small"
                disabled={busy}
                onClick={() => apply(() => api.confirmAppointment(appointment.appointment_id), "appt.markedConfirmed")}
              >
                {t("appt.markConfirmed")}
              </button>
            )}
          </div>
        )}
        {visitor && (
          <p className="small">
            <Link to={`/visitors/${visitor.user_id}`} onClick={onClose}>
              {t("appt.openVisitor")}
            </Link>{" "}
            · {visitor.phone}
          </p>
        )}

        {active && (
          <section className="block">
            <h4>{t("appt.reschedule")}</h4>
            <div className="inline">
              <input
                type="date"
                value={date}
                onChange={(e) => {
                  setDate(e.target.value);
                  setConflict(false);
                }}
              />
              <input
                type="time"
                step={300}
                value={time}
                onChange={(e) => {
                  setTime(e.target.value);
                  setConflict(false);
                }}
              />
              {conflict ? (
                <button className="btn danger" disabled={busy} onClick={() => reschedule(true)}>
                  {t("appt.moveAnyway")}
                </button>
              ) : (
                <button className="btn primary" disabled={busy || !moved} onClick={() => reschedule(false)}>
                  {t("appt.reschedule")}
                </button>
              )}
            </div>
            <SlotPicker
              date={date}
              duration={appointment.duration_minutes}
              value={time}
              onPick={(v) => {
                setTime(v);
                setConflict(false);
              }}
            />
            {conflict && <Banner>{t("appt.overlap")}</Banner>}
          </section>
        )}

        <section className="block">
          <Field label={t("appt.note")}>
            <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <button
            className="btn small"
            disabled={busy || note === (appointment.internal_note ?? "")}
            onClick={() => apply(() => api.updateAppointment(appointment.appointment_id, { internal_note: note }), "appt.updated")}
          >
            {t("appt.saveNote")}
          </button>
        </section>

        <section className="block">
          <h4>{t("appt.reminders")}</h4>
          {appointment.reminders.length === 0 ? (
            <div className="muted">{t("appt.noReminders")}</div>
          ) : (
            <ul className="plain">
              {appointment.reminders.map((r) => {
                const h = humanMinutes(r.minutes_before);
                return (
                  <li key={r.key}>
                    {dateTime(r.send_at, timezone)}{" "}
                    <span className="muted">
                      ({t("appt.beforeStart", { n: `${h.value} ${t(`set.unit.${h.unit}`)}` })})
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="block">
          <h4>{t("appt.messages")}</h4>
          <NotificationsTable appointmentId={appointment.appointment_id} />
        </section>

        {active && (
          <div className="actions">
            <button
              className="btn"
              disabled={busy}
              onClick={() => apply(() => api.updateAppointment(appointment.appointment_id, { status: "completed" }), "appt.updated")}
            >
              {t("appt.complete")}
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() => apply(() => api.updateAppointment(appointment.appointment_id, { status: "no_show" }), "appt.updated")}
            >
              {t("appt.noShow")}
            </button>
            <button className="btn danger" disabled={busy} onClick={() => setConfirmCancel(true)}>
              {t("appt.cancel")}
            </button>
          </div>
        )}
      </Modal>
      {confirmCancel && (
        <ConfirmDialog
          danger
          message={t("appt.cancelConfirm")}
          confirmLabel={t("appt.cancel")}
          onClose={() => setConfirmCancel(false)}
          onConfirm={() => {
            setConfirmCancel(false);
            void apply(() => api.cancelAppointment(appointment.appointment_id), "appt.cancelled");
          }}
        />
      )}
    </>
  );
}
