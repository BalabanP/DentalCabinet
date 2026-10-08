import { useEffect, useState } from "react";
import App from "./App";
import Login from "./Login";
import { currentSession, signOut } from "./auth";
import { AUTH_ENABLED } from "./config";

/** Local mode shows the chat directly; with Cognito configured, it requires sign-in first. */
export default function Root() {
  const [state, setState] = useState<"checking" | "out" | "in">(AUTH_ENABLED ? "checking" : "in");

  useEffect(() => {
    if (!AUTH_ENABLED) return;
    currentSession().then((s) => setState(s ? "in" : "out"));
  }, []);

  if (state === "checking") return null;
  if (state === "out") return <Login onDone={() => setState("in")} />;
  return (
    <App
      onSignOut={
        AUTH_ENABLED
          ? () => {
              signOut();
              setState("out");
            }
          : undefined
      }
    />
  );
}
