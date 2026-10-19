"""Local dev server: the real API + reminder-sender code running on in-memory fake AWS (moto).

No AWS account, no Docker. Data lives in memory and is lost when the process stops.
E-mail and SMS are printed to the console instead of being sent.
Auth is bypassed: every request acts as a user in the groups given by the `X-Dev-Groups`
header (default: admin). Never expose this server beyond localhost.

    python local/server.py [port] [--seed]     (--seed adds demo visitors and appointments)
"""

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252

# Force fake credentials so nothing can ever reach a real AWS account.
os.environ.update(
    AWS_DEFAULT_REGION="eu-central-1",
    AWS_ACCESS_KEY_ID="local",
    AWS_SECRET_ACCESS_KEY="local",
    USERS_TABLE="users",
    PATIENTS_TABLE="patients",
    APPOINTMENTS_TABLE="appointments",
    SETTINGS_TABLE="settings",
    NOTIFICATIONS_TABLE="notifications",
    DOCUMENTS_TABLE="documents",
    DOCUMENTS_BUCKET="patient-docs",
    SCHEDULER_GROUP="reminders",
    SCHEDULER_ROLE_ARN="arn:aws:iam::123456789012:role/local",
    SES_FROM_ADDRESS="clinic@local.test",
    POWERTOOLS_LOG_LEVEL="WARNING",
)

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

TABLES = [
    ("users", "user_id", None, [("email-index", "email", None)]),
    ("patients", "user_id", None, []),
    ("appointments", "appointment_id", None,
     [("user-index", "user_id", "start_at"), ("day-index", "day", "start_at")]),
    ("settings", "setting_id", None, []),
    ("notifications", "appointment_id", "sk", []),
    ("documents", "user_id", "doc_id", []),
]


def create_tables() -> None:
    ddb = boto3.client("dynamodb")
    for name, hash_key, range_key, gsis in TABLES:
        attrs = {hash_key} | ({range_key} if range_key else set())
        key = [{"AttributeName": hash_key, "KeyType": "HASH"}]
        if range_key:
            key.append({"AttributeName": range_key, "KeyType": "RANGE"})
        indexes = []
        for idx, h, r in gsis:
            attrs |= {h} | ({r} if r else set())
            idx_key = [{"AttributeName": h, "KeyType": "HASH"}]
            if r:
                idx_key.append({"AttributeName": r, "KeyType": "RANGE"})
            indexes.append({"IndexName": idx, "KeySchema": idx_key, "Projection": {"ProjectionType": "ALL"}})
        kwargs = dict(
            TableName=name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in sorted(attrs)],
            KeySchema=key,
        )
        if indexes:
            kwargs["GlobalSecondaryIndexes"] = indexes
        ddb.create_table(**kwargs)


def create_queue_and_scheduler() -> None:
    sqs = boto3.client("sqs")
    url = sqs.create_queue(QueueName="reminders")["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    os.environ["REMINDER_QUEUE_URL"] = url
    os.environ["REMINDER_QUEUE_ARN"] = arn
    boto3.client("scheduler").create_schedule_group(Name="reminders")
    boto3.client("s3").create_bucket(
        Bucket="patient-docs", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def install_console_providers() -> None:
    from app import notifications

    def fake_email(to, msg):
        print(f"\n[{stamp()}] EMAIL    to={to}\n  subject: {msg.subject}\n  {msg.text}\n", flush=True)
        return "local-email-id"

    def fake_sms(phone, msg):
        print(f"\n[{stamp()}] SMS      to={phone}\n  {notifications.sms_text(msg.sms)}\n", flush=True)
        return "local-sms-id"

    notifications.send_email = fake_email
    notifications.send_sms = fake_sms
    notifications.sms_enabled = lambda: True


def seed_settings() -> None:
    """Defaults (3 days / 1 day / 2 hours) plus a 3-minute reminder so you can watch one fire."""
    from app import repositories as repo
    from app.models import ReminderRule, ReminderSettings

    settings = ReminderSettings(
        cabinet_name="Local Dev Cabinet",
        cabinet_phone="+40211234567",
        timezone="Europe/Bucharest",
        reminders=[ReminderRule(minutes_before=m) for m in (4320, 1440, 120, 3)],
    )
    repo.put_reminder_settings(settings)


def seed_demo_data() -> None:
    """A few visitors, one appointment and a clinical note, so the chat has something to talk about."""
    from datetime import timedelta

    from app import repositories as repo, services
    from app.models import AppointmentIn, UserIn
    from app.timeutil import utc_now

    people = [
        ("Ana Pop", "ana.pop@example.com", "+40712345001", "ro"),
        ("Mihai Popescu", "mihai.popescu@example.com", "+40712345002", "ro"),
        ("Elena Ionescu", "elena.ionescu@example.com", "+40712345003", "en"),
        ("Ivan Petrov", "ivan.petrov@example.com", "+40712345004", "en"),
    ]
    users = [repo.create_user(UserIn(name=n, email=e, phone=p, locale=loc).model_dump()) for n, e, p, loc in people]
    repo.upsert_patient(users[0]["user_id"], "Hypertension, follow-up every 3 months. Allergic to penicillin.", "seed")
    tz = repo.get_reminder_settings().tz
    day = (utc_now().astimezone(tz) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    while day.weekday() >= 5:  # the default working hours are Monday to Friday
        day += timedelta(days=1)
    services.book(AppointmentIn(user_id=users[0]["user_id"], start_at=day, duration_minutes=30))
    services.book(AppointmentIn(user_id=users[1]["user_id"], start_at=day + timedelta(hours=1), duration_minutes=45))


def scheduler_loop() -> None:
    """moto stores schedules but never fires them. Do what EventBridge Scheduler would do."""
    from app import clients

    group, queue = os.environ["SCHEDULER_GROUP"], os.environ["REMINDER_QUEUE_URL"]
    while True:
        try:
            for s in clients.scheduler().list_schedules(GroupName=group)["Schedules"]:
                full = clients.scheduler().get_schedule(Name=s["Name"], GroupName=group)
                at = re.fullmatch(r"at\((.+)\)", full["ScheduleExpression"]).group(1)
                if datetime.fromisoformat(at).replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
                    clients.sqs().send_message(QueueUrl=queue, MessageBody=full["Target"]["Input"])
                    clients.scheduler().delete_schedule(Name=s["Name"], GroupName=group)
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] {exc!r}", flush=True)
        time.sleep(5)


def sender_loop() -> None:
    from app import clients
    from handlers import sender

    queue = os.environ["REMINDER_QUEUE_URL"]
    while True:
        try:
            res = clients.sqs().receive_message(QueueUrl=queue, MaxNumberOfMessages=5, WaitTimeSeconds=1)
            for m in res.get("Messages", []):
                try:
                    sender.process(json.loads(m["Body"]))
                    clients.sqs().delete_message(QueueUrl=queue, ReceiptHandle=m["ReceiptHandle"])
                except Exception as exc:  # noqa: BLE001  (left on the queue, like real SQS retries)
                    print(f"[sender] failed: {exc!r}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[sender] {exc!r}", flush=True)
        time.sleep(1)


class LambdaContext:
    function_name = "local"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-central-1:123456789012:function:local"
    aws_request_id = "local"


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Dev-Groups")

    def _files(self, url) -> None:
        """Stand-in for S3's signed links: PUT stores a patient file, GET hands it back."""
        from urllib.parse import unquote

        from app import clients

        key = unquote(url.path[len("/_files/"):])
        s3 = clients.s3()
        if self.command == "PUT":
            length = int(self.headers.get("Content-Length") or 0)
            s3.put_object(Bucket="patient-docs", Key=key, Body=self.rfile.read(length),
                          ContentType=self.headers.get("Content-Type", "application/octet-stream"))
            self.send_response(200)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            obj = s3.get_object(Bucket="patient-docs", Key=key)
        except Exception:  # noqa: BLE001
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        data = obj["Body"].read()
        self.send_response(200)
        self.send_header("Content-Type", obj.get("ContentType", "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        from handlers import api

        url = urlsplit(self.path)
        if url.path.startswith("/_files/"):
            return self._files(url)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else None
        groups = (self.headers.get("X-Dev-Groups") or "admin").replace(",", " ")
        query = dict(parse_qsl(url.query))
        event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": url.path,
            "rawQueryString": url.query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "queryStringParameters": query or None,
            "requestContext": {
                "accountId": "1", "apiId": "local", "domainName": "localhost", "domainPrefix": "local",
                "http": {"method": self.command, "path": url.path, "protocol": "HTTP/1.1",
                         "sourceIp": "127.0.0.1", "userAgent": self.headers.get("User-Agent", "")},
                "requestId": "local", "routeKey": "$default", "stage": "$default", "time": "", "timeEpoch": 0,
                "authorizer": {"jwt": {"claims": {"sub": "local-dev", "cognito:groups": f"[{groups}]"},
                                       "scopes": None}},
            },
            "body": body,
            "isBase64Encoded": False,
        }
        res = api.handler(event, LambdaContext())
        payload = (res.get("body") or "").encode()
        self.send_response(res["statusCode"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[{stamp()}] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}", flush=True)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = int(args[0]) if args else 8000
    os.environ.setdefault("SMS_WEBHOOK_TOKEN", "local-dev-token-0123456789")  # lets you fake a visitor's SMS reply
    os.environ["LOCAL_FILES_BASE"] = f"http://127.0.0.1:{port}/_files"  # patient files stay in memory too
    mock = mock_aws()
    mock.start()
    create_tables()
    create_queue_and_scheduler()
    install_console_providers()
    seed_settings()
    if "--seed" in sys.argv:
        seed_demo_data()
        print("Seeded demo visitors, one note and two appointments.", flush=True)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=sender_loop, daemon=True).start()
    print(f"Local API on http://127.0.0.1:{port}  (fake AWS, in-memory; Ctrl+C to stop)", flush=True)
    body = '{"event":"sms:received","payload":{"messageId":"t1","sender":"+40712345001","message":"CONFIRM"}}'
    print("Pretend a visitor replied (a seeded visitor's phone is +40712345001):", flush=True)
    print(
        f"  Invoke-RestMethod -Method Post -Uri http://127.0.0.1:{port}/webhooks/sms/{os.environ['SMS_WEBHOOK_TOKEN']} "
        f"-ContentType application/json -Body '{body}'\n",
        flush=True,
    )
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
