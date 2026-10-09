"""Regression tests for analyzer/chord_analysis (the --no-llm Tier-4 bug).

When use_llm is False, NO Anthropic client may be constructed and no network
call may happen — even if ANTHROPIC_API_KEY is set. We enforce this by making
client construction raise; the analysis must still complete.
"""

import pytest
from music21 import chord as m21chord, stream

import analyzer.chord_analysis as ca
from analyzer.chord_analysis import (
    analyze_chords,
    _batch_identify_with_claude,
    _identify_chord_tiered,
    _normalize_to_pitch_classes,
)
from analyzer.key_analysis import analyze_key, key_from_string


def _explode(*args, **kwargs):
    raise AssertionError("Anthropic client must not be constructed when use_llm=False")


def test_analyze_chords_no_llm_never_constructs_client(monkeypatch, grand_staff_score):
    # Even with a key set, client construction would blow up if reached.
    monkeypatch.setattr(ca, "ANTHROPIC_API_KEY", "dummy-key")
    monkeypatch.setattr(ca.anthropic, "Anthropic", _explode)

    key_info = analyze_key(grand_staff_score)
    result = analyze_chords(grand_staff_score, key_info, use_llm=False)

    assert isinstance(result, dict)
    assert result["total_chords"] >= 1
    # Tier 4 (claude) must have contributed nothing.
    assert result["identification_stats"].get("claude", 0) == 0


def test_batch_identify_disabled_returns_empty_with_key_set(monkeypatch):
    monkeypatch.setattr(ca, "ANTHROPIC_API_KEY", "dummy-key")
    monkeypatch.setattr(ca.anthropic, "Anthropic", _explode)

    batch = [{
        "pitch_classes_label": "[C E G]",
        "pitches": ["C4", "E4", "G4"],
        "measure": 1,
    }]
    assert _batch_identify_with_claude(batch, "C major", use_llm=False) == {}


# music21's RomanNumeral.pitchedCommonName degrades to verbose interval prose
# for unusual voicings. These are the three real forms seen in the wild
# (issue #4); each maps back to its compact Roman figure.
@pytest.mark.parametrize(
    "pitches, verbose_name, figure",
    [
        (["Bb2", "E5"], "Augmented Eighteenth above Bb", "iiio4"),
        (["C3", "E6"], "Major 24th above C", "I"),
        (["F3", "D5", "D6"], "Major Sixth with octave doublings above F", "ii6"),
    ],
)
def test_verbose_interval_names_replaced_by_roman_figure(pitches, verbose_name, figure):
    ch = m21chord.Chord(pitches)
    # Guard the fixture: the chord really does produce the verbose prose.
    assert ch.pitchedCommonName == verbose_name

    pcs = _normalize_to_pitch_classes(ch)
    name, rn_fig, tier = _identify_chord_tiered(ch, pcs, key_from_string("C major"))

    assert tier == "roman"
    assert name == figure
    assert " above " not in name
    assert rn_fig == figure


def test_analyze_chords_excludes_verbose_interval_names():
    """No ' above ' prose reaches unique_chords or the progression."""
    s = stream.Score()
    part = stream.Part()
    for pitches in (["Bb2", "E5"], ["C3", "E6"], ["F3", "D5", "D6"]):
        part.append(m21chord.Chord(pitches, quarterLength=1.0))
    s.insert(0, part)

    result = analyze_chords(s, {"detected_key": "C major"}, use_llm=False)

    assert result["unique_chords"], "expected some identified chords"
    assert all(" above " not in n for n in result["unique_chords"])
    for entry in result["chord_progression_full"]:
        assert " above " not in entry["name"]
