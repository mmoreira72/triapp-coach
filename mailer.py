"""Email through Resend (https://resend.com): password reset links and notices to the coach.

  TRIAPP_RESEND_KEY   a Resend API key (sending access is enough)
  TRIAPP_MAIL_FROM    sender, default "Tri Squad <nao-responder@laloop.ai>" (the domain must be verified in Resend)
  TRIAPP_PUBLIC_URL   address used in links, default https://tri.laloop.ai

Nothing is sent without a key; send() returns False then, and on any error (callers carry on).
"""

import os, json, urllib.request, html

PUBLIC_URL = os.environ.get("TRIAPP_PUBLIC_URL", "https://tri.laloop.ai").rstrip("/")


def configured():
    return bool(os.environ.get("TRIAPP_RESEND_KEY"))


def send(to, subject, text, button=None):
    """Plain email with an optional (label, url) button. True when Resend accepted it."""
    if not configured() or not to:
        return False
    body = "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n"))
    if button:
        label, url = button
        body += (
            f'<p><a href="{html.escape(url)}" style="display:inline-block;padding:10px 16px;border-radius:8px;'
            f'background:#111311;color:#fff;text-decoration:none;font-weight:600">{html.escape(label)}</a></p>'
            f'<p style="color:#666;font-size:13px">Se o botão não funcionar, copia este endereço: {html.escape(url)}</p>'
        )
    payload = {
        "from": os.environ.get("TRIAPP_MAIL_FROM", "Tri Squad <nao-responder@laloop.ai>"),
        "to": [to] if isinstance(to, str) else list(to),
        "subject": subject,
        "text": text + (f"\n\n{button[0]}: {button[1]}" if button else ""),
        "html": f'<div style="font-family:system-ui,-apple-system,sans-serif;font-size:15px;line-height:1.5;color:#111">{body}</div>',
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['TRIAPP_RESEND_KEY']}",
            "Content-Type": "application/json",
            "User-Agent": "triapp/1.0 (+https://tri.laloop.ai)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception:
        return False
