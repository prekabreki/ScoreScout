"""Regression test for analyzer/difficulty (audit H9: difficulty clamping).

A purely-melodic score has no chords, so the raw avg_chord_size computation
yields round(-1) == -1 for chord_density; that component must floor at 0, and
nothing may drag overall or any component below zero.
"""

from analyzer.difficulty import analyze_difficulty
from analyzer.rhythm_analysis import analyze_rhythm
from analyzer.stats import analyze_stats
from analyzer.key_analysis import analyze_key


def test_melodic_difficulty_all_components_nonnegative(melodic_score):
    rhythm = analyze_rhythm(melodic_score)
    stats = analyze_stats(melodic_score)
    key = analyze_key(melodic_score)

    diff = analyze_difficulty(melodic_score, rhythm, stats, key)

    assert diff["overall_difficulty"] >= 0
    assert diff["component_scores"], "expected component scores"
    for name, value in diff["component_scores"].items():
        assert value >= 0, f"component {name} is negative: {value}"
    # chord_density specifically is the H9 regression point.
    assert diff["component_scores"]["chord_density"] == 0


def test_grand_staff_difficulty_well_formed(grand_staff_score):
    rhythm = analyze_rhythm(grand_staff_score)
    stats = analyze_stats(grand_staff_score)
    key = analyze_key(grand_staff_score)

    diff = analyze_difficulty(grand_staff_score, rhythm, stats, key)

    assert 1 <= diff["overall_difficulty"] <= 10
    for name, value in diff["component_scores"].items():
        assert 0 <= value <= 10, f"{name} out of range: {value}"
