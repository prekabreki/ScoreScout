"""Flat names reach reports, the LLM prompt and the chord guide as Bb, not B-.

Issue #34: analyzers emit music21's internal flat spelling, and nothing
converted it before it reached the user. These tests pin the shared
``display_name`` helper, the analyzer output that now runs through it, and the
chord-guide title.
"""

import pytest
from music21 import chord, key, meter, stream, tempo
from pypdf import PdfReader

from analyzer.key_analysis import key_from_string
from analyzer.spelling import display_name
from core.pipeline import run_analysis
from export.guide_pdf import render_chord_guide_pdf
from output.html import render_html
from output.markdown import render_markdown


@pytest.mark.parametrize("text,expected", [
    ("B-", "Bb"),
    ("E-", "Eb"),
    ("B--", "Bbb"),
    ("A--", "Abb"),
    ("F#", "F#"),
    ("C", "C"),
    ("B-4", "Bb4"),
    ("B-/D", "Bb/D"),
    ("B-maj7", "Bbmaj7"),
    ("E-maj7/B-", "Ebmaj7/Bb"),
    ("B-addE-", "BbaddEb"),
    ("Bb", "Bb"),          # already converted: idempotent
    ("Bbb", "Bbb"),
    ("b- minor", "Bb minor"),
    ("B-major triad", "B-major triad"),   # prose, not a chord symbol
    ("Bb-major triad", "Bb-major triad"),
])
def test_display_name_spells_flats(text, expected):
    assert display_name(text) == expected


def test_key_from_string_round_trips_display_spelling():
    """A display-spelled flat key still parses into a music21 Key (#34 danger zone)."""
    parsed = key_from_string("Bb major")
    assert parsed is not None
    assert parsed.tonic.name == "B-"
    assert parsed.mode == "major"

    parsed_minor = key_from_string("Eb minor")
    assert parsed_minor is not None
    assert parsed_minor.tonic.name == "E-"


def _bflat_major_score() -> stream.Score:
    """Four measures of Bb major block chords in both hands (Bb and Eb)."""
    score = stream.Score()
    treble = stream.Part()
    bass = stream.Part()
    for part in (treble, bass):
        part.append(meter.TimeSignature("4/4"))
        part.append(key.KeySignature(-2))
    treble.append(tempo.MetronomeMark(number=100))

    treble_chords = (
        ["B-4", "D5", "F5"],
        ["E-5", "G5", "B-5"],
        ["F4", "A4", "C5"],
        ["B-4", "D5", "F5"],
    )
    bass_chords = (
        ["B-2", "D3", "F3"],
        ["E-3", "G3", "B-3"],
        ["F2", "A2", "C3"],
        ["B-2", "D3", "F3"],
    )
    for number, (upper, lower) in enumerate(zip(treble_chords, bass_chords), start=1):
        treble_measure = stream.Measure(number=number)
        treble_measure.append(chord.Chord(upper, quarterLength=4))
        treble.append(treble_measure)
        bass_measure = stream.Measure(number=number)
        bass_measure.append(chord.Chord(lower, quarterLength=4))
        bass.append(bass_measure)

    score.insert(0, treble)
    score.insert(0, bass)
    return score


@pytest.fixture
def bflat_analysis(tmp_path):
    path = tmp_path / "bflat.musicxml"
    _bflat_major_score().write("musicxml", fp=str(path))
    analysis, _ = run_analysis(str(path), use_llm=False)
    return analysis


def test_analyzer_names_use_display_spelling(bflat_analysis):
    assert bflat_analysis["key"]["detected_key"] == "Bb major"
    assert bflat_analysis["key"]["explicit_key_signature"] == "Bb major"
    assert all("B-" not in p["pitch"] and "E-" not in p["pitch"]
               for p in bflat_analysis["stats"]["most_common_pitches"])
    assert all("B-" not in ch["name"] and "E-" not in ch["name"]
               for ch in bflat_analysis["chords"]["most_common_chords"])
    assert "B-" not in bflat_analysis["annotations"]["simplified"][0]


def test_flat_key_still_gets_roman_numerals(bflat_analysis):
    """display_name must not silently disable Roman-numeral analysis."""
    assert bflat_analysis["chords"]["roman_numerals_sample"]
    assert any(bflat_analysis["chords"]["roman_numerals_sample"])


def test_reports_never_show_music21_flat_spelling(bflat_analysis):
    markdown = render_markdown(bflat_analysis)
    html = render_html(bflat_analysis)

    for report in (markdown, html):
        assert "Bb" in report
        assert "Eb" in report
        assert "B-" not in report
        assert "E-" not in report


def test_chord_guide_title_names_key_once(bflat_analysis, tmp_path):
    out = tmp_path / "chord-guide.pdf"
    render_chord_guide_pdf(
        bflat_analysis["chords"], str(out), key_info=bflat_analysis["key"],
    )

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(out)).pages)
    assert "Key: Bb major" in text
    assert "major major" not in text
