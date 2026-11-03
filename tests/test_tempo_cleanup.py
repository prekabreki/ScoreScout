"""Tempo cleanup must handle MuseScore's playback-only tempo marks.

MuseScore exports hidden tempos with no written number, only a sounding one;
reading ``.number`` alone crashed the proximity pass with a TypeError.
"""
from music21 import note, stream, tempo

from export.annotate import _strip_granular_tempos


def _sounding(bpm):
    mark = tempo.MetronomeMark()
    mark.numberSounding = bpm
    return mark


def _score_with_marks(marks):
    part = stream.Part()
    for i, mark in enumerate(marks, start=1):
        m = stream.Measure(number=i)
        m.insert(0, mark)
        m.append(note.Note("C4", type="whole"))
        part.append(m)
    score = stream.Score()
    score.insert(0, part)
    return score


def _bpms(score):
    return [m.number if m.number is not None else m.numberSounding
            for m in score.recurse().getElementsByClass(tempo.MetronomeMark)]


def test_sounding_only_marks_are_cleaned_like_written_ones():
    # 80 -> 165 jump, a near-duplicate 160, a far jump to 60, back up to 165.
    marks = [tempo.MetronomeMark(number=80), _sounding(165), _sounding(160),
             _sounding(60), _sounding(165), tempo.MetronomeMark(number=135)]
    score = _score_with_marks(marks)
    _strip_granular_tempos(score)
    # 160 is within 10 BPM of 165 and one measure later, so it is dropped.
    assert _bpms(score) == [80, 165, 60, 165, 135]


def test_text_only_mark_survives_cleanup():
    marks = [tempo.MetronomeMark(number=80), _sounding(165), _sounding(60),
             tempo.MetronomeMark(text="a tempo"), _sounding(165), _sounding(70)]
    score = _score_with_marks(marks)
    _strip_granular_tempos(score)
    texts = [m.text for m in score.recurse().getElementsByClass(tempo.MetronomeMark)
             if m.number is None and m.numberSounding is None]
    assert texts == ["a tempo"]
