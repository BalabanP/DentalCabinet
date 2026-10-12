import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "./api";
import { REPLIES_CHANGED } from "./components/RepliesInbox";
import { useData } from "./data";
import { useI18n } from "./i18n";
import type { Lang } from "./i18n";
import { Banner } from "./ui";

export default function Layout({ onSignOut }: { onSignOut?: () => void }) {
  const { t, lang, setLang } = useI18n();
  const { reminders, loadError } = useData();

  // A red dot on "Messages" while visitor replies are waiting for someone to read them.
  const [waiting, setWaiting] = useState(0);
  const location = useLocation();
  const refreshWaiting = useCallback(() => {
    api.listReplies().then((r) => setWaiting(r.length)).catch(() => undefined);
  }, []);
  useEffect(refreshWaiting, [refreshWaiting, location.pathname]);
  useEffect(() => {
    window.addEventListener(REPLIES_CHANGED, refreshWaiting);
    return () => window.removeEventListener(REPLIES_CHANGED, refreshWaiting);
  }, [refreshWaiting]);

  const link = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="logo">🦷</span>
          <div>
            <strong>{reminders?.cabinet_name ?? t("app.title")}</strong>
            <div className="muted small">{reminders?.timezone}</div>
          </div>
        </div>
        <nav>
          <NavLink to="/" end className={link}>
            <span className="ico" aria-hidden="true">📅</span>
            <span className="lbl">{t("nav.calendar")}</span>
          </NavLink>
          <NavLink to="/visitors" className={link}>
            <span className="ico" aria-hidden="true">👥</span>
            <span className="lbl">{t("nav.visitors")}</span>
          </NavLink>
          <NavLink to="/messages" className={link}>
            <span className="ico" aria-hidden="true">✉</span>
            <span className="lbl">{t("nav.messages")}</span>
            {waiting > 0 && <span className="count-dot">{waiting}</span>}
          </NavLink>
          <NavLink to="/settings" className={link}>
            <span className="ico" aria-hidden="true">⚙</span>
            <span className="lbl">{t("nav.settings")}</span>
          </NavLink>
        </nav>
        <div className="sidebar-foot">
          <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label={t("lang.label")}>
            <option value="en">English</option>
            <option value="ro">Română</option>
          </select>
          {onSignOut && (
            <button className="btn small" onClick={onSignOut}>
              {t("nav.signOut")}
            </button>
          )}
        </div>
      </aside>
      <main className="content">
        {loadError && !reminders && <Banner>{t("err.network")}</Banner>}
        <Outlet />
      </main>
    </div>
  );
}
