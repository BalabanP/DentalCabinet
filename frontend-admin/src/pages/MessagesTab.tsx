import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { useI18n } from "../i18n";
import type { Key } from "../i18n";
import { MESSAGE_KINDS } from "../types";
import type {
  Locale,
  MessageKind,
  MessagePreview,
  MessagesConfig,
  Overrides,
  TemplateField,
} from "../types";
import { Banner, Field, Spinner, useBusy, useErrorText, useNotice } from "../ui";

const LOCALES: Locale[] = ["ro", "en"];

/** Stable text for comparing two override sets, whatever order their keys came in. */
function canon(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canon).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canon(v)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

/** Keep only fields that differ from the built-in text. A field equal to its default is not "customised". */
function compact(draft: Overrides, defaults: MessagesConfig["defaults"]): Overrides {
  const out: Overrides = {};
  for (const kind of MESSAGE_KINDS) {
    for (const locale of LOCALES) {
      const fields = draft[kind]?.[locale] ?? {};
      for (const [field, raw] of Object.entries(fields) as [TemplateField, string][]) {
        const value = raw.trim();
        if (value && value !== defaults[kind][locale][field]) {
          out[kind] = { ...out[kind], [locale]: { ...out[kind]?.[locale], [field]: value } };
        }
      }
    }
  }
  return out;
}

export default function MessagesTab({ readOnly }: { readOnly: boolean }) {
  const { t } = useI18n();
  const notify = useNotice();
  const errorText = useErrorText();
  const [busy, run] = useBusy();

  const [config, setConfig] = useState<MessagesConfig | null>(null);
  const [draft, setDraft] = useState<Overrides>({});
  const [kind, setKind] = useState<MessageKind>("reminder");
  const [locale, setLocale] = useState<Locale>("ro");
  const [preview, setPreview] = useState<MessagePreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewTab, setPreviewTab] = useState<"email" | "sms">("email");
  const [focused, setFocused] = useState<TemplateField>("sms_body");

  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const smsRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api
      .getMessages()
      .then((c) => {
        setConfig(c);
        setDraft(c.overrides);
      })
      .catch((e) => notify("error", errorText(e)));
  }, [notify, errorText]);

  // What the editor shows: the customised text if there is one, otherwise the built-in text.
  const value = (field: TemplateField): string =>
    draft[kind]?.[locale]?.[field] ?? config?.defaults[kind][locale][field] ?? "";
  const isCustom = (field: TemplateField): boolean =>
    config !== null && value(field).trim() !== "" && value(field).trim() !== config.defaults[kind][locale][field];

  const setField = (field: TemplateField, text: string) =>
    setDraft((d) => ({ ...d, [kind]: { ...d[kind], [locale]: { ...d[kind]?.[locale], [field]: text } } }));

  const subject = value("email_subject");
  const body = value("email_body");
  const sms = value("sms_body");

  // Live preview, rendered by the server exactly as visitors will receive it.
  useEffect(() => {
    if (!config) return;
    let stale = false;
    const timer = setTimeout(() => {
      api
        .previewMessage({ kind, locale, email_subject: subject, email_body: body, sms_body: sms })
        .then((p) => {
          if (stale) return;
          setPreview(p);
          setPreviewError("");
        })
        .catch((e) => {
          if (stale) return;
          // The server names the problem, e.g. "unknown placeholder: {nam}"
          setPreviewError(
            e instanceof ApiError && e.status === 422 && e.errors?.length
              ? e.errors.map((x) => (x.msg ?? "").replace(/^Value error, /, "")).join("; ")
              : errorText(e),
          );
        });
    }, 400);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [config, kind, locale, subject, body, sms, errorText]);

  const cleaned = useMemo(() => (config ? compact(draft, config.defaults) : {}), [config, draft]);
  const dirty = config !== null && canon(cleaned) !== canon(config.overrides);

  const reset = (field: TemplateField) =>
    setDraft((d) => {
      const fields = { ...d[kind]?.[locale] };
      delete fields[field];
      return { ...d, [kind]: { ...d[kind], [locale]: fields } };
    });

  const insert = (placeholder: string) => {
    const el = { email_subject: subjectRef, email_body: bodyRef, sms_body: smsRef }[focused].current;
    const token = `{${placeholder}}`;
    const current = value(focused);
    if (!el) return setField(focused, current + token);
    const start = el.selectionStart ?? current.length;
    const end = el.selectionEnd ?? current.length;
    setField(focused, current.slice(0, start) + token + current.slice(end));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + token.length, start + token.length);
    });
  };

  const save = () =>
    run(async () => {
      try {
        const saved = await api.putMessages(cleaned);
        setConfig(saved);
        setDraft(saved.overrides);
        notify("ok", t("msgs.saved"));
      } catch (err) {
        notify("error", err instanceof ApiError && err.status === 422 ? t("err.validation", { details: previewError }) : errorText(err));
      }
    });

  if (!config) return <Spinner />;

  const labelOf = (field: TemplateField, key: Key) => (
    <span className="label-row">
      {t(key)}
      <span className={`pill ${isCustom(field) ? "custom" : ""}`}>{isCustom(field) ? t("msgs.customised") : t("msgs.builtIn")}</span>
      {isCustom(field) && !readOnly && (
        <button type="button" className="link-btn" onClick={() => reset(field)}>
          {t("msgs.reset")}
        </button>
      )}
    </span>
  );

  return (
    <div className="editor">
      <div className="card form-grid">
        <p className="muted small">{t("msgs.intro")}</p>

        <div className="row2 equal">
          <Field label={t("msgs.kind")}>
            <select value={kind} onChange={(e) => setKind(e.target.value as MessageKind)}>
              {MESSAGE_KINDS.map((k) => (
                <option key={k} value={k}>
                  {t(`msg.${k}` as Key)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("msgs.language")}>
            <select value={locale} onChange={(e) => setLocale(e.target.value as Locale)}>
              {LOCALES.map((l) => (
                <option key={l} value={l}>
                  {t(`lang.${l}` as Key)}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {!readOnly && (
          <div>
            <div className="field-label">{t("msgs.placeholders")}</div>
            <div className="chips">
              {config.placeholders.map((p) => (
                <button
                  key={p}
                  type="button"
                  className="chip mono"
                  title={t(`msgs.ph.${p}` as Key)}
                  onMouseDown={(e) => e.preventDefault()} // keep the cursor in the text box
                  onClick={() => insert(p)}
                >
                  {`{${p}}`}
                </button>
              ))}
            </div>
          </div>
        )}

        <Field plain label={labelOf("email_subject", "msgs.emailSubject")}>
          <input
            ref={subjectRef}
            value={subject}
            maxLength={150}
            disabled={readOnly}
            onFocus={() => setFocused("email_subject")}
            onChange={(e) => setField("email_subject", e.target.value)}
          />
        </Field>
        <Field plain label={labelOf("email_body", "msgs.emailBody")} hint={t("msgs.emailBodyHint")}>
          <textarea
            ref={bodyRef}
            rows={6}
            value={body}
            maxLength={2000}
            disabled={readOnly}
            onFocus={() => setFocused("email_body")}
            onChange={(e) => setField("email_body", e.target.value)}
          />
        </Field>
        <Field plain label={labelOf("sms_body", "msgs.smsBody")}>
          <textarea
            ref={smsRef}
            rows={3}
            value={sms}
            maxLength={600}
            disabled={readOnly}
            onFocus={() => setFocused("sms_body")}
            onChange={(e) => setField("sms_body", e.target.value)}
          />
        </Field>

        {previewError && <Banner>{previewError}</Banner>}

        {!readOnly && (
          <div className="actions">
            <button className="btn primary" disabled={busy || !dirty || Boolean(previewError)} onClick={save}>
              {t("common.save")}
            </button>
            {dirty && (
              <>
                <button className="btn" disabled={busy} onClick={() => setDraft(config.overrides)}>
                  {t("msgs.resetAll")}
                </button>
                <span className="muted small">{t("msgs.unsaved")}</span>
              </>
            )}
          </div>
        )}
      </div>

      <div className="card preview">
        <div className="tabs">
          <button className={previewTab === "email" ? "active" : ""} onClick={() => setPreviewTab("email")}>
            {t("msgs.previewEmail")}
          </button>
          <button className={previewTab === "sms" ? "active" : ""} onClick={() => setPreviewTab("sms")}>
            {t("msgs.previewSms")}
          </button>
        </div>
        <p className="muted small">{t("msgs.sampleNote")}</p>

        {!preview ? (
          <Spinner />
        ) : previewTab === "email" ? (
          <>
            <div className="email-subject">
              <span className="muted small">{t("msgs.emailSubject")}</span>
              <strong>{preview.email_subject}</strong>
            </div>
            {/* The HTML comes from our server; sandbox="" also blocks scripts and links just in case. */}
            <iframe title="email preview" className="email-frame" sandbox="" srcDoc={preview.email_html} />
          </>
        ) : (
          <>
            <div className="phone">
              <div className="sms-bubble">{preview.sms_text}</div>
            </div>
            <p className={`small ${preview.sms.segments > 1 ? "warn-text" : "muted"}`}>
              {t("msgs.smsCount", { length: preview.sms.length, segments: preview.sms.segments })}
            </p>
            {preview.sms.encoding !== "GSM-7" && <p className="warn-text small">{t("msgs.smsUnicode")}</p>}
            {preview.sms.segments > 1 && <p className="warn-text small">{t("msgs.smsLong")}</p>}
          </>
        )}
      </div>
    </div>
  );
}
