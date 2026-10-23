"""Tests run on small hand-made frames: no athlete data, no network, no LLM."""

import os, sys, tempfile

os.environ["TRIAPP_TODAY"] = "2026-10-04"  # a Sunday; the current week starts on 2026-09-28
os.environ["TRIAPP_OUT"] = tempfile.mkdtemp(prefix="triapp-test-")
os.environ["TRIAPP_ROOT"] = tempfile.mkdtemp(prefix="triapp-root-")
os.environ["TRIAPP_LLM_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
