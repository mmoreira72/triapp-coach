#!/bin/bash
# Tri Squad: nightly WKO5 sync on the coach's Mac (run by launchd, see install.sh).
#  1. opens WKO5 so it pulls the latest workouts and plans from TrainingPeaks, waits, and quits it
#  2. copies the athletes' WKO5 libraries to the Google Drive folder the Tri Squad server reads
#  3. tells the Tri Squad server how it went (the admin page shows it, and the server pulls the Drive)
# With --poll (every 5 min, launchd): asks the server whether the coach pressed "Sincronizar WKO agora" and, if so, syncs.
# Settings: ~/.config/triapp-wko-sync.conf (WKO_APP, WKO_LIBRARY, DRIVE_DEST, WAIT_MIN) and server.conf next to this script
# (TRIAPP_URL, TRIAPP_AGENT_TOKEN). Log: ~/Library/Logs/triapp-wko-sync.log
set -u
VERSION=3
CONF="$HOME/.config/triapp-wko-sync.conf"
LOG="$HOME/Library/Logs/triapp-wko-sync.log"
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[ -f "$HERE/server.conf" ] && . "$HERE/server.conf"
if [ "${1:-}" = "--poll" ]; then  # quiet unless there is something to do: no line in the log every 5 minutes
  [ -n "${TRIAPP_URL:-}" ] && [ -n "${TRIAPP_AGENT_TOKEN:-}" ] || exit 0
  ans="$(curl -fsS -m 20 "$TRIAPP_URL/api/agent/poll?token=$TRIAPP_AGENT_TOKEN&v=$VERSION" 2>/dev/null || true)"
  case "$ans" in *'"run":true'*) exec "$0" --requested ;; *) exit 0 ;; esac
fi
exec >>"$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') start${1:+ ($1)}"
[ -f "$CONF" ] || { echo "missing $CONF: run install.sh"; exit 1; }
# shellcheck disable=SC1090
. "$CONF"
WAIT_MIN="${WAIT_MIN:-15}"
OPENED=0
CRASHED=0
MINUTES=0
REPORT=""

if [ "${1:-}" != "--copy-only" ] && [ -n "${WKO_APP:-}" ] && [ -d "$WKO_APP" ]; then
  if pgrep -f "$WKO_APP/Contents/MacOS" >/dev/null; then
    echo "WKO5 already open: leaving it as it is, copying only"
  else
    echo "opening $WKO_APP for $WAIT_MIN min to sync with TrainingPeaks"
    OPENED=1
    STARTED_AT=$(date +%s)
    MARK="$(mktemp)"  # crash reports newer than this belong to this run
    open -g -a "$WKO_APP"
    sleep 20
    # watch WKO5 every 30 s: if it disappears before the time is up it crashed (a bad workout in a TrainingPeaks sync)
    while [ $(($(date +%s) - STARTED_AT)) -lt $((WAIT_MIN * 60)) ]; do
      if ! pgrep -f "$WKO_APP/Contents/MacOS" >/dev/null; then
        CRASHED=1
        break
      fi
      sleep 30
    done
    MINUTES=$((($(date +%s) - STARTED_AT) / 60))
    if [ "$CRASHED" = 1 ]; then
      REPORT="$(find "$HOME/Library/Logs/DiagnosticReports" -name 'WKO5*' -newer "$MARK" 2>/dev/null | head -1)"
      echo "WKO5 CRASHED after $MINUTES min${REPORT:+ (report: $REPORT)}"
      [ -n "$REPORT" ] && grep -m3 -E 'Crashed|PKTrainingPeaksThread|syncDownload' "$REPORT" | sed 's/^/   /'
    else
      APPNAME="$(basename "$WKO_APP" .app)"
      osascript -e "tell application \"$APPNAME\" to quit" || true
      sleep 20
    fi
    rm -f "$MARK"
  fi
fi

[ -d "${WKO_LIBRARY:-/nonexistent}" ] || { echo "WKO library not found: ${WKO_LIBRARY:-unset}"; exit 1; }
[ -d "${DRIVE_DEST:-/nonexistent}" ] || { echo "Drive folder not found: ${DRIVE_DEST:-unset}"; exit 1; }
# one folder per athlete with its yearly .wko4 files and the .wko5athlete profile; nothing is ever deleted on the Drive
n=0
while IFS= read -r -d '' prof; do
  src="$(dirname "$prof")"
  name="$(basename "$src")"
  rsync -a --update --include='*/' --include='*.wko4' --include='*.wko5athlete' --include='*.wko5' --exclude='*' \
    "$src/" "$DRIVE_DEST/$name/" && n=$((n + 1))
done < <(find "$WKO_LIBRARY" -maxdepth 3 -name '*.wko5athlete' -print0)
echo "copied $n athlete libraries to $DRIVE_DEST"
# status for the Tri Squad server (admin page): when it ran, whether WKO5 crashed, and when each library last changed
{
  printf '{"run_at":"%s","wko_opened":%s,"crashed":%s,"minutes_open":%s,"copied":%s,"report":"%s","libraries":{' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$OPENED" "$CRASHED" "$MINUTES" "$n" "$(basename "${REPORT:-}")"
  first=1
  while IFS= read -r -d '' prof; do
    [ $first = 1 ] || printf ','
    first=0
    printf '"%s":"%s"' "$(basename "$(dirname "$prof")")" "$(date -r "$prof" '+%Y-%m-%dT%H:%M:%S')"
  done < <(find "$WKO_LIBRARY" -maxdepth 3 -name '*.wko5athlete' -print0)
  printf '}}\n'
} >"$DRIVE_DEST/_triapp-wko-status.json" || echo "could not write the status file"
if [ -n "${TRIAPP_URL:-}" ] && [ -n "${TRIAPP_AGENT_TOKEN:-}" ]; then
  curl -fsS -m 30 -H "Content-Type: application/json" --data-binary @"$DRIVE_DEST/_triapp-wko-status.json" \
    "$TRIAPP_URL/api/agent/report?token=$TRIAPP_AGENT_TOKEN" >/dev/null && echo "reported to $TRIAPP_URL" || echo "could not report to the server"
fi
echo "=== $(date '+%Y-%m-%d %H:%M:%S') done"
