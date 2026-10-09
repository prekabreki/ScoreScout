"""Unit tests for the shared report view model (output/report_model.py).

The model is the single owner of report extraction, defaults and truncation.
These tests pin its limits directly and prove html.py and markdown.py render
the same chords, note-map length and sections from the same analysis.
"""

from output.report_model import (
    MAX_COMMON_CHORDS,
    MAX_DYNAMICS,
    MAX_NOTE_MAP_LINES,
    build_report_model,
)
from output.html import render_html
from output.markdown import render_markdown


def _analysis(n_chords=15, n_notes=70, n_dynamics=30):
    return {
        "metadata": {"title": "T", "composer": "C"},
        "difficulty": {
            "overall_difficulty": 5,
            "component_scores": {},
            "skills_required": ["Scales"],
        },
        "stats": {
            "num_measures": 10,
            "total_notes": 100,
            "per_hand": [
                {"part_name": "RH", "note_count": 60,
                 "lowest_note": "C4", "highest_note": "C6"},
            ],
        },
        "rhythm": {
            "time_signatures": [{"signature": "3/4"}],
            "tempos": [{"bpm": 120}],
            "estimated_duration_seconds": 90,
        },
        "key": {"detected_key": "C major"},
        "chords": {
            "most_common_chords": [
                {"name": f"Chord{i}", "count": i} for i in range(1, n_chords + 1)
            ]
        },
        "structure": {
            "key_changes": [
                {"at_measure": 5, "from_key": "C major", "to_key": "G major"}
            ],
            "dynamics": [
                {"measure": i, "marking": "mf"} for i in range(1, n_dynamics + 1)
            ],
        },
        "annotations": {
            "simplified": [f"M{i}: RH(C)" for i in range(1, n_notes + 1)]
        },
    }


# --- truncation limits, owned by the model --------------------------------

def test_chords_truncated_to_limit():
    model = build_report_model(_analysis(n_chords=15))
    assert MAX_COMMON_CHORDS == 12
    assert len(model.common_chords) == MAX_COMMON_CHORDS
    assert model.common_chords[0]["name"] == "Chord1"
    assert all(c["name"] != "Chord13" for c in model.common_chords)


def test_note_map_truncated_to_limit_with_omitted_count():
    model = build_report_model(_analysis(n_notes=70))
    assert MAX_NOTE_MAP_LINES == 60
    assert len(model.note_map) == MAX_NOTE_MAP_LINES
    assert model.note_map_total == 70
    assert model.note_map_omitted == 10
    assert model.note_map[-1] == "M60: RH(C)"


def test_dynamics_truncated_to_limit():
    model = build_report_model(_analysis(n_dynamics=30))
    assert MAX_DYNAMICS == 20
    assert len(model.dynamics) == MAX_DYNAMICS


def test_under_limit_lists_are_not_padded_or_sliced():
    model = build_report_model(_analysis(n_chords=3, n_notes=5, n_dynamics=2))
    assert len(model.common_chords) == 3
    assert len(model.note_map) == 5
    assert model.note_map_omitted == 0
    assert len(model.dynamics) == 2


# --- defaults, owned by the model -----------------------------------------

def test_defaults_for_empty_analysis():
    model = build_report_model({})
    assert model.title == "Unknown"
    assert model.composer == "Unknown"
    assert model.has_composer is False
    assert model.detected_key == "?"
    assert model.time_signature == "4/4"
    assert model.tempo == "N/A"
    assert model.has_tempo is False
    assert model.duration == "N/A"
    assert model.has_duration is False
    assert model.num_measures == "?"
    assert model.total_notes == "?"
    assert model.overall_difficulty == "?"
    assert model.common_chords == ()
    assert model.key_changes == ()
    assert model.dynamics == ()
    assert model.per_hand == ()
    assert model.note_map == ()
    assert model.note_map_omitted == 0
    assert len(model.difficulty_breakdown) == 6
    assert all(v == 0 for _, v in model.difficulty_breakdown)


def test_tempo_and_duration_formatting():
    model = build_report_model(_analysis())
    assert model.tempo == "120 BPM"
    assert model.has_tempo is True
    assert model.duration == "1:30"
    assert model.has_duration is True

    ranged = _analysis()
    ranged["rhythm"]["tempos"] = [{"from_bpm": 200}]
    assert build_report_model(ranged).tempo == "200 BPM"


# --- both renderers show the same data ------------------------------------

def test_renderers_show_same_chords_and_note_map():
    analysis = _analysis()
    html = render_html(analysis)
    md = render_markdown(analysis)

    for i in range(1, MAX_COMMON_CHORDS + 1):
        assert f"Chord{i}" in html
        assert f"Chord{i}" in md
    assert "Chord13" not in html
    assert "Chord13" not in md

    assert "M60: RH(C)" in html
    assert "M60: RH(C)" in md
    assert "M61: RH(C)" not in html
    assert "M61: RH(C)" not in md
    assert "10 more measures" in html
    assert "10 more measures" in md


def test_renderers_show_same_sections():
    analysis = _analysis()
    html = render_html(analysis)
    md = render_markdown(analysis)

    assert "Dynamic Markings" in html
    assert "Dynamic Markings" in md
    assert "m.1: mf" in html
    assert "m.1: mf" in md

    assert "Hands" in html
    assert "Hands" in md
    assert "<td>RH</td>" in html
    assert "| RH |" in md
