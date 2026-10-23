import pandas as pd
import wko_planned


def test_plan_rows_and_unknown_after_the_last_sync():
    acts = pd.DataFrame(
        [
            {
                "athlete": "Ana",
                "source": "wko",
                "date": "2026-10-08",
                "sport_n": "run",
                "title_n": "Z2",
                "plan_desc": "40' Z2",
                "planned_duration_s": 2400,
                "planned_tss": 35,
                "completed": True,
            },
            {
                "athlete": "Ana",
                "source": "wko",
                "date": "2026-10-09",
                "sport_n": "bike",
                "title_n": "Limiar",
                "plan_desc": None,
                "planned_duration_s": 3600,
                "planned_tss": 70,
                "completed": False,
            },
            {
                "athlete": "Ana",
                "source": "wko",
                "date": "2026-10-14",
                "sport_n": "swim",
                "title_n": "Nx100",
                "plan_desc": "palas",
                "planned_duration_s": 2100,
                "planned_tss": None,
                "completed": False,
            },
            {
                "athlete": "Ana",
                "source": "wko",
                "date": "2026-09-01",
                "sport_n": "run",
                "title_n": "velho",
                "plan_desc": "x",
                "planned_duration_s": 1800,
                "planned_tss": 20,
                "completed": True,
            },
            {
                "athlete": "Ana",
                "source": "wko",
                "date": "2026-10-11",
                "sport_n": "run",
                "title_n": "extra",
                "plan_desc": None,
                "planned_duration_s": None,
                "planned_tss": None,
                "completed": True,
            },
        ]
    )
    rows = wko_planned.rows_from(acts, "Ana", "2026-10-15")
    assert [(r["date"], r["title"], r["completed"]) for r in rows] == [
        ("2026-10-08", "Z2", True),
        ("2026-10-09", "Limiar", False),  # before the last sync and not done: missed
        ("2026-10-14", "Nx100", None),  # after the last sync: unknown, not missed
    ]
    assert rows[0]["duration_s"] == 2400 and rows[0]["load"] == 35 and rows[2]["description"] == "palas"
