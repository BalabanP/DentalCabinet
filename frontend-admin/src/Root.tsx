import { useEffect, useState } from "react";
import { HashRouter, Route, Routes } from "react-router-dom";
import { setUnauthorizedHandler } from "./api";
import { currentSession, signOut } from "./auth";
import { AUTH_ENABLED } from "./config";
import { DataProvider } from "./data";
import Layout from "./Layout";
import Login from "./Login";
import CalendarPage from "./pages/CalendarPage";
import MessagesPage from "./pages/MessagesPage";
import SettingsPage from "./pages/SettingsPage";
import VisitorDetailPage from "./pages/VisitorDetailPage";
import VisitorsPage from "./pages/VisitorsPage";

/** Local mode shows the app directly; with Cognito configured it requires sign-in first. */
export default function Root() {
  const [state, setState] = useState<"checking" | "out" | "in">(AUTH_ENABLED ? "checking" : "in");

  useEffect(() => {
    if (!AUTH_ENABLED) return;
    currentSession().then((s) => setState(s ? "in" : "out"));
  }, []);

  const leave = () => {
    signOut();
    setState("out");
  };

  // An expired session on any request sends the user back to the sign-in page.
  useEffect(() => {
    setUnauthorizedHandler(AUTH_ENABLED ? leave : null);
    return () => setUnauthorizedHandler(null);
  }, []);

  if (state === "checking") return null;
  if (state === "out") return <Login onDone={() => setState("in")} />;

  return (
    <DataProvider>
      <HashRouter>
        <Routes>
          <Route element={<Layout onSignOut={AUTH_ENABLED ? leave : undefined} />}>
            <Route index element={<CalendarPage />} />
            <Route path="visitors" element={<VisitorsPage />} />
            <Route path="visitors/:id" element={<VisitorDetailPage />} />
            <Route path="messages" element={<MessagesPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </HashRouter>
    </DataProvider>
  );
}
