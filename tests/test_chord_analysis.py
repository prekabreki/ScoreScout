"""Regression tests for analyzer/chord_analysis (the --no-llm Tier-4 bug).

When use_llm is False, NO Anthropic client may be constructed and no network
call may happen — even if ANTHROPIC_API_KEY is set. We enforce this by making
client construction raise; the analysis must still complete.
"""

import pytest

import analyzer.chord_analysis as ca
from analyzer.chord_analysis import analyze_chords, _batch_identify_with_claude
from analyzer.key_analysis import analyze_key


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
