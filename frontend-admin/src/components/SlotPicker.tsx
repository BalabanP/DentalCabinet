import { useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { Slot } from "../types";

/** Free start times for one day, from the backend's /availability. Click one to pick it. */
export default function SlotPicker({
  date,
  duration,
  value,
  onPick,
}: {
  date: string;
  duration: number;
  value: string;
  onPick: (time: string) => void;
}) {
  const { t } = useI18n();
  const [slots, setSlots] = useState<Slot[] | null>(null);

  useEffect(() => {
    if (!date) return;
    let stale = false;
    setSlots(null);
    const timer = setTimeout(() => {
      api
        .availability(date, duration >= 5 ? duration : undefined)
        .then((r) => !stale && setSlots(r.slots))
        .catch(() => !stale && setSlots([]));
    }, 250);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [date, duration]);

  return (
    <div className="slots">
      <div className="field-label">{t("appt.freeTimes")}</div>
      {slots === null ? (
        <div className="muted">{t("common.loading")}</div>
      ) : slots.length === 0 ? (
        <div className="muted">{t("appt.noSlots")}</div>
      ) : (
        <div className="chips">
          {slots.map((s) => (
            <button
              key={s.start_at}
              type="button"
              className={`chip ${s.local_time === value ? "active" : ""}`}
              onClick={() => onPick(s.local_time)}
            >
              {s.local_time}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
