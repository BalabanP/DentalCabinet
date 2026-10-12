import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import type { EventResizeDoneArg } from "@fullcalendar/interaction";
import luxon3Plugin from "@fullcalendar/luxon3";
import roLocale from "@fullcalendar/core/locales/ro";
import type { DateSelectArg, EventClickArg, EventDropArg, EventInput, EventSourceFuncArg } from "@fullcalendar/core";
import { api, ApiError } from "../api";
import AgendaList from "../components/AgendaList";
import AppointmentDialog from "../components/AppointmentDialog";
import NewAppointmentDialog from "../components/NewAppointmentDialog";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { Appointment, Weekday } from "../types";
import { WEEKDAYS } from "../types";
import { Banner, ConfirmDialog, Toggle, confirmState, useErrorText, useMediaQuery, useNotice } from "../ui";

const pad = (n: number) => String(n).padStart(2, "0");
const toDuration = (minutes: number) => `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}:00`;
const toMinutes = (hhmm: string) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
// FullCalendar days: 0 = Sunday ... 6 = Saturday
const FC_DAY: Record<Weekday, number> = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };

function dayBefore(isoDate: string): string {
  return new Date(Date.parse(`${isoDate}T00:00:00Z`) - 86400000).toISOString().slice(0, 10);
}

type Prompt = { retry: () => Promise<void>; revert: () => void };

export default function CalendarPage() {
  const { t, lang } = useI18n();
  const { timezone, schedule, userById, usersLoaded, loadError } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const calendar = useRef<FullCalendar>(null);
  const phone = useMediaQuery("(max-width: 820px)");

  // On a phone the grid starts on one day; the agenda list is the default phone view.
  useEffect(() => {
    calendar.current?.getApi().changeView(phone ? "timeGridDay" : "timeGridWeek");
  }, [phone]);

  const [showCancelled, setShowCancelled] = useState(false);
  const [creating, setCreating] = useState<{ start?: string; duration?: number } | null>(null);
  const [selected, setSelected] = useState<Appointment | null>(null);
  const [prompt, setPrompt] = useState<Prompt | null>(null);

  const [phoneView, setPhoneView] = useState<"agenda" | "grid">("agenda");
  const [reloadKey, setReloadKey] = useState(0);
  const agenda = phone && phoneView === "agenda";

  const refresh = () => {
    calendar.current?.getApi().refetchEvents();
    setReloadKey((k) => k + 1);
  };

  // Working hours drive the grey/white shading, the visible time range and the slot size.
  const grid = useMemo(() => {
    const hours = schedule?.working_hours ?? {};
    const business: { daysOfWeek: number[]; startTime: string; endTime: string }[] = [];
    let min = 24 * 60;
    let max = 0;
    for (const day of WEEKDAYS) {
      for (const w of hours[day] ?? []) {
        business.push({ daysOfWeek: [FC_DAY[day]], startTime: w.start, endTime: w.end });
        min = Math.min(min, toMinutes(w.start));
        max = Math.max(max, toMinutes(w.end));
      }
    }
    const hasHours = business.length > 0;
    return {
      business: hasHours ? business : false,
      slotMin: toDuration(hasHours ? Math.max(0, Math.floor(min / 60) * 60 - 60) : 7 * 60),
      slotMax: toDuration(hasHours ? Math.min(24 * 60, Math.ceil(max / 60) * 60 + 60) : 20 * 60),
      slot: toDuration(schedule?.slot_minutes ?? 30),
      slotMinutes: schedule?.slot_minutes ?? 30,
    };
  }, [schedule]);

  // New function identity => FullCalendar refetches, so names appear once visitors are loaded.
  const loadEvents = useCallback(
    async (info: EventSourceFuncArg): Promise<EventInput[]> => {
      if (!usersLoaded) return [];
      try {
        const items = await api.listAppointments(info.startStr.slice(0, 10), dayBefore(info.endStr.slice(0, 10)));
        return items
          .filter((a) => showCancelled || a.status !== "cancelled")
          .map((a) => {
            const state = confirmState(a, timezone);
            return {
            id: a.appointment_id,
            title: `${state === "confirmed" ? "✓ " : state === "awaiting" ? "? " : ""}${userById(a.user_id)?.name ?? "…"}`,
            start: a.start_at,
            end: new Date(Date.parse(a.start_at) + a.duration_minutes * 60000).toISOString(),
            classNames: [`fc-status-${a.status}`, ...(state ? [`fc-${state}`] : [])],
            editable: a.status === "scheduled",
            extendedProps: { appt: a },
            };
          });
      } catch (err) {
        notify("error", errorText(err));
        return [];
      }
    },
    [usersLoaded, showCancelled, userById, timezone, notify, errorText],
  );

  const onSelect = (info: DateSelectArg) => {
    const api_ = info.view.calendar;
    api_.unselect();
    if (info.allDay) {
      api_.changeView("timeGridDay", info.start); // month cell: zoom into that day instead
      return;
    }
    const minutes = Math.round((info.end.getTime() - info.start.getTime()) / 60000);
    setCreating({ start: info.startStr.slice(0, 16), duration: Math.max(5, minutes) });
  };

  // Drag = reschedule, resize = change length. An overlap asks before forcing it.
  const change = async (arg: EventDropArg | EventResizeDoneArg, kind: "move" | "resize") => {
    const appt = arg.event.extendedProps.appt as Appointment;
    const start = arg.event.startStr;
    const end = arg.event.end;
    const body =
      kind === "move"
        ? { start_at: start }
        : { duration_minutes: Math.max(5, Math.round(((end?.getTime() ?? 0) - (arg.event.start?.getTime() ?? 0)) / 60000)) };

    const attempt = async (allowOverlap: boolean) => {
      try {
        await api.updateAppointment(appt.appointment_id, { ...body, allow_overlap: allowOverlap || undefined });
        notify("ok", t(kind === "move" ? "appt.moved" : "appt.updated"));
        refresh();
      } catch (err) {
        arg.revert();
        if (err instanceof ApiError && err.status === 409 && !allowOverlap) {
          setPrompt({ retry: () => attempt(true), revert: () => undefined });
        } else {
          notify("error", errorText(err));
        }
      }
    };
    await attempt(false);
  };

  return (
    <div className="page calendar-page">
      <div className="page-head">
        <h1>{t("nav.calendar")}</h1>
        <div className="head-actions">
          {phone && (
            <div className="segmented" role="tablist">
              <button className={agenda ? "active" : ""} onClick={() => setPhoneView("agenda")}>
                {t("cal.agenda")}
              </button>
              <button className={!agenda ? "active" : ""} onClick={() => setPhoneView("grid")}>
                {t("cal.grid")}
              </button>
            </div>
          )}
          <Toggle checked={showCancelled} onChange={setShowCancelled} label={t("cal.showCancelled")} />
          {!phone && (
            <button className="btn primary" onClick={() => setCreating({})}>
              + {t("cal.new")}
            </button>
          )}
        </div>
      </div>
      {loadError && <Banner>{t("err.load")}</Banner>}

      {agenda && <AgendaList reloadKey={reloadKey} showCancelled={showCancelled} onOpen={setSelected} />}
      {phone && (
        <button className="fab" aria-label={t("cal.new")} onClick={() => setCreating({})}>
          +
        </button>
      )}

      {!agenda && (
      <div className="calendar-wrap">
        <FullCalendar
          ref={calendar}
          plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin, luxon3Plugin]}
          initialView={phone ? "timeGridDay" : "timeGridWeek"}
          headerToolbar={
            phone
              ? { left: "prev,next", center: "title", right: "today" }
              : { left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek,timeGridDay" }
          }
          footerToolbar={phone ? { center: "timeGridDay,timeGridWeek,dayGridMonth" } : false}
          titleFormat={phone ? { day: "numeric", month: "short", year: "numeric" } : undefined}
          selectLongPressDelay={250}
          eventLongPressDelay={400}
          timeZone={timezone}
          locales={[roLocale]}
          locale={lang}
          firstDay={1}
          height={phone ? "auto" : "100%"}
          allDaySlot={false}
          nowIndicator
          selectable
          selectMirror
          editable
          eventDurationEditable
          businessHours={grid.business}
          slotMinTime={grid.slotMin}
          slotMaxTime={grid.slotMax}
          slotDuration={grid.slot}
          snapDuration="00:05:00"
          selectAllow={(info) => info.start.getTime() >= Date.now() - grid.slotMinutes * 60000}
          events={loadEvents}
          select={onSelect}
          eventClick={(info: EventClickArg) => setSelected(info.event.extendedProps.appt as Appointment)}
          eventDrop={(arg) => void change(arg, "move")}
          eventResize={(arg) => void change(arg, "resize")}
        />
      </div>
      )}

      {creating && (
        <NewAppointmentDialog
          initialStart={creating.start}
          initialDuration={creating.duration}
          onClose={() => setCreating(null)}
          onCreated={() => {
            setCreating(null);
            refresh();
          }}
        />
      )}
      {selected && (
        <AppointmentDialog
          appointment={selected}
          onClose={() => setSelected(null)}
          onChanged={() => {
            setSelected(null);
            refresh();
          }}
        />
      )}
      {prompt && (
        <ConfirmDialog
          danger
          message={t("appt.overlap")}
          confirmLabel={t("appt.moveAnyway")}
          onClose={() => setPrompt(null)}
          onConfirm={() => {
            const p = prompt;
            setPrompt(null);
            void p.retry();
          }}
        />
      )}
    </div>
  );
}
