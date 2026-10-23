import intervals_sync


def test_threshold_history_keeps_only_changes():
    a, b = {"bike": {"ftp": 250}}, {"bike": {"ftp": 221}}
    rows = [
        {"date": "2026-10-02", "settings": a},
        {"date": "2026-10-03", "settings": a},
        {"date": "2026-10-03", "settings": b},
        {"date": "2026-10-04", "settings": b},
    ]
    assert intervals_sync.compact(rows) == [{"date": "2026-10-02", "settings": a}, {"date": "2026-10-03", "settings": b}]
