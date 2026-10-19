"""Files attached to a patient (photos, Word, PDF). Stored in a private S3 bucket.

The browser never sends a file through the API (it would hit the 6 MB Lambda limit). Instead:
1. it asks for an upload link (`start_upload`), which records the file as "pending";
2. it PUTs the file straight to S3 with that link, signed for that exact type and size;
3. it calls `complete_upload`, which checks the file really arrived and marks it "ready".
Downloads work the same way: a short-lived signed link to the object.
"""

import os
import re
import uuid
from datetime import timedelta
from urllib.parse import quote

from botocore.exceptions import ClientError

from . import clients, repositories as repo
from .config import get_config
from .errors import BadRequest, NotFound
from .models import DocumentUploadIn
from .timeutil import to_iso, utc_now

MAX_BYTES = 20 * 1024 * 1024
LINK_SECONDS = 300  # signed upload / download links are valid for 5 minutes
PENDING_HOURS = 24  # an upload that is never completed disappears from the table after a day

# What may be attached, and the file endings that go with each type.
ALLOWED = {
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "application/pdf": (".pdf",),
    "application/msword": (".doc",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx",),
}


def _bucket() -> str:
    return get_config().documents_bucket


def _local_base() -> str | None:
    """Local development only: files go to the local server instead of S3 links."""
    return os.environ.get("LOCAL_FILES_BASE")


def safe_name(filename: str) -> str:
    """Last path part only, odd characters replaced, so it is safe in an S3 key and a download name."""
    base = re.split(r"[\\/]", filename.strip())[-1]
    base = re.sub(r"[^\w.\- ()]+", "_", base, flags=re.UNICODE).strip(" .") or "file"
    stem, dot, ext = base.rpartition(".")
    if not dot:
        return base[:120]
    return f"{stem[: 120 - len(ext) - 1]}.{ext.lower()}"


def _public(item: dict) -> dict:
    return {k: item[k] for k in ("doc_id", "name", "content_type", "size", "uploaded_at", "uploaded_by") if k in item}


def start_upload(user_id: str, data: DocumentUploadIn, actor: str) -> dict:
    if not repo.get_user(user_id):
        raise NotFound("user not found")
    content_type = data.content_type.lower().strip()
    if content_type not in ALLOWED:
        raise BadRequest("only JPG, PNG, PDF and Word files can be attached")
    name = safe_name(data.filename)
    if not name.lower().endswith(ALLOWED[content_type]):
        raise BadRequest("the file ending does not match its type")
    if data.size > MAX_BYTES:
        raise BadRequest(f"a file can be at most {MAX_BYTES // (1024 * 1024)} MB")

    doc_id = uuid.uuid4().hex
    key = f"patients/{user_id}/{doc_id}/{name}"
    now = utc_now()
    item = {
        "user_id": user_id,
        "doc_id": doc_id,
        "name": name,
        "content_type": content_type,
        "size": data.size,
        "s3_key": key,
        "status": "pending",
        "uploaded_by": actor,
        "created_at": to_iso(now),
        "ttl": int((now + timedelta(hours=PENDING_HOURS)).timestamp()),
    }
    repo.put_document(item)

    if _local_base():
        url = f"{_local_base()}/{quote(key)}"
    else:
        # Type and size are part of the signature: S3 refuses a different file sent with this link.
        url = clients.s3().generate_presigned_url(
            "put_object",
            Params={"Bucket": _bucket(), "Key": key, "ContentType": content_type, "ContentLength": data.size},
            ExpiresIn=LINK_SECONDS,
        )
    return {"document": _public(item), "upload": {"url": url, "method": "PUT", "headers": {"Content-Type": content_type}}}


def complete_upload(user_id: str, doc_id: str) -> dict:
    item = repo.get_document(user_id, doc_id)
    if not item:
        raise NotFound("document not found")
    if item["status"] == "ready":
        return _public(item)
    try:
        head = clients.s3().head_object(Bucket=_bucket(), Key=item["s3_key"])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            raise BadRequest("the file has not arrived yet; upload it again") from exc
        raise
    if head["ContentLength"] != item["size"] or head["ContentLength"] > MAX_BYTES:
        delete(user_id, doc_id)
        raise BadRequest("the uploaded file is not the one announced; upload it again")
    saved = repo.update_document(
        user_id, doc_id, {"status": "ready", "uploaded_at": to_iso(utc_now())}, remove=("ttl",)
    )
    return _public(saved)


def list_ready(user_id: str) -> list[dict]:
    docs = [d for d in repo.list_documents(user_id) if d.get("status") == "ready"]
    return [_public(d) for d in sorted(docs, key=lambda d: d.get("uploaded_at", ""), reverse=True)]


def download_url(item: dict) -> str:
    name = item["name"]
    if _local_base():
        return f"{_local_base()}/{quote(item['s3_key'])}?name={quote(name)}"
    disposition = f"attachment; filename=\"{name.encode('ascii', 'replace').decode()}\"; filename*=UTF-8''{quote(name)}"
    return clients.s3().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": _bucket(),
            "Key": item["s3_key"],
            "ResponseContentDisposition": disposition,
            "ResponseContentType": item["content_type"],
        },
        ExpiresIn=LINK_SECONDS,
    )


def get_ready(user_id: str, doc_id: str) -> dict:
    item = repo.get_document(user_id, doc_id)
    if not item or item.get("status") != "ready":
        raise NotFound("document not found")
    return item


def delete(user_id: str, doc_id: str) -> None:
    item = repo.get_document(user_id, doc_id)
    if not item:
        raise NotFound("document not found")
    clients.s3().delete_object(Bucket=_bucket(), Key=item["s3_key"])
    repo.delete_document(user_id, doc_id)


def delete_all(user_id: str) -> int:
    """Remove every file of a patient (used when the patient is deleted)."""
    items = repo.list_documents(user_id)
    for item in items:
        clients.s3().delete_object(Bucket=_bucket(), Key=item["s3_key"])
        repo.delete_document(user_id, item["doc_id"])
    return len(items)
