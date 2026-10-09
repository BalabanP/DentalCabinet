import { useState } from "react";
import type { FormEvent } from "react";
import { signIn } from "./auth";
import type { SignInStep } from "./auth";
import { useI18n } from "./i18n";
import type { Lang } from "./i18n";

type Pending = Exclude<SignInStep, { step: "done" } | { step: "unsupported" }> | null;

export default function Login({ onDone }: { onDone: () => void }) {
  const { t, lang, setLang } = useI18n();
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
        <h1>{t("login.title")}</h1>
        <p className="muted">{t("login.subtitle")}</p>

        {!pending && (
          <>
            <label className="field">
              <span className="field-label">{t("login.email")}</span>
              <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <label className="field">
              <span className="field-label">{t("login.password")}</span>
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
          </>
        )}
        {pending?.step === "new_password" && (
          <label className="field">
            <span className="field-label">{t("login.newPassword")}</span>
            <input type="password" autoComplete="new-password" value={extra} onChange={(e) => setExtra(e.target.value)} required />
          </label>
        )}
        {pending?.step === "totp" && (
          <label className="field">
            <span className="field-label">{t("login.code")}</span>
            <input inputMode="numeric" autoComplete="one-time-code" value={extra} onChange={(e) => setExtra(e.target.value)} required />
          </label>
        )}

        {error && <div className="banner warn">{error}</div>}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? t("login.wait") : pending ? t("login.continue") : t("login.signIn")}
        </button>
        <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label={t("lang.label")}>
          <option value="en">English</option>
          <option value="ro">Română</option>
        </select>
      </form>
    </div>
  );
}
