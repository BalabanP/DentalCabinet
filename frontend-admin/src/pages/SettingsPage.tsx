import { useState } from "react";
import { api } from "../api";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { Key } from "../i18n";
import type { ReminderRule, ReminderSettings, ScheduleSettings, TimeWindow, Weekday } from "../types";
import { WEEKDAYS } from "../types";
import MessagesTab from "./MessagesTab";
import { Banner, ConfirmDialog, Field, humanMinutes, NumberInput, Spinner, Toggle, useBusy, useErrorText, useNotice } from "../ui";

type Unit = "minutes" | "hours" | "days";
const UNIT_MINUTES: Record<Unit, number> = { minutes: 1, hours: 60, days: 1440 };
const MAX_MINUTES = 60 * 24 * 60;

export default function SettingsPage() {
  const { t } = useI18n();
  const { reminders, schedule, isAdmin } = useData();
  const [tab, setTab] = useState<"reminders" | "hours" | "messages">("reminders");

  return (
    <div className="page">
      <div className="page-head">
        <h1>{t("set.title")}</h1>
      </div>
      <div className="tabs">
        <button className={tab === "reminders" ? "active" : ""} onClick={() => setTab("reminders")}>
          {t("set.tab.reminders")}
        </button>
        <button className={tab === "hours" ? "active" : ""} onClick={() => setTab("hours")}>
          {t("set.tab.hours")}
        </button>
        <button className={tab === "messages" ? "active" : ""} onClick={() => setTab("messages")}>
          {t("set.tab.messages")}
        </button>
      </div>
      {!isAdmin && <Banner kind="info">{t("set.readOnly")}</Banner>}
      {tab === "reminders" ? (
        reminders ? <RemindersTab initial={reminders} readOnly={!isAdmin} /> : <Spinner />
      ) : tab === "messages" ? (
        <MessagesTab readOnly={!isAdmin} />
      ) : schedule ? (
        <HoursTab initial={schedule} readOnly={!isAdmin} />
      ) : (
        <Spinner />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- reminders

function RemindersTab({ initial, readOnly }: { initial: ReminderSettings; readOnly: boolean }) {
  const { t } = useI18n();
  const { reloadSettings } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const [busy, run] = useBusy();
  const [form, setForm] = useState<ReminderSettings>(initial);
  const [confirmApply, setConfirmApply] = useState(false);

  const zones: string[] = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];

  const set = <K extends keyof ReminderSettings>(k: K, v: ReminderSettings[K]) => setForm((f) => ({ ...f, [k]: v }));
  const setRule = (i: number, rule: ReminderRule) => set("reminders", form.reminders.map((r, j) => (j === i ? rule : r)));

  const minutes = form.reminders.map((r) => r.minutes_before);
  const duplicate = new Set(minutes).size !== minutes.length;
  const outOfRange = minutes.some((m) => !Number.isInteger(m) || m < 1 || m > MAX_MINUTES);
  const problem = duplicate ? t("set.invalid.duplicate") : outOfRange ? t("set.invalid.range") : "";

  const save = () =>
    run(async () => {
      try {
        // The server adds a read-only `key` to each rule; send only what it accepts.
        const body: ReminderSettings = {
          ...form,
          cabinet_phone: form.cabinet_phone?.trim() || null,
          reminders: form.reminders.map((r) => ({ minutes_before: r.minutes_before, enabled: r.enabled })),
        };
        await api.putReminderSettings(body);
        await reloadSettings();
        notify("ok", t("set.saved"));
      } catch (err) {
        notify("error", errorText(err));
      }
    });

  const apply = () =>
    run(async () => {
      try {
        const r = await api.applyReminderSettings();
        notify(r.failed ? "error" : "ok", t("set.applied", { updated: r.updated, failed: r.failed }));
      } catch (err) {
        notify("error", errorText(err));
      }
    });

  return (
    <div className="card form-grid narrow">
      <Field label={t("set.cabinet.name")}>
        <input value={form.cabinet_name} disabled={readOnly} maxLength={120} onChange={(e) => set("cabinet_name", e.target.value)} />
      </Field>
      <Field label={t("set.cabinet.phone")}>
        <input value={form.cabinet_phone ?? ""} disabled={readOnly} maxLength={32} onChange={(e) => set("cabinet_phone", e.target.value)} />
      </Field>
      <Field label={t("set.cabinet.timezone")}>
        <input list="zones" value={form.timezone} disabled={readOnly} onChange={(e) => set("timezone", e.target.value)} />
        <datalist id="zones">
          {zones.map((z) => (
            <option key={z} value={z} />
          ))}
        </datalist>
      </Field>

      <Toggle checked={form.send_on_booking} disabled={readOnly} onChange={(v) => set("send_on_booking", v)} label={t("set.sendOnBooking")} />
      <Toggle checked={form.send_on_change} disabled={readOnly} onChange={(v) => set("send_on_change", v)} label={t("set.sendOnChange")} />

      <div>
        <div className="field-label">{t("set.channels")}</div>
        <div className="inline">
          <Toggle
            checked={form.channels.email}
            disabled={readOnly}
            onChange={(v) => set("channels", { ...form.channels, email: v })}
            label={t("msg.email")}
          />
          <Toggle
            checked={form.channels.sms}
            disabled={readOnly}
            onChange={(v) => set("channels", { ...form.channels, sms: v })}
            label={t("msg.sms")}
          />
        </div>
      </div>

      <div>
        <h3>{t("set.reminders.title")}</h3>
        <p className="muted small">{t("set.reminders.help")}</p>
        {form.reminders.map((rule, i) => {
          const h = humanMinutes(rule.minutes_before);
          return (
            <div className="rule" key={i}>
              <Toggle checked={rule.enabled} disabled={readOnly} onChange={(v) => setRule(i, { ...rule, enabled: v })} label={t("set.reminders.enabled")} />
              <NumberInput
                min={1}
                value={h.value}
                disabled={readOnly}
                onChange={(n) => n !== null && n >= 1 && setRule(i, { ...rule, minutes_before: Math.round(n * UNIT_MINUTES[h.unit]) })}
              />
              <select
                value={h.unit}
                disabled={readOnly}
                onChange={(e) => setRule(i, { ...rule, minutes_before: h.value * UNIT_MINUTES[e.target.value as Unit] })}
              >
                {(["minutes", "hours", "days"] as const).map((u) => (
                  <option key={u} value={u}>
                    {t(`set.unit.${u}` as Key)}
                  </option>
                ))}
              </select>
              <span className="muted small">{t("set.reminders.before")}</span>
              {!readOnly && (
                <button className="icon-btn" onClick={() => set("reminders", form.reminders.filter((_, j) => j !== i))} aria-label={t("common.delete")}>
                  ✕
                </button>
              )}
            </div>
          );
        })}
        {!readOnly && form.reminders.length < 10 && (
          <button className="btn small" onClick={() => set("reminders", [...form.reminders, { minutes_before: 60, enabled: true }])}>
            + {t("set.reminders.add")}
          </button>
        )}
        {problem && <div className="field-error">{problem}</div>}
      </div>

      {!readOnly && (
        <div className="actions">
          <button className="btn primary" disabled={busy || Boolean(problem)} onClick={save}>
            {t("common.save")}
          </button>
          <button className="btn" disabled={busy} onClick={() => setConfirmApply(true)}>
            {t("set.apply")}
          </button>
        </div>
      )}
      {confirmApply && (
        <ConfirmDialog
          message={t("set.applyConfirm")}
          confirmLabel={t("set.apply")}
          onClose={() => setConfirmApply(false)}
          onConfirm={() => {
            setConfirmApply(false);
            void apply();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- working hours

function HoursTab({ initial, readOnly }: { initial: ScheduleSettings; readOnly: boolean }) {
  const { t } = useI18n();
  const { reloadSettings } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const [busy, run] = useBusy();
  const [slot, setSlot] = useState(initial.slot_minutes);
  const [hours, setHours] = useState<Partial<Record<Weekday, TimeWindow[]>>>(initial.working_hours);

  const windows = (d: Weekday) => hours[d] ?? [];
  const setWindows = (d: Weekday, w: TimeWindow[]) => setHours((h) => ({ ...h, [d]: w }));

  const invalid = WEEKDAYS.some((d) => {
    const sorted = [...windows(d)].sort((a, b) => a.start.localeCompare(b.start));
    return sorted.some((w, i) => !w.start || !w.end || w.end <= w.start || (i > 0 && w.start < sorted[i - 1].end));
  });

  const save = () =>
    run(async () => {
      try {
        await api.putSchedule({ slot_minutes: slot, working_hours: hours });
        await reloadSettings();
        notify("ok", t("set.saved"));
      } catch (err) {
        notify("error", errorText(err));
      }
    });

  return (
    <div className="card form-grid narrow">
      <p className="muted small">{t("set.hours.help")}</p>
      <Field label={t("set.hours.slot")}>
        <NumberInput min={5} max={240} step={5} value={slot} disabled={readOnly} onChange={(n) => n !== null && setSlot(n)} />
      </Field>

      {WEEKDAYS.map((d) => (
        <div className="day-row" key={d}>
          <strong className="day-name">{t(`day.${d}` as Key)}</strong>
          <div className="day-windows">
            {windows(d).length === 0 && <span className="muted">{t("set.hours.closed")}</span>}
            {windows(d).map((w, i) => (
              <div className="inline" key={i}>
                <input
                  type="time"
                  value={w.start}
                  disabled={readOnly}
                  onChange={(e) => setWindows(d, windows(d).map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))}
                />
                <span>–</span>
                <input
                  type="time"
                  value={w.end}
                  disabled={readOnly}
                  onChange={(e) => setWindows(d, windows(d).map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))}
                />
                {!readOnly && (
                  <button className="icon-btn" onClick={() => setWindows(d, windows(d).filter((_, j) => j !== i))} aria-label={t("common.delete")}>
                    ✕
                  </button>
                )}
              </div>
            ))}
            {!readOnly && (
              <button className="btn small" onClick={() => setWindows(d, [...windows(d), { start: "09:00", end: "17:00" }])}>
                + {t("set.hours.addWindow")}
              </button>
            )}
          </div>
        </div>
      ))}
      {invalid && <div className="field-error">{t("set.hours.invalid")}</div>}

      {!readOnly && (
        <div className="actions">
          <button className="btn primary" disabled={busy || invalid || slot < 5 || slot > 240} onClick={save}>
            {t("common.save")}
          </button>
        </div>
      )}
    </div>
  );
}
