import base64
from datetime import timedelta

import boto3
import pytest

from app import repositories as repo, services
from test_api import USER, book, call, make_user

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(autouse=True)
def _any_hour(monkeypatch):
    monkeypatch.setattr(services, "_ensure_working_hours", lambda *a, **k: None)


def s3_keys():
    res = boto3.client("s3").list_objects_v2(Bucket="patient-docs")
    return sorted(o["Key"] for o in res.get("Contents", []))


def upload(uid, name="scan.pdf", content_type=PDF, data=b"%PDF-1.4 test", groups=("clinician",)):
    """What the browser does: ask for a link, PUT the bytes, then say it is done."""
    status, res = call("POST", f"/users/{uid}/documents",
                       {"filename": name, "content_type": content_type, "size": len(data)}, groups=groups)
    assert status == 201, res
    doc_id = res["document"]["doc_id"]
    key = repo.get_document(uid, doc_id)["s3_key"]
    boto3.client("s3").put_object(Bucket="patient-docs", Key=key, Body=data, ContentType=content_type)
    status, done = call("POST", f"/users/{uid}/documents/{doc_id}/complete", groups=groups)
    assert status == 200, done
    return done


# ---------- e-mail is optional ----------


def test_patient_can_be_created_without_email(aws):
    for body in ({"name": "Ion", "phone": "+40711111111"}, {"name": "Maria", "phone": "+40722222222", "email": ""}):
        status, user = call("POST", "/users", body)
        assert status == 201, user
        assert "email" not in user
    status, found = call("GET", "/users", query={"q": "ion"})
    assert status == 200 and [u["name"] for u in found["items"]] == ["Ion"]


def test_email_can_be_removed_and_added_again(aws):
    uid = make_user()["user_id"]
    status, user = call("PATCH", f"/users/{uid}", {"email": ""})
    assert status == 200 and "email" not in user
    status, user = call("PATCH", f"/users/{uid}", {"email": "New@Example.com"})
    assert status == 200 and user["email"] == "new@example.com"


def test_email_still_unique_when_given(aws):
    make_user()
    status, _ = call("POST", "/users", {**USER, "phone": "+40799999999"})
    assert status == 409


def test_invalid_email_is_still_rejected(aws):
    status, res = call("POST", "/users", {"name": "X", "phone": "+40711111111", "email": "not-an-email"})
    assert status == 422


# ---------- documents ----------


def test_upload_list_download_delete(aws):
    uid = make_user()["user_id"]
    status, start = call("POST", f"/users/{uid}/documents",
                         {"filename": "Radiografie ăș.pdf", "content_type": PDF, "size": 13}, groups=("clinician",))
    assert status == 201
    assert start["upload"]["method"] == "PUT" and "X-Amz-Signature" in start["upload"]["url"]
    assert start["upload"]["headers"] == {"Content-Type": PDF}
    assert call("GET", f"/users/{uid}/documents", groups=("clinician",))[1]["items"] == []  # pending: not shown

    doc = upload(uid, "xray.pdf")
    assert doc["name"] == "xray.pdf" and doc["size"] == len(b"%PDF-1.4 test") and doc["uploaded_by"] == "staff-1"
    items = call("GET", f"/users/{uid}/documents", groups=("clinician",))[1]["items"]
    assert [d["doc_id"] for d in items] == [doc["doc_id"]]
    assert "s3_key" not in items[0]

    status, link = call("GET", f"/users/{uid}/documents/{doc['doc_id']}/download", groups=("clinician",))
    assert status == 200 and "X-Amz-Signature" in link["url"] and "response-content-disposition" in link["url"]

    assert call("DELETE", f"/users/{uid}/documents/{doc['doc_id']}", groups=("clinician",))[0] == 204
    assert call("GET", f"/users/{uid}/documents", groups=("clinician",))[1]["items"] == []
    assert not any(doc["doc_id"] in k for k in s3_keys())


@pytest.mark.parametrize(
    "name,content_type,size",
    [
        ("virus.exe", "application/x-msdownload", 10),
        ("photo.pdf", "image/jpeg", 10),  # ending does not match the type
        ("big.pdf", PDF, 21 * 1024 * 1024),
    ],
)
def test_upload_rules(aws, name, content_type, size):
    uid = make_user()["user_id"]
    status, _ = call("POST", f"/users/{uid}/documents", {"filename": name, "content_type": content_type, "size": size})
    assert status == 400


def test_word_and_jpg_are_accepted(aws):
    uid = make_user()["user_id"]
    upload(uid, "consent.docx", DOCX, b"PK...")
    upload(uid, "tooth.JPG", "image/jpeg", b"\xff\xd8\xff")
    names = sorted(d["name"] for d in call("GET", f"/users/{uid}/documents")[1]["items"])
    assert names == ["consent.docx", "tooth.jpg"]


def test_complete_before_the_file_arrives_is_refused(aws):
    uid = make_user()["user_id"]
    _, start = call("POST", f"/users/{uid}/documents", {"filename": "a.pdf", "content_type": PDF, "size": 5})
    status, _ = call("POST", f"/users/{uid}/documents/{start['document']['doc_id']}/complete")
    assert status == 400


def test_a_different_file_than_announced_is_discarded(aws):
    uid = make_user()["user_id"]
    _, start = call("POST", f"/users/{uid}/documents", {"filename": "a.pdf", "content_type": PDF, "size": 5})
    doc_id = start["document"]["doc_id"]
    boto3.client("s3").put_object(Bucket="patient-docs", Key=repo.get_document(uid, doc_id)["s3_key"], Body=b"x" * 50)
    assert call("POST", f"/users/{uid}/documents/{doc_id}/complete")[0] == 400
    assert repo.get_document(uid, doc_id) is None and s3_keys() == []


def test_file_names_are_made_safe(aws):
    uid = make_user()["user_id"]
    doc = upload(uid, r"C:\Users\me\..\evil/../raport <1>.pdf")
    assert doc["name"] == "raport _1_.pdf"
    assert all(k.startswith(f"patients/{uid}/") for k in s3_keys())


def test_reception_cannot_see_or_add_documents(aws):
    uid = make_user()["user_id"]
    for method, path, body in [
        ("GET", f"/users/{uid}/documents", None),
        ("POST", f"/users/{uid}/documents", {"filename": "a.pdf", "content_type": PDF, "size": 5}),
        ("GET", f"/users/{uid}/export", None),
    ]:
        assert call(method, path, body, groups=("reception",))[0] == 403


def test_deleting_a_patient_deletes_their_files(aws):
    uid = make_user()["user_id"]
    upload(uid, "a.pdf")
    upload(uid, "b.pdf")
    assert len(s3_keys()) == 2
    assert call("DELETE", f"/users/{uid}")[0] == 204
    assert s3_keys() == []
    assert repo.list_documents(uid) == []


# ---------- export ----------


def test_export_has_a_pdf_summary_and_links_to_every_file(aws):
    uid = make_user(name="Ana Pop")["user_id"]
    call("PUT", f"/users/{uid}/patient", {"notes": "Alergie la penicilină. Șase plombe, țesut sănătos."})
    book(uid, timedelta(days=3))
    upload(uid, "xray.pdf")
    upload(uid, "consent.docx", DOCX, b"PK...")

    status, bundle = call("GET", f"/users/{uid}/export", query={"lang": "ro"}, groups=("clinician",))
    assert status == 200, bundle
    pdf = base64.b64decode(bundle["summary_pdf"])
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000
    assert bundle["filename"].startswith("ana-pop-")
    assert bundle["documents_folder"] == "documente"
    assert sorted(d["name"] for d in bundle["documents"]) == ["consent.docx", "xray.pdf"]
    assert all("X-Amz-Signature" in d["url"] for d in bundle["documents"])


def test_export_works_for_a_patient_with_nothing_yet(aws):
    status, user = call("POST", "/users", {"name": "Ion Gol", "phone": "+40711111111"})
    status, bundle = call("GET", f"/users/{user['user_id']}/export", query={"lang": "en"})
    assert status == 200 and bundle["documents"] == [] and bundle["documents_folder"] == "documents"
    assert base64.b64decode(bundle["summary_pdf"]).startswith(b"%PDF")


def test_export_of_unknown_patient_is_404(aws):
    assert call("GET", "/users/nope/export")[0] == 404
