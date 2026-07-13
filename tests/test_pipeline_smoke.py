"""Smoke test: the full analyzer pipeline runs end-to-end on a synthetic score.

Every analysis stage must return a dict without raising, with ANTHROPIC_API_KEY
empty / use_llm=False (no network).
"""

from analyzer.key_analysis import analyze_key
from analyzer.rhythm_analysis import analyze_rhythm
from analyzer.stats import analyze_stats
from analyzer.chord_analysis import analyze_chords
from analyzer.structure import analyze_structure
from analyzer.difficulty import analyze_difficulty
from analyzer.note_annotations import generate_annotations


def test_full_pipeline_smoke(parsed_melodic_score):
    score = parsed_melodic_score

    key = analyze_key(score)
    assert isinstance(key, dict)

    rhythm = analyze_rhythm(score)
    assert isinstance(rhythm, dict)

    stats = analyze_stats(score)
    assert isinstance(stats, dict)

    chords = analyze_chords(score, key, use_llm=False)
    assert isinstance(chords, dict)

    structure = analyze_structure(score)
    assert isinstance(structure, dict)

    difficulty = analyze_difficulty(score, rhythm, stats, key)
    assert isinstance(difficulty, dict)

    annotations = generate_annotations(score)
    assert isinstance(annotations, dict)


def test_full_pipeline_smoke_grand_staff(grand_staff_score):
    score = grand_staff_score
    key = analyze_key(score)
    rhythm = analyze_rhythm(score)
    stats = analyze_stats(score)
    chords = analyze_chords(score, key, use_llm=False)
    structure = analyze_structure(score)
    difficulty = analyze_difficulty(score, rhythm, stats, key)
    annotations = generate_annotations(score)

    for d in (key, rhythm, stats, chords, structure, difficulty, annotations):
        assert isinstance(d, dict)
