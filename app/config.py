"""Configuration from environment variables (see docker-compose.yml)."""

import os, json, secrets
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
ROOT = Path(os.environ.get("TRIAPP_ROOT", str(REPO_DIR.parent / "triapp-data")))  # *_Fit_Files and *_wko folders
OUT = Path(os.environ.get("TRIAPP_OUT", str(REPO_DIR / "out")))  # pipeline outputs
DB = Path(os.environ.get("TRIAPP_DB", str(OUT / "app.sqlite")))
DATA_JSON = OUT / "data" / "dashboard_data.json"
STATUS_JSON = OUT / "pipeline_status.json"
LOG_FILE = OUT / "pipeline.log"
RCLONE_REMOTE = os.environ.get("TRIAPP_RCLONE_REMOTE", "")  # e.g. "gdrive:triapp" (empty = no Drive sync)
PY = os.environ.get("TRIAPP_PY", "python3")
COOKIE_SECURE = os.environ.get("TRIAPP_COOKIE_SECURE", "1") == "1"

_secret_file = OUT / ".secret"
SECRET = os.environ.get("TRIAPP_SECRET")
if not SECRET:
    OUT.mkdir(parents=True, exist_ok=True)
    if not _secret_file.exists():
        _secret_file.write_text(secrets.token_hex(32))
        _secret_file.chmod(0o600)
    SECRET = _secret_file.read_text().strip()


# athlete key -> display name (keys must match the pipeline's athlete names)
def athletes():
    """{key: full name} of the athletes in the current dashboard data (discovered from the Drive folders by the pipeline)."""
    try:
        d = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        return {k: v.get("full_name", k) for k, v in d.get("athletes", {}).items()}
    except Exception:
        return {}  # no data yet: the first pipeline run creates it
