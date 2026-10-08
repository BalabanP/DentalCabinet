"""End-to-end smoke test against the running local server: python local/smoke.py [port]"""

import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8000}"


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


status, user = call("POST", "/users", {"name": "Ana Pop", "email": "ana@example.com", "phone": "+40712345678", "locale": "ro"})
print("create user      ", status, user["user_id"])

print("save notes       ", call("PUT", f"/users/{user['user_id']}/patient", {"notes": "Test diagnosis"})[0])

start = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
status, appt = call("POST", "/appointments", {"user_id": user["user_id"], "start_at": start})
print("book in 5 min    ", status, [r["key"] for r in appt["reminders"]], "<- 3-min reminder fires in ~2 min")
print("confirmation should appear in the server console now")
