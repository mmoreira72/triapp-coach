#!/usr/bin/env bash
# Full refresh outside the app: the same steps, in the same order, as app/pipeline.py (the app runs them daily at 05:00).
set -euo pipefail
cd "$(dirname "$0")"
: "${TRIAPP_ROOT:?set TRIAPP_ROOT to the folder holding the athlete folders (*_wko, *_Fit_Files)}"
export TRIAPP_OUT="${TRIAPP_OUT:-$PWD/out}"
mkdir -p "$TRIAPP_OUT/data"
PY="${PY:-python3}"
for step in intervals_sync tp_calendar parse_fit parse_wko build_dataset wko_planned reconcile build_dashboard_data race_impact skills_engine session_notes morning_brief notify; do
  echo "== $step"
  $PY "$step.py"
done
