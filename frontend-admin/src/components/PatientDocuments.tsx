import { useCallback, useEffect, useRef, useState } from "react";
import { api, putFile } from "../api";
import { useData } from "../data";
import { useI18n } from "../i18n";
import type { PatientDocument } from "../types";
import { ConfirmDialog, Spinner, useErrorText, useNotice } from "../ui";

const MAX_MB = 20;

// The browser does not always know a file's type (Word files on some PCs), so fall back to the ending.
const TYPE_BY_ENDING: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  pdf: "application/pdf",
  doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};
const ACCEPT = Object.keys(TYPE_BY_ENDING).map((e) => `.${e}`).join(",");

export function fileSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function fileType(file: File): string | null {
  const ending = file.name.split(".").pop()?.toLowerCase() ?? "";
  const byEnding = TYPE_BY_ENDING[ending];
  return byEnding ?? null;
}

/** The files attached to one patient: upload, open, delete. Shown to clinicians and admins only. */
export default function PatientDocuments({ userId }: { userId: string }) {
  const { t, dateTime } = useI18n();
  const { timezone } = useData();
  const notify = useNotice();
  const errorText = useErrorText();
  const input = useRef<HTMLInputElement>(null);

  const [items, setItems] = useState<PatientDocument[] | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<PatientDocument | null>(null);

  const load = useCallback(() => {
    api
      .listDocuments(userId)
      .then((r) => setItems(r.items))
      .catch((e) => {
        setItems([]);
        notify("error", errorText(e));
      });
  }, [userId, notify, errorText]);

  useEffect(load, [load]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    for (const file of Array.from(files)) {
      const type = fileType(file);
      if (!type) {
        notify("error", t("docs.badType", { name: file.name }));
        continue;
      }
      if (file.size > MAX_MB * 1024 * 1024) {
        notify("error", t("docs.tooBig", { name: file.name, mb: MAX_MB }));
        continue;
      }
      setUploading(file.name);
      try {
        const ticket = await api.startUpload(userId, { filename: file.name, content_type: type, size: file.size });
        await putFile(ticket, file);
        await api.completeUpload(userId, ticket.document.doc_id);
        notify("ok", t("docs.uploaded"));
      } catch (err) {
        notify("error", `${file.name}: ${errorText(err)}`);
      }
    }
    setUploading(null);
    if (input.current) input.current.value = "";
    load();
  };

  const open = async (doc: PatientDocument) => {
    try {
      // The link is sent as a download, so the page itself stays where it is.
      window.location.href = await api.documentLink(userId, doc.doc_id);
    } catch (err) {
      notify("error", errorText(err));
    }
  };

  const remove = async (doc: PatientDocument) => {
    setToDelete(null);
    try {
      await api.deleteDocument(userId, doc.doc_id);
      setItems((list) => (list ?? []).filter((d) => d.doc_id !== doc.doc_id));
      notify("ok", t("docs.deleted"));
    } catch (err) {
      notify("error", errorText(err));
    }
  };

  return (
    <section className="card">
      <div className="detail-head">
        <h3>{t("docs.title")}</h3>
        <label className={`btn small ${uploading ? "disabled" : ""}`}>
          + {t("docs.add")}
          <input
            ref={input}
            type="file"
            multiple
            accept={ACCEPT}
            disabled={Boolean(uploading)}
            className="visually-hidden"
            onChange={(e) => void upload(e.target.files)}
          />
        </label>
      </div>
      <p className="muted small">{t("docs.hint", { mb: MAX_MB })}</p>
      {uploading && <div className="muted small">{t("docs.uploading", { name: uploading })}</div>}

      {items === null ? (
        <Spinner />
      ) : items.length === 0 ? (
        <div className="muted">{t("docs.empty")}</div>
      ) : (
        <ul className="doc-list">
          {items.map((d) => (
            <li key={d.doc_id} className="doc">
              <span className="doc-kind" aria-hidden="true">
                {d.name.split(".").pop()?.toUpperCase()}
              </span>
              <span className="doc-main">
                <strong>{d.name}</strong>
                <span className="muted small">
                  {fileSize(d.size)}
                  {d.uploaded_at ? ` · ${dateTime(d.uploaded_at, timezone)}` : ""}
                </span>
              </span>
              <span className="doc-actions">
                <button className="btn small" onClick={() => void open(d)}>
                  {t("docs.open")}
                </button>
                <button className="btn small danger" onClick={() => setToDelete(d)}>
                  {t("docs.delete")}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {toDelete && (
        <ConfirmDialog
          danger
          message={t("docs.deleteConfirm", { name: toDelete.name })}
          confirmLabel={t("docs.delete")}
          onClose={() => setToDelete(null)}
          onConfirm={() => void remove(toDelete)}
        />
      )}
    </section>
  );
}
