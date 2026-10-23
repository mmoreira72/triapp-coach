from app import demo


def test_demo_is_an_anonymised_copy():
    d = {
        "generated": "2026-10-12",
        "athletes": {
            "Marcelo": {
                "name": "Marcelo",
                "full_name": "Marcelo Moreira",
                "ftp": 221,
                "recent": [{"key": "k", "note": "após a fratura", "note_voice": "x"}],
                "executions": [],
            }
        },
        "analysis": {
            "Marcelo": {
                "narrative": "Marcelo, ...",
                "narrative_voice": "Marcelo...",
                "findings": [
                    {"skill": "context", "severity": "info", "title": "Lesão", "detail": "fratura"},
                    {"skill": "consistency", "severity": "alert", "title": "Consistência", "detail": "40%"},
                    {
                        "skill": "threshold_audit",
                        "severity": "info",
                        "title": "Limiar",
                        "detail": "IF alto. Ignorado por Marcelo Moreira em 2026-10-12: x",
                        "ignored": {"was": "alert", "who": "Marcelo Moreira", "at": "2026-10-12"},
                    },
                ],
            }
        },
        "races_target": {
            "salou": {"race": {"name": "Salou"}, "athletes": {"Marcelo": {"name": "Marcelo", "total": "5:00"}, "Athlete2": {}}}
        },
    }
    s = demo.slice_for(d)
    A = s["athletes"]["Demo"]
    assert A["full_name"] == "Atleta Demo" and A["ftp"] == 221 and "note" not in A["recent"][0]
    an = s["analysis"]["Demo"]
    assert an["narrative"] is None and [f["skill"] for f in an["findings"]] == ["consistency", "threshold_audit"]
    assert an["counts"]["alert"] == 2 and "Marcelo" not in str(an)
    assert s["races_target"]["salou"]["athletes"] == {"Demo": {"name": "Atleta Demo", "total": "5:00"}}
    assert d["athletes"]["Marcelo"]["recent"][0]["note"] == "após a fratura"  # the real data is untouched
