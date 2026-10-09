import { useState } from "react";
import { api, ApiError } from "../api";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { Appointment } from "../types";
import { Banner, Field, Modal, todayInZone, useBusy, useErrorText, useNotice } from "../ui";
import SlotPicker from "./SlotPicker";
import VisitorForm from "./VisitorForm";
import VisitorPicker from "./VisitorPicker";

/**
 * Book an appointment. `initialStart` is a cabinet-local "YYYY-MM-DDTHH:mm" (from a calendar click);
 * times are sent without a timezone, which the backend reads as cabinet-local.
 */
export default function NewAppointmentDialog({
  initialStart,
  initialDuration,
  presetUserId,
  onClose,
  onCreated,
}: {
  initialStart?: string;
  initialDuration?: number;
  presetUserId?: string;
  onClose: () => void;
  onCreated: (a: Appointment) => void;
}) {
  const { t } = useI18n();
  const { timezone } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const [busy, run] = useBusy();

  const [userId, setUserId] = useState(presetUserId ?? "");
  const [date, setDate] = useState(initialStart?.slice(0, 10) ?? todayInZone(timezone, 1));
  const [time, setTime] = useState(initialStart?.slice(11, 16) ?? "09:00");
  const [duration, setDuration] = useState(initialDuration ?? 30);
  const [note, setNote] = useState("");
  const [conflict, setConflict] = useState(false);
  const [creatingVisitor, setCreatingVisitor] = useState(false);

  const canSubmit = Boolean(userId && date && time && duration >= 5 && duration <= 480);

  const submit = async (allowOverlap: boolean) => {
    await run(async () => {
      try {
        const created = await api.createAppointment({
          user_id: userId,
          start_at: `${date}T${time}:00`,
          duration_minutes: duration,
          internal_note: note.trim() || undefined,
          allow_overlap: allowOverlap || undefined,
        });
        notify("ok", t("appt.created"));
        onCreated(created);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) setConflict(true);
        else notify("error", errorText(err));
      }
    });
  };

  return (
    <>
      <Modal
        title={t("appt.newTitle")}
        onClose={onClose}
        wide
        footer={
          <>
            <button className="btn" onClick={onClose}>
              {t("common.cancel")}
            </button>
            {conflict ? (
              <button className="btn danger" onClick={() => submit(true)} disabled={busy || !canSubmit}>
                {t("appt.bookAnyway")}
              </button>
            ) : (
              <button className="btn primary" onClick={() => submit(false)} disabled={busy || !canSubmit}>
                {t("appt.book")}
              </button>
            )}
          </>
        }
      >
        <div className="form-grid">
          <Field label={t("appt.visitor")}>
            <VisitorPicker value={userId} onChange={setUserId} onCreateNew={() => setCreatingVisitor(true)} />
          </Field>
          <div className="row2">
            <Field label={t("appt.dateTime")}>
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
              </div>
            </Field>
            <Field label={t("appt.duration")}>
              <input
                type="number"
                min={5}
                max={480}
                step={5}
                value={duration}
                onChange={(e) => {
                  setDuration(Number(e.target.value));
                  setConflict(false);
                }}
              />
            </Field>
          </div>
          <SlotPicker
            date={date}
            duration={duration}
            value={time}
            onPick={(v) => {
              setTime(v);
              setConflict(false);
            }}
          />
          <Field label={t("appt.note")}>
            <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          {conflict && <Banner>{t("appt.overlap")}</Banner>}
        </div>
      </Modal>
      {creatingVisitor && (
        <VisitorForm
          onClose={() => setCreatingVisitor(false)}
          onSaved={(u) => {
            setUserId(u.user_id);
            setCreatingVisitor(false);
          }}
        />
      )}
    </>
  );
}
