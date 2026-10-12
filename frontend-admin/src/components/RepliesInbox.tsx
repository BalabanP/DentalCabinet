import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { Key } from "../i18n";
import type { SmsReply } from "../types";
import { useBusy, useErrorText, useNotice } from "../ui";

/** Tell the rest of the app (the red dot in the menu) that the inbox changed. */
export const REPLIES_CHANGED = "replies-changed";

/** SMS answers from visitors that the app could not apply by itself, for reception to read. */
export default function RepliesInbox() {
  const { t, dateTime } = useI18n();
  const { timezone } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const [busy, run] = useBusy();
  const [items, setItems] = useState<SmsReply[]>([]);

  const load = useCallback(() => {
    api
      .listReplies()
      .then(setItems)
      .catch((e) => notify("error", errorText(e)));
  }, [notify, errorText]);

  useEffect(load, [load]);

  const dismiss = (id: string) =>
    run(async () => {
      try {
        await api.dismissReply(id);
        setItems((list) => list.filter((r) => r.id !== id));
        window.dispatchEvent(new Event(REPLIES_CHANGED));
      } catch (err) {
        notify("error", errorText(err));
      }
    });

  if (items.length === 0) return null;

  return (
    <section className="card replies">
      <h3>
        {t("replies.title")} <span className="count-dot">{items.length}</span>
      </h3>
      <p className="muted small">{t("replies.intro")}</p>
      {items.map((r) => (
        <div key={r.id} className="reply">
          <div className="reply-head">
            <strong>{r.user_name ?? t("replies.unknownNumber")}</strong>
            <span className="muted small">
              {r.sender} · {dateTime(r.received_at, timezone)}
            </span>
          </div>
          <blockquote>{r.text}</blockquote>
          <div className="muted small">{t(`replies.reason.${r.reason}` as Key)}</div>
          <div className="actions">
            {r.user_id && (
              <Link className="btn small" to={`/visitors/${r.user_id}`}>
                {t("replies.openVisitor")}
              </Link>
            )}
            <button className="btn small" disabled={busy} onClick={() => dismiss(r.id)}>
              {t("replies.dismiss")}
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}
