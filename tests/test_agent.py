def test_sync_request_is_handed_over_once_and_reported(tmp_path, monkeypatch):
    from app import config, agent

    monkeypatch.setattr(config, "DB", str(tmp_path / "app.sqlite"))
    tok = agent.new_token()
    assert agent.check(tok) and not agent.check("wrong") and not agent.check("")
    assert agent.poll("3") is False and agent.status()["online"]
    agent.request("Miguel")
    assert agent.status()["pending"]
    assert agent.poll("3") is True and agent.poll("3") is False  # once
    started = []
    monkeypatch.setattr(agent, "DRIVE_WAIT_S", 0)

    class Now:
        def __init__(self, target, daemon=True):
            self.t = target

        def start(self):
            self.t()

    monkeypatch.setattr(agent.threading, "Thread", Now)
    agent.report({"crashed": 0, "copied": 6, "wko_opened": 1, "minutes_open": 15}, lambda: started.append(1))
    st = agent.status()
    assert not st["pending"] and st["result"]["copied"] == 6 and started == [1]
    assert agent.new_token() != tok and not agent.check(tok)  # a new download replaces the token
