"""Shared fixtures and helpers for the test suite.

No test makes a real network call: the Anthropic client is mocked/monkeypatched
wherever LLM paths are exercised, and ``ANTHROPIC_API_KEY`` is empty in CI.

Scores are built programmatically with music21 (or written to a temporary
MusicXML file and parsed through the real parser) so we never depend on the
user's personal score library.
"""

from __future__ import annotations

import pytest
from music21 import stream, note, meter, tempo, chord as m21chord


def _melody_part(notes_with_octave: list[str]) -> stream.Part:
    """Build a single-voice melodic Part from note names like 'C4'."""
    p = stream.Part()
    p.append(meter.TimeSignature("4/4"))
    p.append(tempo.MetronomeMark(number=120))
    for n in notes_with_octave:
        p.append(note.Note(n, quarterLength=1.0))
    return p


@pytest.fixture
def melodic_score() -> stream.Score:
    """A purely-melodic score (single part, no chords)."""
    s = stream.Score()
    s.insert(0, _melody_part(["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]))
    return s


@pytest.fixture
def grand_staff_score() -> stream.Score:
    """A two-part piano grand staff with a melody and a chordal bass.

    The bass part carries explicit Chord objects so chord/difficulty analysis
    has real harmony to chew on.
    """
    s = stream.Score()

    treble = stream.Part()
    treble.append(meter.TimeSignature("4/4"))
    treble.append(tempo.MetronomeMark(number=120))
    for n in ["E5", "D5", "C5", "D5", "E5", "E5", "E5", "D5"]:
        treble.append(note.Note(n, quarterLength=1.0))

    bass = stream.Part()
    bass.append(meter.TimeSignature("4/4"))
    # C major, G major, A minor, F major triads
    for pcs in (["C3", "E3", "G3"], ["G2", "B2", "D3"],
                ["A2", "C3", "E3"], ["F2", "A2", "C3"]):
        bass.append(m21chord.Chord(pcs, quarterLength=2.0))

    s.insert(0, treble)
    s.insert(0, bass)
    return s


@pytest.fixture
def parsed_melodic_score(tmp_path):
    """Round-trip a synthetic melodic score through the real parse_score().

    Writing to a temp .musicxml file and parsing it exercises the actual
    parser validation path without touching any external score.
    """
    from analyzer.parser import parse_score

    s = stream.Score()
    s.insert(0, _melody_part(["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]))
    xml_path = tmp_path / "synthetic.musicxml"
    s.write("musicxml", fp=str(xml_path))
    return parse_score(str(xml_path))
