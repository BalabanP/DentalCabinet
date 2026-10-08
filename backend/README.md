# Medical Cabinet: serverless backend

Python 3.11, AWS SAM. Manages visitors of a medical cabinet, appointments and clinical notes, and reminds visitors by **email (SES)** and **WhatsApp (AWS End User Messaging Social)**.

## Architecture

```
Client -> API Gateway HTTP API (Cognito JWT) -> ApiFunction
                                                  |- DynamoDB: Users, Patients, Appointments, Settings, Notifications
                                                  |- EventBridge Scheduler: one one-time schedule per reminder
                                                  '- SQS: immediate notices (confirmation / rescheduled / cancelled)
EventBridge Scheduler --at(send_at)--> ReminderQueue (+DLQ, alarm) --> SenderFunction --> SES / WhatsApp
```

Reminders are exact one-time schedules, so there is no polling Lambda. On reschedule or cancel the old schedules are deleted. Any that survive are discarded by the sender, because it checks that the appointment is still `scheduled` at the same `start_at`.

## Data model

| Table | Key | Notes |
|---|---|---|
| Users | `user_id`; GSI `email-index` | name, email, phone (E.164), `locale` (en/ro/ru), `notify_email` / `notify_whatsapp` consent flags |
| Patients | `user_id` (1:1 with user) | `notes`: free text (at most 20,000 chars). Separate table, clinician-only |
| Appointments | `appointment_id`; GSIs `user-index`, `day-index` | `start_at` (UTC), `day` (cabinet-local date), `status`, `internal_note` (never sent), `reminders[]` |
| Settings | `setting_id = reminders` | cabinet name/phone/timezone, channels, reminder offsets |
| Notifications | `appointment_id` + `sk` | delivery log and idempotency (`sending/sent/failed/skipped`), TTL 1 year |

## Reminder configuration

`PUT /settings/reminders` (admin). Defaults match the requirement:

```json
{
  "cabinet_name": "Dr. Pop Cabinet",
  "cabinet_phone": "+40211234567",
  "timezone": "Europe/Bucharest",
  "send_on_booking": true,
  "send_on_change": true,
  "channels": { "email": true, "whatsapp": true },
  "reminders": [
    { "minutes_before": 4320, "enabled": true },
    { "minutes_before": 1440, "enabled": true },
    { "minutes_before": 120,  "enabled": true }
  ]
}
```

- `send_on_booking`: confirmation right after the appointment is made. `send_on_change`: notice on reschedule or cancel.
- Up to 10 reminders, unique offsets, each up to 60 days. A reminder whose time has already passed at booking is skipped (a visit tomorrow gets no 3-day reminder).
- Changes affect appointments booked or rescheduled afterwards. `POST /settings/reminders/apply-to-existing` re-plans all future appointments without sending new confirmations.
- A message goes out on a channel only if the cabinet channel flag **and** the visitor's consent flag are on.

## API

All routes need `Authorization: Bearer <Cognito token>`. Roles are Cognito groups: `reception` < `clinician` < `admin` (clinician and admin can also read and write patient notes; admin also changes settings).

| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/users` | staff | 409 on duplicate email |
| GET | `/users` | staff | `?q=` search by name, email or phone (min 2 chars, prefix matches first, `limit` default 25), `?email=` exact lookup, or `limit` (1-100) and `next_token` pagination |
| GET / PATCH / DELETE | `/users/{id}` | staff | DELETE returns 409 while future appointments exist; also deletes the patient record |
| GET / PUT | `/users/{id}/patient` | clinician | body `{"notes": "..."}` |
| POST | `/appointments` | staff | `{user_id, start_at, duration_minutes?, internal_note?, allow_overlap?}`. Naive `start_at` means cabinet-local time. Returns 409 if the time overlaps another scheduled appointment, unless `allow_overlap` is true |
| GET | `/appointments` | staff | `?from=YYYY-MM-DD&to=` (max 62 days, default today), or `?user_id=`; optional `status` |
| GET / PATCH | `/appointments/{id}` | staff | PATCH: `start_at` (reschedule), `duration_minutes`, `internal_note`, or `status` = `completed` / `no_show` |
| POST | `/appointments/{id}/cancel` | staff | idempotent |
| GET | `/appointments/{id}/notifications` | staff | delivery log |
| GET | `/availability` | staff | `?from=YYYY-MM-DD&to=` (max 14 days) `&duration_minutes=`. Free start times inside working hours, in the future, not overlapping a booking |
| GET | `/settings/schedule` | staff | working hours and slot spacing |
| PUT | `/settings/schedule` | admin | `{"slot_minutes": 30, "working_hours": {"mon": [{"start":"09:00","end":"17:00"}], ...}}`. Missing or empty day = closed. Default: Mon-Fri 09:00-17:00, 30-minute slots |
| GET | `/settings/reminders` | staff | |
| PUT | `/settings/reminders` | admin | |
| POST | `/settings/reminders/apply-to-existing` | admin | |

Errors are `{"message": ...}`. Validation errors return 422 with field paths and never echo the submitted values.

## Local development and tests

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1                            # PowerShell (cmd: .venv\Scripts\activate.bat; macOS/Linux: source .venv/bin/activate)
pip install -r src/requirements.txt pytest "moto[dynamodb]" cfn-lint
pytest                  # unit and moto-backed integration tests (no AWS account needed)
cfn-lint template.yaml
```

## Run locally (no AWS, no Docker)

```powershell
python local/server.py            # API on http://127.0.0.1:8000
python local/smoke.py             # in a second terminal: creates a user, notes and an appointment
```

This runs the real API and sender code on in-memory fake AWS (moto). Data is lost on restart. Emails and WhatsApp messages are printed in the server console instead of being sent. A background loop fires schedules when they are due, which stands in for EventBridge Scheduler. Authentication is bypassed: every request is an `admin`. To try another role, send the header `X-Dev-Groups: reception` (or `clinician`).

The local settings are seeded with the default reminders plus one extra **3 minutes before** reminder, so you can book a visit about 5 minutes ahead and watch a reminder arrive in about 2 minutes. Call `PUT /settings/reminders` to change them.

Example calls:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/settings/reminders
Invoke-RestMethod http://127.0.0.1:8000/users -Method Post -ContentType application/json -Body '{"name":"Ana Pop","email":"ana@example.com","phone":"+40712345678"}'
Invoke-RestMethod http://127.0.0.1:8000/users
```

## Deploy

Prerequisites: AWS credentials, [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).

1. **SES**: verify the sender identity (e.g. `clinic@example.com`). New accounts are in the sandbox and can only send to verified addresses. Request production access before go-live.
2. Edit `parameter_overrides` in `samconfig.toml` (`SesFromAddress`, `CorsOrigin`).
3. `sam build && sam deploy` (use `--config-env prod` for production). Take `ApiUrl`, `UserPoolId` and `UserPoolClientId` from the stack outputs for the frontend.
4. **Create staff** (self sign-up is disabled):
   ```bash
   aws cognito-idp admin-create-user --user-pool-id <UserPoolId> --username doctor@example.com
   aws cognito-idp admin-add-user-to-group --user-pool-id <UserPoolId> --username doctor@example.com --group-name clinician
   ```
5. **Set the cabinet name, timezone and reminders** with `PUT /settings/reminders`. Until you do, the timezone defaults to UTC.

### WhatsApp setup (optional; the channel is skipped until configured)

1. Create or link a WhatsApp Business Account in **AWS End User Messaging Social** (needs a Meta Business account and a phone number).
2. Create and get approved **four message templates**, in each language you use (`en`, `ro`, `ru`), named `appointment_confirmation`, `appointment_reminder`, `appointment_rescheduled`, `appointment_cancelled`. Each takes four body variables: `{{1}}` name, `{{2}}` cabinet, `{{3}}` date (`24.10.2026`), `{{4}}` time (`09:00`). Example for `appointment_reminder` (en): *"Hello {{1}}, this is a reminder of your appointment at {{2}} on {{3}} at {{4}}."*
3. Redeploy with `WhatsAppPhoneNumberId=phone-number-id-...`.

### Operations
- `DeadLetterAlarm` fires when a message fails 3 times. Set `AlarmEmail` to be notified. Inspect failures through `GET /appointments/{id}/notifications` and the CloudWatch logs.
- Tables, KMS key and Cognito pool have `DeletionPolicy: Retain`. `prod` also enables deletion protection.

## Security and privacy notes
- DynamoDB is encrypted with a customer-managed KMS key, with point-in-time recovery on. Cognito is admin-create-only, MFA (TOTP) is on by default, and password length is at least 12.
- Least privilege: the sender Lambda cannot read the Patients table, and messages contain only name, cabinet, date and time. Never diagnoses.
- Logs never include request bodies, notes, emails or phone numbers.
- Consent: `notify_email` / `notify_whatsapp` are per visitor. Make sure you have a lawful basis and record consent before messaging. `DELETE /users/{id}` supports erasure.
- This repository does not make you GDPR/HIPAA compliant by itself (data-processing agreements, retention policy, audit requirements and region choice are yours to decide). Check them with your compliance contact.

## Known limits and next steps
- `GET /users` uses a DynamoDB scan, which is fine for a single cabinet (thousands of visitors). Add a search index (e.g. name prefix GSI) if listing or search gets slow.
- Booking checks overlaps but not working hours, so staff can still book outside them. Use `/availability` to offer slots. The overlap check reads then writes without a lock, so two simultaneous bookings of the same slot could both succeed (unlikely at one-cabinet volume).
- `GET /users?q=` scans the table, which is fine for one cabinet.
- Patient notes keep only the latest version. Add an append-only history table if you need an audit trail of clinical edits.
