import { NavLink, Outlet } from "react-router-dom";
import { useData } from "./data";
import { useI18n } from "./i18n";
import type { Lang } from "./i18n";
import { Banner } from "./ui";

export default function Layout({ onSignOut }: { onSignOut?: () => void }) {
  const { t, lang, setLang } = useI18n();
  const { reminders, loadError } = useData();

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
