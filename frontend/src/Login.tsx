import { useState } from "react";
import type { FormEvent } from "react";
import { signIn } from "./auth";
import type { SignInStep } from "./auth";

type Pending = Exclude<SignInStep, { step: "done" } | { step: "unsupported" }> | null;

export default function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [extra, setExtra] = useState("");
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const handle = (result: SignInStep) => {
    if (result.step === "done") return onDone();
    if (result.step === "unsupported") return setError(result.message);
    setExtra("");
    setPending(result);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      handle(pending ? await pending.submit(extra) : await signIn(email, password));
    } catch (err) {
      // Cognito's messages are safe to show ("Incorrect username or password.", "Invalid code", ...)
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form onSubmit={submit}>
        <h1>Cabinet Assistant</h1>
        <p className="sub">Sign in with your staff account.</p>

        {!pending && (
          <>
            <label>
              Email
              <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
          </>
        )}

        {pending?.step === "new_password" && (
          <label>
            Choose a new password (12+ characters, upper and lower case, a digit and a symbol)
            <input type="password" autoComplete="new-password" value={extra} onChange={(e) => setExtra(e.target.value)} required />
          </label>
        )}
        {pending?.step === "totp" && (
          <label>
            Code from your authenticator app
            <input inputMode="numeric" autoComplete="one-time-code" value={extra} onChange={(e) => setExtra(e.target.value)} required />
          </label>
        )}

        {error && <div className="banner warn">{error}</div>}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Please wait…" : pending ? "Continue" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
