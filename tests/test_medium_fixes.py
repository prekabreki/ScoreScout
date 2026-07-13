"""Regression tests for the medium-severity audit bundle (issue #16, M1-M13).

Each test pins a specific correctness fix so the behavior can't silently
regress. No network: ANTHROPIC_API_KEY is empty in CI and no LLM path runs.
"""

from pathlib import Path

import pytest
from music21 import stream, note, meter, tempo, pitch

from analyzer.difficulty import analyze_difficulty, DEFAULT_BPM
from analyzer.rhythm_analysis import analyze_rhythm, _estimate_duration_seconds
from analyzer.stats import analyze_stats
from analyzer.key_analysis import analyze_key
from export.annotate import _is_accidental_outside_key


# --- M1: library path containment (no str-prefix bypass) -------------------

def test_m1_path_within_rejects_sibling_dir(tmp_path):
    from app import _path_within

    lib = tmp_path / "Piano"
    lib.mkdir()
    sibling = tmp_path / "Piano_backup"
    sibling.mkdir()

    inside = lib / "song.mxl"
    outside = sibling / "song.mxl"

    assert _path_within(inside.resolve(), lib) is True
    # The old str.startswith check let "Piano_backup" pass for "Piano"; it must not.
    assert _path_within(outside.resolve(), lib) is False


# --- M2: difficulty tolerates range-shaped tempo entries -------------------

def test_m2_difficulty_reads_range_shaped_tempo(grand_staff_score):
    """A compressed accel/rit entry exposes from_bpm (no 'bpm'); difficulty must
    pick up a real BPM from it instead of silently falling back to DEFAULT_BPM."""
    rhythm = analyze_rhythm(grand_staff_score)
    stats = analyze_stats(grand_staff_score)
    key = analyze_key(grand_staff_score)

    # Force a range-shaped first tempo (what _compress_tempo_marks emits).
    rhythm["tempos"] = [
        {"type": "accel", "from_bpm": 200, "to_bpm": 260,
         "from_measure": 1, "to_measure": 5},
    ]
    diff = analyze_difficulty(grand_staff_score, rhythm, stats, key)
    # With bpm=200 (>> DEFAULT_BPM 120) tempo_density should be at its max.
    assert diff["component_scores"]["tempo_density"] >= 1

    # Compare against the DEFAULT_BPM fallback: an empty tempo list must score
    # strictly lower, proving the range dict was actually read.
    rhythm_default = dict(rhythm)
    rhythm_default["tempos"] = []
    diff_default = analyze_difficulty(grand_staff_score, rhythm_default, stats, key)
    # 200 BPM (read from from_bpm) must score strictly above the 120 fallback,
    # which only holds if the range dict was actually parsed.
    assert diff["component_scores"]["tempo_density"] > diff_default["component_scores"]["tempo_density"]
    assert DEFAULT_BPM == 120  # guard the assumption above


# --- M7: accidental-outside-key detection actually discriminates -----------

def test_m7_accidental_outside_key_true_and_false():
    # G major = 1 sharp. F# is diatonic; G# and Bb are chromatic.
    assert _is_accidental_outside_key(pitch.Pitch("F#4"), 1) is False
    assert _is_accidental_outside_key(pitch.Pitch("G#4"), 1) is True
    # C major = 0 sharps: any sounding accidental is chromatic.
    assert _is_accidental_outside_key(pitch.Pitch("B-4"), 0) is True
    # F major = 1 flat: Bb is diatonic.
    assert _is_accidental_outside_key(pitch.Pitch("B-4"), -1) is False
    # No accidental object -> not "outside key".
    assert _is_accidental_outside_key(pitch.Pitch("C4"), 0) is False


# --- M13: duration integrates across tempo changes -------------------------

def test_m13_duration_integrates_tempo_changes():
    """A piece that is half at 60 BPM and half at 120 BPM must be longer than
    the same length taken entirely at 120 BPM (the old constant-tempo estimate)."""
    p = stream.Part()
    p.append(meter.TimeSignature("4/4"))
    p.append(tempo.MetronomeMark(number=60))
    for _ in range(4):
        p.append(note.Note("C4", quarterLength=1.0))  # 4 quarters @ 60 bpm = 4.0s
    p.append(tempo.MetronomeMark(number=120))
    for _ in range(4):
        p.append(note.Note("C4", quarterLength=1.0))  # 4 quarters @ 120 bpm = 2.0s
    s = stream.Score()
    s.insert(0, p)

    integrated = _estimate_duration_seconds(s, [{"bpm": 60}, {"bpm": 120}])
    constant_120 = round(float(s.highestTime) / 120 * 60, 1)

    assert integrated is not None
    # ~6.0s integrated vs 4.0s if it had wrongly used 120 BPM throughout.
    assert integrated > constant_120
    assert integrated == pytest.approx(6.0, abs=0.2)


# --- M11: chord progression dict no longer carries the duplicate _sample ----

def test_m11_no_duplicate_chord_progression_sample(grand_staff_score):
    from analyzer.chord_analysis import analyze_chords

    key = analyze_key(grand_staff_score)
    chords = analyze_chords(grand_staff_score, key, use_llm=False)
    assert "chord_progression_full" in chords
    assert "chord_progression_sample" not in chords
