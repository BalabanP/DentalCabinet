export type Locale = "en" | "ro";

export type User = {
  user_id: string;
  name: string;
  email?: string; // optional: patients without e-mail get SMS only
  phone: string;
  locale: Locale;
  notify_email: boolean;
  notify_sms: boolean;
  created_at: string;
  updated_at: string;
};
export type UserInput = Omit<User, "user_id" | "created_at" | "updated_at" | "email"> & { email: string }; // "" = none

export type AppointmentStatus = "scheduled" | "cancelled" | "completed" | "no_show";

export type ScheduledReminder = { key: string; minutes_before: number; send_at: string; schedule_name: string };

export type Appointment = {
  appointment_id: string;
  user_id: string;
  start_at: string; // UTC, e.g. 2026-10-12T07:00:00Z
  day: string; // cabinet-local YYYY-MM-DD
  duration_minutes: number;
  status: AppointmentStatus;
  internal_note?: string;
  confirmed_at?: string | null; // the visitor (or staff) confirmed the visit
  confirmed_via?: "sms" | "staff" | null;
  cancelled_via?: "sms" | null;
  reminders: ScheduledReminder[];
  created_at: string;
  updated_at: string;
};

export type Patient = { user_id: string; notes: string; updated_at?: string; updated_by?: string };

export type NotificationRecord = {
  appointment_id: string;
  sk: string;
  kind: "confirmation" | "reminder" | "rescheduled" | "cancelled";
  channel: "email" | "sms" | "whatsapp"; // "whatsapp" only appears in old log rows
  status: "sending" | "sent" | "failed" | "skipped";
  provider_id?: string;
  error?: string;
  updated_at: string;
};

export type ReminderRule = { minutes_before: number; enabled: boolean };
export type ReminderSettings = {
  cabinet_name: string;
  cabinet_phone: string | null;
  timezone: string;
  send_on_booking: boolean;
  send_on_change: boolean;
  channels: { email: boolean; sms: boolean };
  reminders: ReminderRule[];
};

export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Weekday = (typeof WEEKDAYS)[number];
export type TimeWindow = { start: string; end: string };
export type ScheduleSettings = { slot_minutes: number; working_hours: Partial<Record<Weekday, TimeWindow[]>> };

export type Slot = { day: string; local_time: string; start_at: string };
export type Availability = { duration_minutes: number; timezone: string; slots: Slot[] };

// Editable message texts (Settings, Messages)
export const MESSAGE_KINDS = ["confirmation", "reminder", "rescheduled", "cancelled"] as const;
export type MessageKind = (typeof MESSAGE_KINDS)[number];
export const TEMPLATE_FIELDS = ["email_subject", "email_body", "sms_body"] as const;
export type TemplateField = (typeof TEMPLATE_FIELDS)[number];
export type TemplateFields = Partial<Record<TemplateField, string>>;
export type Overrides = Partial<Record<MessageKind, Partial<Record<Locale, TemplateFields>>>>;
export type MessagesConfig = {
  placeholders: string[];
  defaults: Record<MessageKind, Record<Locale, Record<TemplateField, string>>>;
  overrides: Overrides;
};
export type MessagePreview = {
  email_subject: string;
  email_html: string;
  email_text: string;
  sms_text: string;
  sms: { encoding: string; length: number; segments: number };
};

/** An SMS answer from a visitor that the app could not act on by itself. */
export type SmsReply = {
  id: string;
  sender: string;
  text: string;
  received_at: string;
  reason: "unknown_sender" | "unrecognised" | "no_upcoming" | "several_upcoming";
  user_id?: string;
  user_name?: string;
};

/** A file attached to a patient (stored in S3). */
export type PatientDocument = {
  doc_id: string;
  name: string;
  content_type: string;
  size: number;
  uploaded_at?: string;
  uploaded_by?: string;
};

export type UploadTicket = {
  document: PatientDocument;
  upload: { url: string; method: "PUT"; headers: Record<string, string> };
};

/** What the export endpoint returns; the browser turns it into a ZIP. */
export type ExportBundle = {
  filename: string;
  summary_pdf: string; // base64
  documents_folder: string;
  documents: { name: string; size: number; url: string }[];
};
