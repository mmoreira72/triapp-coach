"""The local LLM (Ollama, OpenAI-compatible API): configuration, one call helper, output clean-up.

The model only writes prose from numbers the deterministic steps computed. Empty TRIAPP_LLM_URL = no LLM.
"""

import os, re, json, urllib.request

URL = os.environ.get("TRIAPP_LLM_URL", "")  # e.g. http://host.docker.internal:11434
MODEL = os.environ.get("TRIAPP_LLM_MODEL", "gemma4:26b-a4b-it-qat")
_SYMBOLS = {r"\\geq?": "≥", r"\\leq?": "≤", r"\\times": "×", r"\\approx": "≈", r"\\pm": "±"}


def available():
    return bool(URL)


def request(messages, think=False, **options):
    """urllib Request for a chat completion; options are the API's (temperature, stream, max_tokens...).

    think=False turns the model's reasoning off: Gemma 4 otherwise writes 1000-3000 hidden tokens before a 60-word note
    (a minute instead of 4 s on the box) for the same text. The nightly texts with many rules (morning brief, analysis) keep it.
    """
    body = {"model": MODEL, "messages": messages, **options}
    if not think:
        body.setdefault("reasoning_effort", "none")
    return urllib.request.Request(
        URL.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )


def chat(messages, timeout=240, think=False, **options):
    """One blocking completion; returns the cleaned text."""
    options.setdefault("stream", False)
    with urllib.request.urlopen(request(messages, think=think, **options), timeout=timeout) as resp:
        return clean(json.loads(resp.read().decode())["choices"][0]["message"]["content"])


def clean(text, strip=True):
    """LLM output to plain text: <think> blocks out, LaTeX symbols to unicode, $...$ wrappers removed.
    strip=False keeps the edges, for streamed chunks."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    for pattern, symbol in _SYMBOLS.items():
        text = re.sub(pattern, symbol, text)
    text = re.sub(r"\$\s*([^$]{1,40}?)\s*\$", r"\1", text)
    return text.strip() if strip else text
