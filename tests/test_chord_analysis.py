"""Regression tests for analyzer/chord_analysis (the --no-llm Tier-4 bug).

When use_llm is False, NO Anthropic client may be constructed and no network
call may happen — even if ANTHROPIC_API_KEY is set. We enforce this by making
client construction raise; the analysis must still complete.
"""

import pytest
from music21 import chord as m21chord, stream

import llm.chord_names as chord_names
import llm.client as llm_client
from analyzer.chord_analysis import (
    analyze_chords,
    _identify_chord_tiered,
    _normalize_to_pitch_classes,
)
from analyzer.key_analysis import analyze_key, key_from_string


def _explode(*args, **kwargs):
    raise AssertionError("Anthropic client must not be constructed when use_llm=False")


def test_analyze_chords_no_llm_never_constructs_client(monkeypatch, grand_staff_score):
    # Even with a key set (and a namer supplied), client construction would
    # blow up if reached.
    monkeypatch.setattr(llm_client, "ANTHROPIC_API_KEY", "dummy-key")
    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _explode)
    monkeypatch.setattr(chord_names, "ANTHROPIC_API_KEY", "dummy-key")

    key_info = analyze_key(grand_staff_score)
    result = analyze_chords(
        grand_staff_score,
        key_info,
        use_llm=False,
        chord_namer=chord_names.identify_chords,
    )

    assert isinstance(result, dict)
    assert result["total_chords"] >= 1
    # Tier 4 (claude) must have contributed nothing.
    assert result["identification_stats"].get("claude", 0) == 0


def test_identify_chords_empty_key_returns_empty_without_client(monkeypatch):
    monkeypatch.setattr(chord_names, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _explode)

    batch = [{
        "pitch_classes_label": "[C E G]",
        "pitches": ["C4", "E4", "G4"],
        "measure": 1,
    }]
    assert chord_names.identify_chords(batch, "C major") == {}


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


@pytest.mark.parametrize(
    "pitches, expected",
    [
        (["C4", "E4", "G4"], "C"),
        (["F3", "A3", "C4"], "F"),
        (["G3", "B3", "D4", "F4"], "G7"),
        (["E3", "G3", "C4"], "C/E"),
        (["G4", "B4", "D5"], "G"),
        (["A4", "C5", "E5"], "Am"),
        (["E4", "G4", "C5"], "C/E"),
    ],
)
def test_tier1_names_against_real_bass(pitches, expected):
    """Tier 1 must voice above the real bass, not the lowest pitch class (#33)."""
    s = stream.Score()
    part = stream.Part()
    part.append(m21chord.Chord(pitches, quarterLength=1.0))
    s.insert(0, part)

    result = analyze_chords(s, {"detected_key": "C major"}, use_llm=False)

    assert result["unique_chords"] == [expected]
