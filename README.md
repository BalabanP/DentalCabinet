# Medical Cabinet

| Folder | What it is |
|---|---|
| `backend/` | Serverless AWS backend (visitors, appointments, reminders). See `backend/README.md` |
| `chat-server/` | Python service that lets an LLM use the backend as tools (holds the API key) |
| `frontend/` | React chat UI with Cognito sign-in |
| `frontend-admin/` | React admin UI (calendar, visitors, settings, message log), English and Romanian |

```
Locally:  Browser --/api--> chat-server (uvicorn :8100, streams) --HTTP--> local backend (:8000, fake AWS)

On AWS:   Browser --HTTPS--> API Gateway (verifies the Cognito token)
                                |-- POST /chat --> ChatFunction --invoke--> ApiFunction (the backend)
                                |                       '--> LLM (API key from Secrets Manager)
                                '-- everything else --> ApiFunction
          UI files: S3 + CloudFront (optional, DeployFrontend=true)
```

The browser never sees the LLM key. The chat server gives the model a fixed set of tools. **Every change** (create, edit, delete, book, cancel, edit notes) pauses and shows an Approve / Reject card first. Reads run immediately. On AWS the chat Lambda calls the backend Lambda directly and passes along the user's verified identity, so the assistant can only do what the signed-in person's role allows.

## Run everything locally

Three terminals (PowerShell). Everything runs on fake in-memory AWS, so nothing real is touched.

**1. Backend with demo data** (port 8000):
```powershell
cd backend
..\.venv\Scripts\python.exe local\server.py 8000 --seed
```

**2. Chat server** (port 8100). First time only: create the key file.
```powershell
cd chat-server
copy .env.example .env      # then open .env and paste your ANTHROPIC_API_KEY
..\.venv\Scripts\python.exe -m pip install -r src\requirements.txt
..\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir src --port 8100
```

**3. UI** (port 5173). Needs [Node.js LTS](https://nodejs.org) (`winget install OpenJS.NodeJS.LTS`, then a new terminal). If PowerShell blocks `npm`, use `npm.cmd`:
```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```
Open http://localhost:5173. Without `.env.production` the UI runs in local mode: no sign-in.

Things to try: "Who is booked in the next days?", "Find a free 30 min slot next Monday and book Ana Pop", "Cancel Mihai Popescu's appointment", "Show Ana's notes".

## Admin UI (calendar, visitors, settings)

A conventional point-and-click interface, no chat or LLM involved. It talks straight to the backend API.

- **Calendar:** week, day and month views. Click an empty slot to book (the dialog suggests free times), drag a visit to reschedule it, drag its bottom edge to change its length, click it for details (reschedule, mark completed or no-show, cancel, see reminders and delivery log). Non-working hours are shaded. An overlap asks before forcing it.
- **Visitors:** search, add, edit; each visitor has their appointments and, for clinicians and admins, the clinical notes.
- **Messages:** which email / SMS reminders were sent or failed for the appointments of a period.
- **Settings:** reminder times, channels, timezone, cabinet name; working hours and slot length (admins edit, others read).
- **Message texts** (Settings, "Texte mesaje"): edit the subject and text of the email and, separately, the SMS, for each kind of message (confirmation, reminder, rescheduled, cancelled) and each language (Romanian, English). Click a placeholder such as `{name}` to insert it. A live preview shows the real designed email and the SMS, with its length in SMS parts. A customised field can be reset to the built-in text, and anything left untouched keeps following the built-in text.
- English / Romanian switch, light and dark theme.

Run locally (the backend from step 1 above must be running):
```powershell
cd frontend-admin
npm.cmd install
npm.cmd run dev
```
Open http://localhost:5174. Without `.env.production` it runs in local mode: no sign-in, everyone is an admin.

## Deploy the chat to AWS

Do the backend deploy first (see `backend/README.md`), then:

1. **Build and deploy.** The chat is **off by default**: add `DeployChat=true` to `parameter_overrides` in `backend/samconfig.toml` to create the chat Lambda and its secret (the secret costs about $0.40/month). Add `DeployFrontend=true` to `parameter_overrides` in `backend/samconfig.toml` if you want S3 + CloudFront to host the UI (CloudFront can take about 5 minutes to create). Optionally set `ChatEnablePatientNotes=true`.
   ```powershell
   cd backend
   sam build
   sam deploy
   ```
2. **Store the LLM key in Secrets Manager** (never in a file). Use the `ChatApiKeySecretArn` stack output:
   ```powershell
   aws secretsmanager put-secret-value --secret-id <ChatApiKeySecretArn> --secret-string "sk-ant-..." --region eu-central-1
   ```
   Lambdas cache the key per warm container, so if you change it later, redeploy or wait for old containers to recycle.
3. **Create a staff user** and add it to a group (`admin`, `clinician` or `reception`) as in the backend README.
4. **Build the UI with your stack's values.** Copy `frontend\.env.example` to `frontend\.env.production`, fill in `ApiUrl`, `UserPoolId` and `UserPoolClientId`, then:
   ```powershell
   cd frontend
   npm.cmd run build
   ```
5. **Upload it** (needs `DeployFrontend=true`; the bucket and distribution ids are stack outputs):
   ```powershell
   aws s3 sync dist s3://<SiteBucketName> --delete --exclude "admin/*"
   aws cloudfront create-invalidation --distribution-id <SiteDistributionId> --paths "/*"
   ```
   Open the `SiteUrl` output and sign in.

### Deploy the admin UI to the same site
It is served from `/admin/` of the same CloudFront site (needs `DeployFrontend=true`), with the same sign-in.
```powershell
cd frontend-admin
copy .env.example .env.production      # fill in ApiUrl, UserPoolId, UserPoolClientId (same values as the chat UI)
npm.cmd run build
aws s3 sync dist s3://<SiteBucketName>/admin --delete
aws cloudfront create-invalidation --distribution-id <SiteDistributionId> --paths "/admin/*"
```
Open the `AdminSiteUrl` output. Keep the `--exclude "admin/*"` on the chat UI upload above, otherwise it deletes the admin UI.

### Differences from local
- **Answers arrive all at once** on AWS instead of streaming word by word. API Gateway HTTP APIs cannot stream, and a request is cut off after 30 seconds, so very long multi-step requests can fail. Break them up if so.
- **MFA:** if the stack was deployed with `RequireMfa=true`, new users must enrol an authenticator app, which the sign-in page cannot do yet. Use `RequireMfa=false` for now (the dev default), or enrol through the AWS CLI.
- The sign-in page handles first-login password change and TOTP codes.

## SMS through an Android phone (optional third channel)

Reminders can also go out as SMS from a spare Android phone running the open-source app
[SMS Gateway for Android](https://docs.sms-gate.app). The backend calls the gateway's API and the phone sends the
text from its own SIM, so the cost is your SIM plan. Google Messages for web is not used: it has no official API.

```
reminder due -> Sender Lambda -> api.sms-gate.app (cloud API) -> your phone -> SMS to the visitor
```

**Set up**
1. On the phone: install the app, allow the SMS permission, turn on **Cloud Server** and tap **Online**. The app then shows a **username and password**.
2. Store them in AWS (an encrypted Parameter Store entry, free). The name must be `/<stack name>/sms-gateway`; for dev:
   ```powershell
   '{"username":"PASTE-USER","password":"PASTE-PASSWORD"}' | Set-Content $env:TEMP\sms.json -Encoding ascii
   aws ssm put-parameter --name "/medical-cabinet-dev/sms-gateway" --type SecureString --value file://$env:TEMP/sms.json --overwrite --region eu-central-1
   Remove-Item $env:TEMP\sms.json
   ```
3. Deploy once (`sam build`, `sam deploy`) so the Sender Lambda gets permission to read it.
4. In the admin UI: **Settings, Reminders, Channels**, turn **SMS** on and save. Each visitor also has an SMS consent toggle (on by default).

**Good to know**
- Until step 2 is done, SMS messages are recorded as *skipped* and nothing is sent.
- In the Messages page, *Sent* for SMS means the gateway **accepted** the message. The phone then sends it, so it must be on, charged, with signal and internet. If it is offline, the gateway holds the message until it is back.
- Romanian letters (ă, â, î, ș, ț) are sent as plain letters (a, a, i, s, t) so a message fits fewer SMS parts.
- The text (visitor name, cabinet, date, time; never medical notes) passes through the gateway's cloud service, a free community service whose uptime is not guaranteed. The app supports a self-hosted "private server" if you ever want that.
- Local development prints SMS in the backend console instead of sending anything.

### Visitors answering by SMS (CONFIRM / CANCEL)

The reminder SMS ends with "Reply CONFIRM or CANCEL." (Romanian: "Raspundeti CONFIRM sau ANULEAZA."). When the phone receives
the answer, the gateway app calls a web address of your backend (a *webhook*):

```
visitor replies -> your phone -> SMS Gateway app -> POST /webhooks/sms/<secret> -> backend
```

| The visitor sends | What happens |
|---|---|
| CONFIRM, DA, OK, YES, CONFIRMAT | The visit is marked **confirmed** (green tick in the calendar, agenda and visit window). |
| CANCEL, ANULEAZA, ANULEZ | The visit is **cancelled**, the slot is free again and the visitor gets the usual cancellation message. |
| Anything else, a longer message, or a number not in the visitor list | Nothing changes. It appears in **Messages, Replies that need attention** (and a red number on the Messages tab) for staff. |
| A reply from a visitor with **two or more** upcoming visits | Not guessed. It goes to the same list. |

A visit that is today or tomorrow and still unconfirmed shows an amber **?** in the calendar and "Awaiting confirmation" in the agenda.
Moving a visit to another time clears its confirmation. Staff can also press **Mark as confirmed** in the visit window (for example after a phone call).
"No" / "Nu" alone are not treated as CANCEL (it may mean "move it"), so staff read those.

**Set up (once, after the SMS gateway works)**
1. Make a secret and store it next to the gateway login (name `/<stack name>/sms-webhook`):
   ```powershell
   $b = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
   $token = ([BitConverter]::ToString($b) -replace '-','').ToLower()
   (@{ token = $token } | ConvertTo-Json -Compress) | Set-Content $env:TEMP\hook.json -Encoding ascii
   aws ssm put-parameter --name "/medical-cabinet-dev/sms-webhook" --type SecureString --value file://$env:TEMP/hook.json --overwrite --region eu-central-1
   Remove-Item $env:TEMP\hook.json
   ```
2. Deploy (`sam build`, `sam deploy`). The API URL is in the stack outputs.
3. Tell the gateway where to send received SMS (use your own login and API URL):
   ```powershell
   $url = "https://YOUR-API-ID.execute-api.eu-central-1.amazonaws.com/webhooks/sms/$token"
   (@{ url = $url; event = "sms:received" } | ConvertTo-Json -Compress) | Set-Content $env:TEMP\wh.json -Encoding ascii
   curl.exe -u "USERNAME:PASSWORD" -H "Content-Type: application/json" -d "@$env:TEMP\wh.json" https://api.sms-gate.app/3rdparty/v1/webhooks
   Remove-Item $env:TEMP\wh.json
   ```
   List them with `curl.exe -u "USERNAME:PASSWORD" https://api.sms-gate.app/3rdparty/v1/webhooks`.
4. Send a reminder to your own number, reply CONFIRM, and check the visit in the calendar.

**Good to know**
- The secret in the URL is what protects the endpoint; anyone without it gets "not found". Keep the URL private.
  The gateway can also sign requests (app: Settings, Webhooks, Signing Key). Only add `"signing_key"` to the parameter
  if you know your gateway mode signs, because once it is set every unsigned call is refused.
- The gateway retries for about two days until it gets an answer. Each message is applied once.
- The text of a reply is stored only when staff need to read it, for 90 days. Replies that were applied (CONFIRM / CANCEL) are not stored.
- Without step 1 the endpoint answers "not found" and nothing is read; everything else keeps working.
- Locally: `python local\server.py 8000 --seed` prints a PowerShell line that fakes a visitor's CONFIRM.
- Reminder texts you customised in the admin UI keep your wording. Add the reply sentence yourself if you want it.

## Cost switches (all off by default)
| Parameter | Turns on | Costs |
|---|---|---|
| `DeployChat=true` | Chat Lambda + Secrets Manager secret | about $0.40/month (secret) plus model usage |
| `UseCustomKmsKey=true` | Tables encrypted with your own KMS key | about $1/month. Off = DynamoDB's free AWS-owned key |
| `DeployFrontend=true` | S3 + CloudFront website | pennies |

Flipping `UseCustomKmsKey` on an existing stack re-encrypts the tables, so do it when nobody is using the app. A KMS key from an earlier deploy is kept (so data is never lost) until you schedule its deletion yourself.

## Notes
- **Patient notes go to the LLM provider.** Locally `ENABLE_PATIENT_NOTES=false` in `chat-server/.env` keeps them out. On AWS the `ChatEnablePatientNotes` parameter defaults to `false`. Use only fake data until you have decided where real patient data may be processed (provider terms, region, consent).
- Tests: `backend` (`pytest`, 61 tests) and `chat-server` (`pytest`, 28 tests). The two UIs have no automated tests yet (they type-check and build).
- **Using the local chat against the deployed API:** set `API_BASE_URL` in `chat-server/.env` to the `ApiUrl` output and send a valid token. Normally not needed; use the deployed chat instead.
