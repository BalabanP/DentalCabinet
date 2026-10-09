import { useMemo, useState } from "react";
import { useData } from "../data";
import { useI18n } from "../i18n";

const digits = (s: string) => s.replace(/\D/g, "");

/** Pick a visitor from the loaded list by name, email or phone. */
export default function VisitorPicker({
  value,
  onChange,
  onCreateNew,
}: {
  value: string;
  onChange: (userId: string) => void;
  onCreateNew: () => void;
}) {
  const { t } = useI18n();
  const { users, userById } = useData();
  const [query, setQuery] = useState("");
  const selected = value ? userById(value) : undefined;

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users.slice(0, 8);
    const qd = digits(q);
    return users
      .filter((u) => u.name.toLowerCase().includes(q) || u.email.includes(q) || (qd.length >= 3 && digits(u.phone).includes(qd)))
      .slice(0, 8);
  }, [users, query]);

  if (selected) {
    return (
      <div className="picked">
        <div>
          <strong>{selected.name}</strong>
          <div className="muted small">
            {selected.phone} · {selected.email}
          </div>
        </div>
        <button type="button" className="btn small" onClick={() => onChange("")}>
          {t("common.edit")}
        </button>
      </div>
    );
  }

  return (
    <div className="picker">
      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("appt.pickVisitor")} autoFocus />
      <div className="picker-list">
        {matches.length === 0 && <div className="muted pad-s">{t("appt.noVisitorMatch")}</div>}
        {matches.map((u) => (
          <button type="button" key={u.user_id} className="picker-item" onClick={() => onChange(u.user_id)}>
            <strong>{u.name}</strong>
            <span className="muted small">
              {u.phone} · {u.email}
            </span>
          </button>
        ))}
        <button type="button" className="picker-item new" onClick={onCreateNew}>
          + {t("appt.newVisitor")}
        </button>
      </div>
    </div>
  );
}
