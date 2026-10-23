"""Planned vs executed: the target read from the coach's title and the verdict from the laps."""

import pandas as pd
import execution as ex


def test_titles_the_parser_understands():
    assert ex.parse_title("Arcos de Valdevez - 2x10' Z3", "run") | {"text": ""} == {
        "reps": 2,
        "rep_s": 600,
        "zone": 3,
        "text": "",
    }
    assert ex.parse_title("Zona 4 - 7x4'", "run") | {"text": ""} == {
        "reps": 7,
        "rep_s": 240,
        "zone": 4,
        "text": "",
    }  # zone before the repeats
    assert ex.parse_title("7x2000 Z4 I=2'", "run") | {"text": ""} == {"reps": 7, "rep_m": 2000, "zone": 4, "text": ""}
    assert ex.parse_title("10x100 Z3 I=30''", "swim") | {"text": ""} == {"reps": 10, "rep_m": 100, "zone": 3, "text": ""}
    assert ex.parse_title("3x4km a 4:10 - 3:50", "run") | {"text": ""} == {"reps": 3, "rep_m": 4000, "text": ""}  # no zone


def test_titles_left_without_a_target():
    for title in ("2x2x(4' Z4 + 2x25' Z3)", "Tempo 18'", "1h Z2", "3x(10' + ramp down 100-75)", None):
        assert ex.parse_title(title, "bike") is None


def laps(rows):
    return pd.DataFrame(rows, columns=["lap", "intensity", "wkt_step_index", "timer_s", "distance_m", "avg_hr", "avg_power"])


# Marcelo, 2 Oct 2026, "2x10' Z3": the watch splits each step at every km, hence two laps per block
RUN_2X10 = laps(
    [
        (0, "warmup", 0, 389, 1000, 132, 321),
        (1, "warmup", 0, 211, 543, 141, 331),
        (2, "active", 1, 373, 1000, 147, 339),
        (3, "active", 1, 227, 632, 147, 327),
        (4, "rest", 2, 300, 721, 145, 288),
        (5, "active", 1, 364, 1000, 150, 335),
        (6, "active", 1, 236, 639, 155, 340),
        (7, "rest", 2, 300, 650, 146, 269),
        (8, "cooldown", 4, 402, 1000, 149, 298),
        (9, "cooldown", 4, 154, 232, 138, 156),
    ]
)


def test_laps_of_the_same_step_merge_into_blocks():
    blocks = ex.blocks(RUN_2X10)
    assert [(b["kind"], b["s"]) for b in blocks] == [
        ("warmup", 600),
        ("active", 600),
        ("rest", 300),
        ("active", 600),
        ("rest", 300),
        ("cooldown", 556),
    ]
    assert blocks[1]["hr"] == 147 and blocks[3]["hr"] == 152 and blocks[1]["pace"] == 6.13


def run_row(lthr):
    return pd.Series({"sport_n": "run", "lthr_used": lthr, "ftp_used": None})


def test_structure_and_zone_met():
    verdict, notes = ex.assess(run_row(163), ex.blocks(RUN_2X10), ex.parse_title("2x10' Z3", "run"))
    assert verdict == "cumprido"
    assert notes == ["blocos na Z3 por FC", "FC subiu 5 bpm do primeiro ao último bloco"]


def test_same_run_is_above_target_for_a_lower_threshold():
    verdict, _ = ex.assess(run_row(150), ex.blocks(RUN_2X10), ex.parse_title("2x10' Z3", "run"))
    assert verdict == "acima do alvo"


def test_wrong_number_of_blocks():
    verdict, notes = ex.assess(run_row(163), ex.blocks(RUN_2X10), ex.parse_title("4x10' Z3", "run"))
    assert verdict == "estrutura diferente" and notes[0] == "2 blocos de trabalho para 4 planeados"


def test_short_blocks_are_not_judged_by_heart_rate():
    short = laps(
        [(0, "warmup", 0, 600, 1500, 130, None)]
        + [(i, "active" if i % 2 else "rest", i, 240 if i % 2 else 60, 900, 160, None) for i in range(1, 15)]
    )
    verdict, notes = ex.assess(run_row(185), ex.blocks(short), ex.parse_title("Zona 4 - 7x4'", "run"))
    assert verdict == "estrutura cumprida" and notes[0].startswith("zona não avaliada: blocos curtos")
