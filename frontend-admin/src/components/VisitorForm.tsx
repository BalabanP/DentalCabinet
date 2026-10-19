import { useState } from "react";
import { api, ApiError } from "../api";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { User, UserInput } from "../types";
import { E164, EMAIL, Field, Modal, Toggle, useBusy, useErrorText, useNotice } from "../ui";

/** Create (no `user`) or edit a visitor. */
export default function VisitorForm({
  user,
  onClose,
  onSaved,
}: {
  user?: User;
  onClose: () => void;
  onSaved: (u: User) => void;
}) {
  const { t } = useI18n();
  const { upsertUser } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const [busy, run] = useBusy();
  const [form, setForm] = useState<UserInput>({
    name: user?.name ?? "",
    email: user?.email ?? "",
    phone: user?.phone ?? "",
    locale: user?.locale === "en" ? "en" : "ro", // a record saved with an old language falls back to Romanian
    notify_email: user?.notify_email ?? true,
    notify_sms: user?.notify_sms ?? true,
  });
  const [touched, setTouched] = useState(false);
  const set = <K extends keyof UserInput>(k: K, v: UserInput[K]) => setForm((f) => ({ ...f, [k]: v }));

  const errors = {
    name: form.name.trim() ? "" : t("vis.form.required"),
    email: !form.email.trim() || EMAIL.test(form.email.trim()) ? "" : t("vis.form.invalidEmail"), // optional
    phone: E164.test(form.phone.replace(/[\s-]/g, "")) ? "" : t("vis.form.invalidPhone"),
  };
  const valid = !errors.name && !errors.email && !errors.phone;

  const submit = async () => {
    setTouched(true);
    if (!valid) return;
    const body: UserInput = {
      ...form,
      name: form.name.trim(),
      email: form.email.trim().toLowerCase(),
      phone: form.phone.replace(/[\s-]/g, ""),
    };
    await run(async () => {
      try {
        const saved = user ? await api.updateUser(user.user_id, body) : await api.createUser(body);
        upsertUser(saved);
        notify("ok", user ? t("vis.updated") : t("vis.created"));
        onSaved(saved);
      } catch (err) {
        notify("error", err instanceof ApiError && err.status === 409 ? err.message : errorText(err));
      }
    });
  };

  return (
    <Modal
      title={user ? t("vis.detail.edit") : t("vis.add")}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className="btn primary" onClick={submit} disabled={busy}>
            {t("common.save")}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <Field label={t("vis.form.name")} error={touched ? errors.name : ""}>
          <input value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
        </Field>
        <Field label={t("vis.form.emailOptional")} hint={t("vis.form.emailHint")} error={touched ? errors.email : ""}>
          <input type="email" value={form.email} onChange={(e) => set("email", e.target.value)} />
        </Field>
        <Field label={t("vis.form.phone")} hint={t("vis.form.phoneHint")} error={touched ? errors.phone : ""}>
          <input type="tel" value={form.phone} onChange={(e) => set("phone", e.target.value)} />
        </Field>
        <Field label={t("vis.form.language")}>
          <select value={form.locale} onChange={(e) => set("locale", e.target.value as UserInput["locale"])}>
            <option value="ro">{t("lang.ro")}</option>
            <option value="en">{t("lang.en")}</option>
          </select>
        </Field>
        <Toggle checked={form.notify_email} onChange={(v) => set("notify_email", v)} label={t("vis.form.notifyEmail")} />
        <Toggle checked={form.notify_sms} onChange={(v) => set("notify_sms", v)} label={t("vis.form.notifySms")} />
      </div>
    </Modal>
  );
}
