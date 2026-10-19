import { getIdToken } from "./auth";
import { API_BASE, AUTH_ENABLED } from "./config";
import type {
  Appointment,
  Availability,
  MessagePreview,
  MessagesConfig,
  MessageKind,
  NotificationRecord,
  Overrides,
  Locale,
  Patient,
  ReminderSettings,
  ScheduleSettings,
  User,
  UserInput,
  SmsReply,
  PatientDocument,
  UploadTicket,
  ExportBundle,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public errors?: { loc?: (string | number)[]; msg?: string }[]) {
    super(message);
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

type Query = Record<string, string | number | undefined | null>;

async function request<T>(method: string, path: string, opts: { body?: unknown; query?: Query } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (AUTH_ENABLED) {
    const token = await getIdToken();
    if (!token) {
      onUnauthorized?.();
      throw new ApiError(401, "Not signed in");
    }
    headers.Authorization = `Bearer ${token}`;
  }

  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  const qs = params.toString();

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ""}`, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (res.status === 401) onUnauthorized?.();
  if (res.status === 204) return undefined as T;

  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.message ?? `HTTP ${res.status}`, data?.errors);
  return data as T;
}

export const api = {
  // visitors
  async listAllUsers(): Promise<User[]> {
    const all: User[] = [];
    let token: string | null | undefined;
    do {
      const page = await request<{ items: User[]; next_token: string | null }>("GET", "/users", {
        query: { limit: 100, next_token: token ?? undefined },
      });
      all.push(...page.items);
      token = page.next_token;
    } while (token);
    return all;
  },
  createUser: (body: UserInput) => request<User>("POST", "/users", { body }),
  updateUser: (id: string, body: Partial<UserInput>) => request<User>("PATCH", `/users/${id}`, { body }),
  deleteUser: (id: string) => request<void>("DELETE", `/users/${id}`),

  // patient notes
  getPatient: (id: string) => request<Patient>("GET", `/users/${id}/patient`),
  putPatient: (id: string, notes: string) => request<Patient>("PUT", `/users/${id}/patient`, { body: { notes } }),

  // patient documents
  listDocuments: (id: string) =>
    request<{ items: PatientDocument[]; max_bytes: number }>("GET", `/users/${id}/documents`),
  startUpload: (id: string, body: { filename: string; content_type: string; size: number }) =>
    request<UploadTicket>("POST", `/users/${id}/documents`, { body }),
  completeUpload: (id: string, docId: string) =>
    request<PatientDocument>("POST", `/users/${id}/documents/${docId}/complete`),
  documentLink: (id: string, docId: string) =>
    request<{ url: string }>("GET", `/users/${id}/documents/${docId}/download`).then((r) => r.url),
  deleteDocument: (id: string, docId: string) => request<void>("DELETE", `/users/${id}/documents/${docId}`),
  exportPatient: (id: string, lang: Locale) =>
    request<ExportBundle>("GET", `/users/${id}/export`, { query: { lang } }),

  // appointments
  listAppointments: (from: string, to: string) =>
    request<{ items: Appointment[] }>("GET", "/appointments", { query: { from, to } }).then((r) => r.items),
  listUserAppointments: (userId: string) =>
    request<{ items: Appointment[] }>("GET", "/appointments", { query: { user_id: userId } }).then((r) => r.items),
  createAppointment: (body: {
    user_id: string;
    start_at: string;
    duration_minutes: number;
    internal_note?: string;
    allow_overlap?: boolean;
  }) => request<Appointment>("POST", "/appointments", { body }),
  updateAppointment: (
    id: string,
    body: { start_at?: string; duration_minutes?: number; internal_note?: string; status?: "completed" | "no_show"; allow_overlap?: boolean },
  ) => request<Appointment>("PATCH", `/appointments/${id}`, { body }),
  cancelAppointment: (id: string) => request<Appointment>("POST", `/appointments/${id}/cancel`),
  confirmAppointment: (id: string) => request<Appointment>("POST", `/appointments/${id}/confirm`),
  listReplies: () => request<{ items: SmsReply[] }>("GET", "/sms-replies").then((r) => r.items),
  dismissReply: (id: string) => request<void>("DELETE", `/sms-replies/${id}`),
  listNotifications: (id: string) =>
    request<{ items: NotificationRecord[] }>("GET", `/appointments/${id}/notifications`).then((r) => r.items),
  availability: (from: string, durationMinutes?: number) =>
    request<Availability>("GET", "/availability", { query: { from, duration_minutes: durationMinutes } }),

  // message texts
  getMessages: () => request<MessagesConfig>("GET", "/settings/messages"),
  putMessages: (overrides: Overrides) => request<MessagesConfig>("PUT", "/settings/messages", { body: { overrides } }),
  previewMessage: (body: {
    kind: MessageKind;
    locale: Locale;
    email_subject: string;
    email_body: string;
    sms_body: string;
  }) => request<MessagePreview>("POST", "/settings/messages/preview", { body }),

  // settings
  getReminderSettings: () => request<ReminderSettings>("GET", "/settings/reminders"),
  putReminderSettings: (body: ReminderSettings) => request<ReminderSettings>("PUT", "/settings/reminders", { body }),
  applyReminderSettings: () =>
    request<{ updated: number; failed: number }>("POST", "/settings/reminders/apply-to-existing"),
  getSchedule: () => request<ScheduleSettings>("GET", "/settings/schedule"),
  putSchedule: (body: ScheduleSettings) => request<ScheduleSettings>("PUT", "/settings/schedule", { body }),
};

/**
 * Send a file straight to storage with the signed link from `startUpload`. No login header: the link
 * itself is the permission, and an extra Authorization header would make S3 refuse it.
 */
export async function putFile(ticket: UploadTicket, file: Blob): Promise<void> {
  let res: Response;
  try {
    res = await fetch(ticket.upload.url, { method: ticket.upload.method, headers: ticket.upload.headers, body: file });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!res.ok) throw new ApiError(res.status, `upload failed (HTTP ${res.status})`);
}

/** Download a file from a signed link (used to put patient documents in the export ZIP). */
export async function fetchFile(url: string): Promise<Blob> {
  let res: Response;
  try {
    res = await fetch(url);
  } catch {
    throw new ApiError(0, "network");
  }
  if (!res.ok) throw new ApiError(res.status, `download failed (HTTP ${res.status})`);
  return res.blob();
}
