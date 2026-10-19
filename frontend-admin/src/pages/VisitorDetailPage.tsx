import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import AppointmentDialog from "../components/AppointmentDialog";
import NewAppointmentDialog from "../components/NewAppointmentDialog";
import PatientDocuments from "../components/PatientDocuments";
import VisitorForm from "../components/VisitorForm";
import { useData } from "../data";
import { downloadPatientExport } from "../exportPatient";
import { useI18n } from "../i18n";
import type { Appointment } from "../types";
import { Banner, ConfirmDialog, Spinner, StatusBadge, useBusy, useErrorText, useNotice } from "../ui";

export default function VisitorDetailPage() {
  const { id = "" } = useParams();
  const { t, dateTime, lang } = useI18n();
  const { userById, usersLoaded, removeUser, timezone, isClinician } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const navigate = useNavigate();
  const [busy, run] = useBusy();

  const user = userById(id);
  const [appointments, setAppointments] = useState<Appointment[] | null>(null);
  const [notes, setNotes] = useState<string | null>(null);
  const [savedNotes, setSavedNotes] = useState("");
  const [notesMeta, setNotesMeta] = useState<string | undefined>();
  const [editing, setEditing] = useState(false);
  const [booking, setBooking] = useState(false);
  const [opened, setOpened] = useState<Appointment | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const loadAppointments = useCallback(() => {
    api
      .listUserAppointments(id)
      .then(setAppointments)
      .catch((e) => {
        setAppointments([]);
        notify("error", errorText(e));
      });
  }, [id, notify, errorText]);

  useEffect(() => {
    setAppointments(null);
    setNotes(null);
    if (!user) return;
    loadAppointments();
    if (isClinician) {
      api
        .getPatient(id)
        .then((p) => {
          setNotes(p.notes);
          setSavedNotes(p.notes);
          setNotesMeta(p.updated_at);
        })
        .catch((e) => notify("error", errorText(e)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, Boolean(user), isClinician]);

  if (!usersLoaded) return <Spinner />;
  if (!user) {
    return (
      <div className="page">
        <Banner>{t("vis.notFound")}</Banner>
        <Link to="/visitors">← {t("vis.title")}</Link>
      </div>
    );
  }

  const saveNotes = () =>
    run(async () => {
      try {
        const saved = await api.putPatient(id, notes ?? "");
        setSavedNotes(saved.notes);
        setNotesMeta(saved.updated_at);
        notify("ok", t("common.saved"));
      } catch (err) {
        notify("error", errorText(err));
      }
    });

  const exportZip = () =>
    run(async () => {
      notify("ok", t("vis.exporting"));
      try {
        await downloadPatientExport(id, lang);
        notify("ok", t("vis.exported"));
      } catch (err) {
        notify("error", errorText(err));
      }
    });

  const remove = () =>
    run(async () => {
      try {
        await api.deleteUser(id);
        removeUser(id);
        notify("ok", t("vis.deleted"));
        navigate("/visitors");
      } catch (err) {
        notify("error", err instanceof ApiError && err.status === 409 ? err.message : errorText(err));
      }
    });

  return (
    <div className="page">
      <Link to="/visitors" className="back">
        ← {t("vis.title")}
      </Link>
      <div className="page-head">
        <h1>{user.name}</h1>
        <div className="head-actions">
          <button className="btn" onClick={() => setEditing(true)}>
            {t("vis.detail.edit")}
          </button>
          {isClinician && (
            <button className="btn" disabled={busy} onClick={exportZip}>
              {t("vis.export")}
            </button>
          )}
          <button className="btn primary" onClick={() => setBooking(true)}>
            + {t("vis.detail.book")}
          </button>
        </div>
      </div>

      <div className="cards">
        <section className="card">
          <dl className="facts">
            <dt>{t("vis.col.phone")}</dt>
            <dd>{user.phone}</dd>
            <dt>{t("vis.col.email")}</dt>
            <dd>{user.email || "—"}</dd>
            <dt>{t("vis.col.language")}</dt>
            <dd>{user.locale === "en" || user.locale === "ro" ? t(`lang.${user.locale}`) : String(user.locale)}</dd>
            <dt>{t("vis.col.notify")}</dt>
            <dd>
              <span className={`dot ${user.notify_email ? "on" : "off"}`}>✉ {t("msg.email")}</span>{" "}
              <span className={`dot ${user.notify_sms !== false ? "on" : "off"}`}>📱 {t("msg.sms")}</span>
            </dd>
          </dl>
        </section>

        <section className="card">
          <h3>{t("vis.notes.title")}</h3>
          {!isClinician ? (
            <div className="muted">{t("vis.notes.noAccess")}</div>
          ) : notes === null ? (
            <Spinner />
          ) : (
            <>
              <p className="muted small">{t("vis.notes.hint")}</p>
              <textarea rows={7} maxLength={20000} value={notes} onChange={(e) => setNotes(e.target.value)} />
              <div className="actions">
                <button className="btn primary" disabled={busy || notes === savedNotes} onClick={saveNotes}>
                  {t("vis.notes.save")}
                </button>
                {notesMeta && (
                  <span className="muted small">{t("vis.notes.lastEdit", { when: dateTime(notesMeta, timezone) })}</span>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      {isClinician && <PatientDocuments userId={id} />}

      <section className="card">
        <h3>{t("vis.detail.appointments")}</h3>
        {appointments === null ? (
          <Spinner />
        ) : appointments.length === 0 ? (
          <div className="muted">{t("vis.detail.noAppointments")}</div>
        ) : (
          <table className="table clickable compact">
            <tbody>
              {appointments.map((a) => (
                <tr key={a.appointment_id} onClick={() => setOpened(a)}>
                  <td>{dateTime(a.start_at, timezone)}</td>
                  <td>
                    {a.duration_minutes} {t("common.min")}
                  </td>
                  <td>
                    <StatusBadge status={a.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className="danger-zone">
        <button className="btn danger" onClick={() => setConfirmDelete(true)}>
          {t("vis.delete")}
        </button>
      </div>

      {editing && <VisitorForm user={user} onClose={() => setEditing(false)} onSaved={() => setEditing(false)} />}
      {booking && (
        <NewAppointmentDialog
          presetUserId={id}
          onClose={() => setBooking(false)}
          onCreated={() => {
            setBooking(false);
            loadAppointments();
          }}
        />
      )}
      {opened && (
        <AppointmentDialog
          appointment={opened}
          onClose={() => setOpened(null)}
          onChanged={() => {
            setOpened(null);
            loadAppointments();
          }}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          danger
          message={t("vis.deleteConfirm")}
          confirmLabel={t("common.delete")}
          onClose={() => setConfirmDelete(false)}
          onConfirm={() => {
            setConfirmDelete(false);
            void remove();
          }}
        />
      )}
    </div>
  );
}
