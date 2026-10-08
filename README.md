# Medical Cabinet

| Folder | What it is |
|---|---|
| `backend/` | Serverless AWS backend (visitors, appointments, reminders). See `backend/README.md` |
| `chat-server/` | Python service that lets an LLM use the backend as tools (holds the API key) |
| `frontend/` | React chat UI with Cognito sign-in |

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

## Deploy the chat to AWS

Do the backend deploy first (see `backend/README.md`), then:

1. **Build and deploy.** Add `DeployFrontend=true` to `parameter_overrides` in `backend/samconfig.toml` if you want S3 + CloudFront to host the UI (CloudFront can take about 5 minutes to create). Optionally set `ChatEnablePatientNotes=true`.
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
   aws s3 sync dist s3://<SiteBucketName> --delete
   aws cloudfront create-invalidation --distribution-id <SiteDistributionId> --paths "/*"
   ```
   Open the `SiteUrl` output and sign in.

### Differences from local
- **Answers arrive all at once** on AWS instead of streaming word by word. API Gateway HTTP APIs cannot stream, and a request is cut off after 30 seconds, so very long multi-step requests can fail. Break them up if so.
- **MFA:** if the stack was deployed with `RequireMfa=true`, new users must enrol an authenticator app, which the sign-in page cannot do yet. Use `RequireMfa=false` for now (the dev default), or enrol through the AWS CLI.
- The sign-in page handles first-login password change and TOTP codes.

## Notes
- **Patient notes go to the LLM provider.** Locally `ENABLE_PATIENT_NOTES=false` in `chat-server/.env` keeps them out. On AWS the `ChatEnablePatientNotes` parameter defaults to `false`. Use only fake data until you have decided where real patient data may be processed (provider terms, region, consent).
- Tests: `backend` (`pytest`, 61 tests) and `chat-server` (`pytest`, 28 tests). The UI has no automated tests yet.
- **Using the local chat against the deployed API:** set `API_BASE_URL` in `chat-server/.env` to the `ApiUrl` output and send a valid token. Normally not needed; use the deployed chat instead.
