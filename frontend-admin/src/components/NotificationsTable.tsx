import { useEffect, useState } from "react";
import { api } from "../api";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { Key } from "../i18n";
import type { NotificationRecord } from "../types";
import { useErrorText } from "../ui";

/** Delivery log (email / SMS) of one appointment. */
export default function NotificationsTable({ appointmentId }: { appointmentId: string }) {
  const { t, dateTime } = useI18n();
  const { timezone } = useData();
  const errorText = useErrorText();
  const [rows, setRows] = useState<NotificationRecord[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stale = false;
    setRows(null);
    api
      .listNotifications(appointmentId)
      .then((r) => !stale && setRows(r))
      .catch((e) => !stale && setError(errorText(e)));
    return () => {
      stale = true;
    };
  }, [appointmentId, errorText]);

  if (error) return <div className="field-error">{error}</div>;
  if (rows === null) return <div className="muted">{t("common.loading")}</div>;
  if (rows.length === 0) return <div className="muted">{t("appt.noMessages")}</div>;

  return (
    <table className="table compact stack">
      <thead>
        <tr>
          <th>{t("msg.col.kind")}</th>
          <th>{t("msg.col.channel")}</th>
          <th>{t("msg.col.status")}</th>
          <th>{t("msg.col.time")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((n) => (
          <tr key={n.sk}>
            <td className="primary">{t(`msg.${n.kind}` as Key)}</td>
            <td data-label={t("msg.col.channel")}>{t(`msg.${n.channel}` as Key)}</td>
            <td data-label={t("msg.col.status")}>
              <span className={`badge notif-${n.status}`} title={n.error}>
                {t(`msg.${n.status}` as Key)}
              </span>
            </td>
            <td data-label={t("msg.col.time")}>{dateTime(n.updated_at, timezone)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
