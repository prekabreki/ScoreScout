"""Tests for direct .mscz annotation injection (export/mscz_inject.py).

Fixtures are tiny .mscz archives built from hand-written MuseScore XML, so
these run without MuseScore installed. They pin the beat counter that drives
(measure, beat)-keyed chord-name lookups: a tuplet and a grace note must not
drift the running beat.
"""

import xml.etree.ElementTree as ET
import zipfile

from export import mscz_inject

_MSCX_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<museScore version="4.20">\n'
    '  <Score>\n'
    '    <Division>480</Division>\n'
    '    <Staff id="1">\n'
    '      <Measure>\n'
    '        <voice>%s</voice>\n'
    '      </Measure>\n'
    '    </Staff>\n'
    '  </Score>\n'
    '</museScore>\n'
)


def _note(pitch: int) -> str:
    return "<Note><pitch>%d</pitch></Note>" % pitch


def _chord(duration_type: str, pitches: list[int], marker: str = "") -> str:
    notes = "".join(_note(p) for p in pitches)
    return "<Chord>%s<durationType>%s</durationType>%s</Chord>" % (
        marker, duration_type, notes,
    )


def _build_mscz(tmp_path, voice_xml: str):
    mscz_path = tmp_path / "fixture.mscz"
    with zipfile.ZipFile(str(mscz_path), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("fixture.mscx", _MSCX_TEMPLATE % voice_xml)
    return mscz_path


def _labels(mscz_path) -> list[str | None]:
    """Return each chord's injected lyric text, in document order."""
    with zipfile.ZipFile(str(mscz_path)) as z:
        name = next(n for n in z.namelist() if n.endswith(".mscx"))
        root = ET.fromstring(z.read(name))
    result = []
    for chord in root.iter("Chord"):
        text = chord.find("Lyrics/text")
        result.append(text.text if text is not None else None)
    return result


def _progression() -> dict:
    return {
        "chord_progression_full": [
            {"measure": 1, "beat": 1.0, "name": "C"},
            {"measure": 1, "beat": 2.0, "name": "G"},
            {"measure": 1, "beat": 2.0 + 1.0 / 3.0, "name": "Am"},
            {"measure": 1, "beat": 2.0 + 2.0 / 3.0, "name": "F"},
            {"measure": 1, "beat": 3.0, "name": "C7"},
            # Decoy: only a drifted beat counter can land here.
            {"measure": 1, "beat": 4.0, "name": "Dm"},
        ]
    }


def test_tuplet_and_grace_do_not_drift_beats(tmp_path):
    """Labels land on the notes their (measure, beat) analysis belongs to."""
    voice = (
        _chord("quarter", [60, 64, 67])                       # beat 1.0 -> C
        + _chord("16th", [62], marker="<grace16/>")           # grace, beat 2.0
        + "<Tuplet><normalNotes>2</normalNotes>"
          "<actualNotes>3</actualNotes><baseNote>eighth</baseNote></Tuplet>"
        + _chord("eighth", [55, 59, 62])                      # beat 2.0    -> G
        + _chord("eighth", [57, 60, 64])                      # beat 2.3333 -> Am
        + _chord("eighth", [53, 57, 60])                      # beat 2.6667 -> F
        + "<endTuplet/>"
        + _chord("quarter", [48, 52, 55])                     # beat 3.0 -> C7
    )
    src = _build_mscz(tmp_path, voice)
    out = tmp_path / "annotated.mscz"

    mscz_inject.inject_mscz(str(src), str(out), chord_info=_progression())

    assert _labels(out) == ["C", "D", "G", "Am", "F", "C7"]


def test_plain_measure_beats_unchanged(tmp_path):
    """A measure without tuplets/grace keeps the pre-fix beat behaviour."""
    voice = (
        _chord("quarter", [60, 64, 67])   # beat 1.0 -> C
        + _chord("quarter", [55, 59, 62]) # beat 2.0 -> G
        + _chord("half", [57, 60, 64])    # beat 3.0 -> C7
    )
    src = _build_mscz(tmp_path, voice)
    out = tmp_path / "annotated.mscz"

    mscz_inject.inject_mscz(str(src), str(out), chord_info=_progression())

    assert _labels(out) == ["C", "G", "C7"]


def test_dur_quarters_scales_tuplet_and_zeroes_grace():
    quarter = ET.fromstring("<Chord><durationType>quarter</durationType></Chord>")
    dotted = ET.fromstring(
        "<Chord><durationType>quarter</durationType><dots>1</dots></Chord>"
    )
    grace = ET.fromstring(
        "<Chord><grace16/><durationType>16th</durationType></Chord>"
    )

    assert mscz_inject._dur_quarters(quarter) == 1.0
    assert mscz_inject._dur_quarters(dotted) == 1.5
    # 3:2 triplet: written eighth lasts 2/3 of an eighth.
    assert mscz_inject._dur_quarters(quarter, tuplet_ratio=2.0 / 3.0) == 2.0 / 3.0
    assert mscz_inject._dur_quarters(grace) == 0.0


def test_tuplet_ratio_from_element():
    triplet = ET.fromstring(
        "<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>"
    )
    no_ratio = ET.fromstring("<Tuplet><baseNote>eighth</baseNote></Tuplet>")
    broken = ET.fromstring(
        "<Tuplet><normalNotes>0</normalNotes><actualNotes>3</actualNotes></Tuplet>"
    )

    assert mscz_inject._tuplet_ratio(triplet) == 2.0 / 3.0
    assert mscz_inject._tuplet_ratio(no_ratio) == 1.0
    assert mscz_inject._tuplet_ratio(broken) == 1.0
