import llm


def test_clean_latex_and_think_blocks():
    assert llm.clean(r"IF $\ge$ 0,85 e TSB \leq 5 <think>hmm</think> ok ") == "IF ≥ 0,85 e TSB ≤ 5  ok"
    assert llm.clean(r"\geq 3 \times 10 \approx 30 \pm 2") == "≥ 3 × 10 ≈ 30 ± 2"


def test_streamed_chunks_keep_their_edges():
    assert llm.clean(" palavra ", strip=False) == " palavra "


def test_no_url_means_no_llm():
    assert llm.available() is False


def test_reasoning_off_unless_asked(monkeypatch):
    import json

    monkeypatch.setattr(llm, "URL", "http://box:11434")

    body = lambda **k: json.loads(llm.request([{"role": "user", "content": "x"}], **k).data)
    assert body()["reasoning_effort"] == "none"
    assert "reasoning_effort" not in body(think=True)
