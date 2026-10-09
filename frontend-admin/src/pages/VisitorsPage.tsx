import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import VisitorForm from "../components/VisitorForm";
import { useData } from "../data";
import { useI18n } from "../i18n";
import { Banner, Spinner } from "../ui";

const PAGE = 50;
const digits = (s: string) => s.replace(/\D/g, "");

export default function VisitorsPage() {
  const { t } = useI18n();
  const { users, usersLoaded, loadError } = useData();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [adding, setAdding] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    const qd = digits(q);
    return users.filter(
      (u) => u.name.toLowerCase().includes(q) || u.email.includes(q) || (qd.length >= 3 && digits(u.phone).includes(qd)),
    );
  }, [users, query]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>{t("vis.title")}</h1>
        <div className="head-actions">
          <input
            className="search"
            type="search"
            placeholder={t("common.search")}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setLimit(PAGE);
            }}
          />
          <button className="btn primary" onClick={() => setAdding(true)}>
            + {t("vis.add")}
          </button>
        </div>
      </div>
      {loadError && <Banner>{t("err.load")}</Banner>}
      {!usersLoaded ? (
        <Spinner />
      ) : filtered.length === 0 ? (
        <div className="muted pad">{t("vis.empty")}</div>
      ) : (
        <>
          <div className="muted small">{t("vis.count", { n: filtered.length })}</div>
          <div className="table-wrap">
            <table className="table clickable stack">
              <thead>
                <tr>
                  <th>{t("vis.col.name")}</th>
                  <th>{t("vis.col.phone")}</th>
                  <th>{t("vis.col.email")}</th>
                  <th>{t("vis.col.language")}</th>
                  <th>{t("vis.col.notify")}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, limit).map((u) => (
                  <tr key={u.user_id} onClick={() => navigate(`/visitors/${u.user_id}`)}>
                    <td className="primary">
                      <strong>{u.name}</strong>
                    </td>
                    <td data-label={t("vis.col.phone")}>{u.phone}</td>
                    <td data-label={t("vis.col.email")}>{u.email}</td>
                    <td data-label={t("vis.col.language")}>{u.locale.toUpperCase()}</td>
                    <td data-label={t("vis.col.notify")}>
                      <span title={t("msg.email")} className={`dot ${u.notify_email ? "on" : "off"}`}>
                        ✉
                      </span>{" "}
                      <span title={t("msg.sms")} className={`dot ${u.notify_sms !== false ? "on" : "off"}`}>
                        📱
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {filtered.length > limit && (
            <button className="btn" onClick={() => setLimit(limit + PAGE)}>
              {t("vis.showMore")}
            </button>
          )}
        </>
      )}
      {adding && (
        <VisitorForm
          onClose={() => setAdding(false)}
          onSaved={(u) => {
            setAdding(false);
            navigate(`/visitors/${u.user_id}`);
          }}
        />
      )}
    </div>
  );
}
