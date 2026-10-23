"""Push notifications to the native app (Apple Push Notification service).

The app registers its device token after login (POST /api/push/register); tokens live in the app database with the user's
email, role and athlete. The pipeline's notify.py step and the app send through send(). Nothing happens until APNs is
configured with the key from the Apple Developer account:

  TRIAPP_APNS_KEY_PATH  path to the AuthKey_XXXXXXXXXX.p8 file (inside the container)
  TRIAPP_APNS_KEY_ID    the key's 10-character id
  TRIAPP_APNS_TEAM_ID   the developer team id
  TRIAPP_APNS_TOPIC     the app's bundle id (default ai.laloop.trisquad)
  TRIAPP_APNS_ENV       "development" (builds from Xcode) or "production" (TestFlight and App Store): tried first
  TRIAPP_APNS_SANDBOX_KEY_PATH / TRIAPP_APNS_SANDBOX_KEY_ID
                        optional second key for the sandbox, when the main key is limited to production (Apple's topic
                        specific keys cover one environment); without it the main key is used for both
"""

import os, sqlite3, time, json
from pathlib import Path

TOPIC = os.environ.get("TRIAPP_APNS_TOPIC", "ai.laloop.trisquad")
HOSTS = {"development": "https://api.sandbox.push.apple.com", "production": "https://api.push.apple.com"}
_jwt = {}  # env -> (token, issued at)


def _db():
    out = Path(os.environ.get("TRIAPP_OUT", Path(__file__).resolve().parent / "out"))
    con = sqlite3.connect(os.environ.get("TRIAPP_DB", str(out / "app.sqlite")))
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS push_devices (token TEXT PRIMARY KEY, email TEXT NOT NULL, role TEXT, athlete TEXT,
                   platform TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_seen TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.execute("CREATE TABLE IF NOT EXISTS push_sent (key TEXT PRIMARY KEY, sent_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    return con


def configured():
    return all(os.environ.get(k) for k in ("TRIAPP_APNS_KEY_PATH", "TRIAPP_APNS_KEY_ID", "TRIAPP_APNS_TEAM_ID"))


def _key(env):
    """(path, key id) used for an environment: the sandbox key when one is set, otherwise the main key."""
    if env == "development" and os.environ.get("TRIAPP_APNS_SANDBOX_KEY_PATH") and os.environ.get("TRIAPP_APNS_SANDBOX_KEY_ID"):
        return os.environ["TRIAPP_APNS_SANDBOX_KEY_PATH"], os.environ["TRIAPP_APNS_SANDBOX_KEY_ID"]
    return os.environ["TRIAPP_APNS_KEY_PATH"], os.environ["TRIAPP_APNS_KEY_ID"]


def register(token, user, platform="ios"):
    token = "".join(c for c in str(token) if c.isalnum())[:200]
    if len(token) < 32:
        raise ValueError("token inválido")
    con = _db()
    con.execute(
        """INSERT INTO push_devices(token,email,role,athlete,platform) VALUES(?,?,?,?,?)
           ON CONFLICT(token) DO UPDATE SET email=excluded.email, role=excluded.role, athlete=excluded.athlete,
           platform=excluded.platform, last_seen=CURRENT_TIMESTAMP""",
        (token, user["email"], user["role"], user.get("athlete"), platform),
    )
    con.commit()
    con.close()


def unregister(token):
    con = _db()
    con.execute("DELETE FROM push_devices WHERE token=?", (token,))
    con.commit()
    con.close()


def devices_of(email):
    """Tokens registered by one user (whatever their role)."""
    con = _db()
    rows = [r[0] for r in con.execute("SELECT token FROM push_devices WHERE email=?", (email,))]
    con.close()
    return rows


def devices(athlete=None, coaches=False):
    """Tokens of the athlete's own devices and/or of every coach."""
    con = _db()
    rows = [dict(r) for r in con.execute("SELECT token, email, role, athlete FROM push_devices")]
    con.close()
    return [
        r for r in rows if (athlete and r["role"] == "athlete" and r["athlete"] == athlete) or (coaches and r["role"] == "coach")
    ]


def already_sent(key):
    """True if `key` was sent before; marks it as sent otherwise (each event notifies once)."""
    con = _db()
    try:
        con.execute("INSERT INTO push_sent(key) VALUES(?)", (key,))
        con.commit()
        return False
    except sqlite3.IntegrityError:
        return True
    finally:
        con.close()


def was_sent(key):
    con = _db()
    try:
        return con.execute("SELECT 1 FROM push_sent WHERE key=?", (key,)).fetchone() is not None
    finally:
        con.close()


def mark_sent(keys):
    con = _db()
    con.executemany("INSERT OR IGNORE INTO push_sent(key) VALUES(?)", [(k,) for k in keys])
    con.commit()
    con.close()


def _auth(env="production"):
    import jwt  # PyJWT with cryptography (ES256)

    path, kid = _key(env)
    tok, at = _jwt.get((path, kid), (None, 0))
    if not tok or time.time() - at > 50 * 60:  # Apple accepts a token for up to 60 min
        tok = jwt.encode(
            {"iss": os.environ["TRIAPP_APNS_TEAM_ID"], "iat": int(time.time())},
            Path(path).read_text(),
            algorithm="ES256",
            headers={"kid": kid},
        )
        _jwt[(path, kid)] = (tok, time.time())
    return tok


def send(tokens, title, body, url="/", thread=None):
    """Send one alert to each token. Returns {"sent": n, "failed": n}.

    Builds run from Xcode register sandbox tokens, TestFlight and App Store builds production ones, and both can be in the
    table at once: a token Apple calls bad in the configured environment is tried in the other one before it is dropped,
    and only a token rejected as gone everywhere is removed (a key limited to one environment never removes anything)."""
    if not configured() or not tokens:
        return {"sent": 0, "failed": 0, "skipped": "apns not configured" if not configured() else "no devices"}
    import httpx

    first = os.environ.get("TRIAPP_APNS_ENV", "production")
    order = [first if first in HOSTS else "production"] + [e for e in HOSTS if e != first]
    payload = {"aps": {"alert": {"title": title[:120], "body": body[:240]}, "sound": "default"}, "url": url}
    if thread:
        payload["aps"]["thread-id"] = thread
    base = {"apns-topic": TOPIC, "apns-push-type": "alert", "apns-priority": "10"}
    content = json.dumps(payload, ensure_ascii=False).encode()
    sent = failed = 0
    with httpx.Client(http2=True, timeout=15) as c:
        for t in dict.fromkeys(tokens):
            gone = 0
            for env in order:
                try:
                    r = c.post(
                        f"{HOSTS[env]}/3/device/{t}", headers={**base, "authorization": f"bearer {_auth(env)}"}, content=content
                    )
                except httpx.HTTPError:
                    break
                if r.status_code == 200:
                    sent += 1
                    break
                reason = (r.json() if r.content else {}).get("reason", "")
                if r.status_code == 410 or reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
                    gone += 1  # bad here: maybe a token of the other environment
                    continue
                if reason == "BadEnvironmentKeyInToken":
                    continue  # the key does not cover this environment: try the other, never drop the token
                break
            else:
                failed += 1
                if gone == len(order):
                    unregister(t)
                continue
            if r.status_code != 200:
                failed += 1
    return {"sent": sent, "failed": failed}
