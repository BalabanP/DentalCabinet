"""HTML layout of the reminder e-mails.

Email clients ignore most modern CSS, so this uses tables and inline styles only. Every piece of text that
comes from a template or a visitor is escaped. Colours: teal for normal messages, red for cancellations.
"""

from html import escape

ACCENT = {"confirmation": "#0f7b8a", "reminder": "#0f7b8a", "rescheduled": "#c2750a", "cancelled": "#b42318"}

LABELS = {
    "en": {"date": "Date", "time": "Time", "footer": "You are receiving this message because you have an appointment at {cabinet}."},
    "ro": {"date": "Data", "time": "Ora", "footer": "Primiți acest mesaj deoarece aveți o programare la {cabinet}."},
}

FONT = "Arial, Helvetica, sans-serif"


def _paragraphs(text: str) -> str:
    """Blank line = new paragraph; a single line break stays a line break."""
    out = []
    for block in text.strip().split("\n\n"):
        block = block.strip()
        if block:
            out.append(
                f'<p style="margin:0 0 16px;font:16px/1.6 {FONT};color:#1b2430;">'
                + escape(block).replace("\n", "<br>")
                + "</p>"
            )
    return "".join(out)


def render_email(
    *, kind: str, locale: str, subject: str, body: str, cabinet: str, date: str, time: str, contact: str
) -> str:
    labels = LABELS.get(locale, LABELS["en"])
    accent = ACCENT.get(kind, "#0f7b8a")
    preheader = escape(" ".join(body.split())[:110])
    struck = "text-decoration:line-through;" if kind == "cancelled" else ""

    def detail(label: str, value: str) -> str:
        return (
            f'<td style="padding:14px 22px;vertical-align:top;">'
            f'<div style="font:700 11px {FONT};letter-spacing:.08em;text-transform:uppercase;color:#667085;">{escape(label)}</div>'
            f'<div style="font:700 20px {FONT};color:#1b2430;margin-top:4px;{struck}">{escape(value)}</div></td>'
        )

    contact_html = (
        f'<div style="font:600 14px {FONT};color:#1b2430;margin-bottom:6px;">{escape(contact)}</div>' if contact else ""
    )
    footer = escape(labels["footer"].replace("{cabinet}", cabinet))

    return f"""<!doctype html>
<html lang="{escape(locale)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f3f6f9;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f6f9;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border:1px solid #e3e8ef;border-radius:14px;overflow:hidden;">
<tr><td style="background:{accent};padding:22px 28px;font:700 20px/1.3 {FONT};color:#ffffff;">{escape(cabinet)}</td></tr>
<tr><td style="padding:28px 28px 12px;">
<h1 style="margin:0 0 18px;font:700 22px/1.3 {FONT};color:#1b2430;">{escape(subject)}</h1>
{_paragraphs(body)}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 18px;background:#f3f9fa;border:1px solid #cfe6ea;border-left:4px solid {accent};border-radius:10px;">
<tr>{detail(labels["date"], date)}{detail(labels["time"], time)}</tr>
</table>
</td></tr>
<tr><td style="padding:18px 28px;background:#f8fafc;border-top:1px solid #e3e8ef;font:13px/1.5 {FONT};color:#667085;">
{contact_html}{footer}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
