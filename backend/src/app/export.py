"""The "Export patient" feature: a PDF summary of everything known about one patient.

The PDF holds the details, the clinical notes, the visit history and the list of attached files.
The admin UI puts it in a ZIP together with the files themselves (downloaded straight from S3).
"""

import base64
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fpdf import FPDF

from . import documents, repositories as repo
from .errors import NotFound
from .timeutil import parse_iso, utc_now

FONTS = Path(__file__).with_name("fonts")  # DejaVu Sans: has the Romanian letters ă â î ș ț

LABELS = {
    "en": {
        "title": "Patient record", "exported": "Exported", "details": "Details", "name": "Name",
        "phone": "Phone", "email": "E-mail", "language": "Language", "reminders": "Reminders",
        "by_email": "e-mail", "by_sms": "SMS", "none": "none", "since": "Patient since",
        "notes": "Clinical notes", "no_notes": "No notes.", "updated": "Last changed",
        "visits": "Visits", "no_visits": "No visits.", "min": "min", "documents": "Attached documents",
        "no_documents": "No documents.", "in_zip": "The files are in the \"documents\" folder of this ZIP.",
        "page": "Page", "lang_en": "English", "lang_ro": "Romanian",
        "scheduled": "scheduled", "completed": "completed", "no_show": "did not come", "cancelled": "cancelled",
        "confirmed": "confirmed",
    },
    "ro": {
        "title": "Fișa pacientului", "exported": "Exportat", "details": "Date personale", "name": "Nume",
        "phone": "Telefon", "email": "E-mail", "language": "Limba", "reminders": "Reamintiri",
        "by_email": "e-mail", "by_sms": "SMS", "none": "niciuna", "since": "Pacient din",
        "notes": "Note clinice", "no_notes": "Nicio notă.", "updated": "Ultima modificare",
        "visits": "Vizite", "no_visits": "Nicio vizită.", "min": "min", "documents": "Documente atașate",
        "no_documents": "Niciun document.", "in_zip": "Fișierele sunt în dosarul \"documente\" din acest ZIP.",
        "page": "Pagina", "lang_en": "Engleză", "lang_ro": "Română",
        "scheduled": "programată", "completed": "efectuată", "no_show": "neprezentat", "cancelled": "anulată",
        "confirmed": "confirmată",
    },
}


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-") or "patient"


def _size(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} KB"


class _Pdf(FPDF):
    footer_text = ""

    def footer(self):
        self.set_y(-14)
        self.set_font("DejaVu", "", 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 6, f"{self.footer_text}   {self.page_no()}", align="R")


def summary_pdf(user_id: str, lang: str = "ro") -> bytes:
    L = LABELS.get(lang, LABELS["ro"])
    user = repo.get_user(user_id)
    if not user:
        raise NotFound("user not found")
    settings = repo.get_reminder_settings()
    tz = ZoneInfo(settings.timezone)
    local = lambda iso: parse_iso(iso).astimezone(tz)  # noqa: E731
    patient = repo.get_patient(user_id) or {}
    visits = sorted(repo.query_appointments_by_user(user_id), key=lambda a: a["start_at"], reverse=True)
    docs = documents.list_ready(user_id)

    pdf = _Pdf(format="A4")
    pdf.add_font("DejaVu", "", str(FONTS / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(FONTS / "DejaVuSans-Bold.ttf"))
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=20)
    pdf.footer_text = f"{user['name']} · {L['page']}"
    pdf.add_page()

    def heading(text: str):
        pdf.ln(4)
        pdf.set_font("DejaVu", "B", 12)
        pdf.set_text_color(15, 90, 90)
        pdf.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(200, 210, 210)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)
        pdf.set_text_color(20, 20, 20)

    def row(label: str, value: str):
        pdf.set_font("DejaVu", "B", 10)
        pdf.cell(42, 6, label)
        pdf.set_font("DejaVu", "", 10)
        pdf.multi_cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

    def text(value: str, size: int = 10):
        pdf.set_font("DejaVu", "", size)
        pdf.multi_cell(0, 5.5, value, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 5, settings.cabinet_name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "B", 20)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 11, f"{L['title']}: {user['name']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 5, f"{L['exported']}: {utc_now().astimezone(tz):%d.%m.%Y %H:%M}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(20, 20, 20)

    heading(L["details"])
    row(L["name"], user["name"])
    row(L["phone"], user.get("phone") or "—")
    row(L["email"], user.get("email") or "—")
    row(L["language"], L.get(f"lang_{user.get('locale', 'ro')}", user.get("locale", "")))
    channels = [L["by_email"]] if user.get("notify_email", True) and user.get("email") else []
    if user.get("notify_sms", True):
        channels.append(L["by_sms"])
    row(L["reminders"], ", ".join(channels) or L["none"])
    if user.get("created_at"):
        row(L["since"], f"{local(user['created_at']):%d.%m.%Y}")

    heading(L["notes"])
    if patient.get("notes", "").strip():
        if patient.get("updated_at"):
            pdf.set_text_color(110, 110, 110)
            text(f"{L['updated']}: {local(patient['updated_at']):%d.%m.%Y %H:%M}", 9)
            pdf.set_text_color(20, 20, 20)
            pdf.ln(1)
        text(patient["notes"])
    else:
        text(L["no_notes"])

    heading(L["visits"])
    if not visits:
        text(L["no_visits"])
    for a in visits:
        start = local(a["start_at"])
        status = L.get(a["status"], a["status"])
        if a["status"] == "scheduled" and a.get("confirmed_at"):
            status += f", {L['confirmed']}"
        line = f"{start:%d.%m.%Y  %H:%M}   ·   {a['duration_minutes']} {L['min']}   ·   {status}"
        if a.get("internal_note"):
            line += f"\n      {a['internal_note']}"
        text(line)

    heading(L["documents"])
    if not docs:
        text(L["no_documents"])
    else:
        for d in docs:
            when = f"{local(d['uploaded_at']):%d.%m.%Y}" if d.get("uploaded_at") else ""
            text(f"{d['name']}   ·   {_size(d['size'])}   ·   {when}")
        pdf.ln(2)
        pdf.set_text_color(110, 110, 110)
        text(L["in_zip"], 9)

    return bytes(pdf.output())


def export_bundle(user_id: str, lang: str = "ro") -> dict:
    """What the browser needs to build the ZIP: the PDF and a short-lived link for every file."""
    user = repo.get_user(user_id)
    if not user:
        raise NotFound("user not found")
    pdf = summary_pdf(user_id, lang)
    files = []
    for d in documents.list_ready(user_id):
        item = documents.get_ready(user_id, d["doc_id"])
        files.append({"name": d["name"], "size": d["size"], "url": documents.download_url(item)})
    base = f"{slug(user['name'])}-{datetime.now(ZoneInfo(repo.get_reminder_settings().timezone)):%Y-%m-%d}"
    return {
        "filename": base,
        "summary_pdf": base64.b64encode(pdf).decode(),
        "documents_folder": "documente" if lang == "ro" else "documents",
        "documents": files,
    }
