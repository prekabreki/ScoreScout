"""Tests for the shared analysis pipeline (core/pipeline.py).

Both the CLI and the Flask app run their analysis through ``run_analysis``;
these tests pin its return contract and the title-fallback behavior that used
to live only in the web app.
"""

from music21 import stream, note, meter

from core.pipeline import run_analysis


def _write_score(tmp_path):
    s = stream.Score()
    p = stream.Part()
    p.append(meter.TimeSignature("4/4"))
    for n in ["C4", "D4", "E4", "F4"]:
        p.append(note.Note(n, quarterLength=1.0))
    s.insert(0, p)
    path = tmp_path / "untitled.musicxml"
    s.write("musicxml", fp=str(path))
    return path


def test_run_analysis_returns_analysis_dict_and_score(tmp_path):
    path = _write_score(tmp_path)
    analysis, score = run_analysis(str(path), use_llm=False)

    assert isinstance(analysis, dict)
    assert set(analysis) == {
        "metadata", "key", "rhythm", "stats", "chords",
        "structure", "difficulty", "annotations",
    }
    assert isinstance(score, stream.Score)


def test_title_fallback_used_when_score_has_no_title(tmp_path):
    path = _write_score(tmp_path)
    analysis, _ = run_analysis(
        str(path), use_llm=False, title_fallback="my_piece.musicxml",
    )
    assert analysis["metadata"]["title"] == "my_piece"


def test_title_fallback_ignored_when_score_already_titled(tmp_path, monkeypatch):
    path = _write_score(tmp_path)
    monkeypatch.setattr("core.pipeline.extract_metadata", lambda score: {
        "title": "Sonata",
        "composer": "Unknown",
        "number_of_parts": 1,
        "part_names": ["Part 1"],
    })

    analysis, _ = run_analysis(
        str(path), use_llm=False, title_fallback="ignored.musicxml",
    )
    assert analysis["metadata"]["title"] == "Sonata"
