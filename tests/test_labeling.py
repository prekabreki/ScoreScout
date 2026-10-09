"""Tests for the shared labeling core (issue #3).

The two export paths used to compute a beginner label twice: over music21 in
``export.annotate`` and over raw .mscx XML with a fixed MIDI-to-name table in
``export.mscz_inject``. These pin the one core both now use, and that a
flat-key piece gets the same spelling and the same guided selection through
either path.
"""

import xml.etree.ElementTree as ET
import zipfile

import pytest
from music21 import meter, note, pitch, stream

from export import annotate, labeling, mscz_inject
from export.profiles import get_profile

# ---------------------------------------------------------------------------
# Shared function / policy
# ---------------------------------------------------------------------------

def test_both_paths_label_through_one_core():
    """Both export paths call the same label function and selection policy."""
    assert annotate.label_for is labeling.label_for
    assert annotate.select_labels is labeling.select_labels
    assert mscz_inject.label_for is labeling.label_for
    assert mscz_inject.select_labels is labeling.select_labels


# ---------------------------------------------------------------------------
# label_for
# ---------------------------------------------------------------------------

def test_label_for_single_note():
    assert labeling.label_for([pitch.Pitch("B-4")]) == "Bb"


def test_label_for_dyad_collapses_octave_duplicate():
    dyad = [pitch.Pitch("C4"), pitch.Pitch("C5")]
    assert labeling.label_for(dyad) == "C"


def test_label_for_dyad_shows_both_pitches():
    dyad = [pitch.Pitch("C4"), pitch.Pitch("E4")]
    assert labeling.label_for(dyad) == "C/E"


def test_label_for_triad_prefers_analyzed_chord_name():
    chord_info = {"chord_progression_full": [
        {"measure": 1, "beat": 1.0, "name": "C"},
    ]}
    pitches = [pitch.Pitch("C4"), pitch.Pitch("E4"), pitch.Pitch("G4")]
    assert labeling.label_for(pitches, chord_info, 1, 1.0) == "C"


def test_label_for_many_notes_falls_back_to_three_plus():
    pitches = [pitch.Pitch(n) for n in ("C4", "E4", "G4", "B4", "D5")]
    assert labeling.label_for(pitches) == "C/E/G+"


def test_label_for_empty_is_none():
    assert labeling.label_for([]) is None


# ---------------------------------------------------------------------------
# spell_midi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("midi,key_sharps,expected", [
    (68, -3, "Ab"),   # Ab in Eb major (3 flats)
    (63, -3, "Eb"),
    (70, -2, "Bb"),   # Bb in Bb major (2 flats)
    (66, 2, "F#"),    # F# in D major (2 sharps)
    (61, 0, "C#"),    # no key signature: music21 default spelling
])
def test_spell_midi_follows_key_signature(midi, key_sharps, expected):
    assert labeling.spell_midi(midi, key_sharps).name.replace("-", "b") == expected


def test_spell_midi_keeps_chromatic_spelling():
    # Ab (pc 8) is not diatonic in Bb major, so its default sharp spelling stays.
    assert labeling.spell_midi(68, -2).name == "G#"


# ---------------------------------------------------------------------------
# select_labels guided policy
# ---------------------------------------------------------------------------

def _events(pitches_by_part):
    parts = []
    for part_pitches in pitches_by_part:
        parts.append([
            labeling.PartEvent([pitch.Pitch(p)], measure=1, beat=float(i + 1), target=i)
            for i, p in enumerate(part_pitches)
        ])
    return parts


def test_guided_keeps_first_note_and_accidental():
    # Ten notes (cap = 2): the first, then the first chromatic G# in C major.
    parts = _events([["C4", "D4", "E4", "F4", "G#4", "A4", "B4", "C5", "D5", "E5"]])
    selected = labeling.select_labels(parts, "guided", key_sharps=0)
    targets = [ev.target for ev in selected]
    assert targets == [0, 4]


def test_guided_drops_everything_under_cap():
    # 20% of 4 notes is 0, so even the first note is capped out.
    parts = _events([["C4", "D4", "E4", "F4"]])
    assert labeling.select_labels(parts, "guided", key_sharps=0) == []


def test_none_profile_labels_nothing():
    parts = _events([["C4", "D4"]])
    assert labeling.select_labels(parts, "none") == []


# ---------------------------------------------------------------------------
# Cross-path parity
# ---------------------------------------------------------------------------

_MSCX_MULTI = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<museScore version="4.20">\n'
    '  <Score>\n'
    '    <Division>480</Division>\n'
    '    <Staff id="1">\n'
    '%s'
    '    </Staff>\n'
    '  </Score>\n'
    '</museScore>\n'
)


def _chord_xml(midi, duration_type):
    return "<Chord><durationType>%s</durationType><Note><pitch>%d</pitch></Note></Chord>" % (
        duration_type, midi,
    )


def _rest_xml(duration_type):
    return "<Rest><durationType>%s</durationType></Rest>" % duration_type


def _flat_key_mscx():
    """Three 4/4 measures in Eb major: a re-entry after a half rest, no leaps."""
    m1 = "<Measure><voice>%s%s%s</voice></Measure>" % (
        _chord_xml(63, "quarter"), _chord_xml(70, "quarter"), _rest_xml("half"),
    )
    m2 = "<Measure><voice>%s</voice></Measure>" % "".join(
        _chord_xml(m, "quarter") for m in (68, 67, 65, 63)
    )
    m3 = "<Measure><voice>%s</voice></Measure>" % "".join(
        _chord_xml(m, "quarter") for m in (58, 63, 65, 67)
    )
    return _MSCX_MULTI % (m1 + m2 + m3)


def _flat_key_score():
    part = stream.Part()
    part.append(meter.TimeSignature("4/4"))

    def measure(number, items):
        m = stream.Measure(number=number)
        for name, ql in items:
            if name is None:
                m.append(note.Rest(quarterLength=ql))
            else:
                m.append(note.Note(name, quarterLength=ql))
        return m

    part.append(measure(1, [("E-4", 1), ("B-4", 1), (None, 2)]))
    part.append(measure(2, [("A-4", 1), ("G4", 1), ("F4", 1), ("E-4", 1)]))
    part.append(measure(3, [("B-3", 1), ("E-4", 1), ("F4", 1), ("G4", 1)]))
    score = stream.Score()
    score.insert(0, part)
    return score


def _music21_labels(score):
    out = []
    for n in score.recurse().notes:
        if not n.lyrics:
            continue
        midis = tuple(p.midi for p in (n.pitches if n.isChord else [n.pitch]))
        out.append((n.measureNumber, round(float(n.beat), 3), midis, n.lyrics[0].text))
    return out


def _mscz_labels(path):
    with zipfile.ZipFile(str(path)) as z:
        name = next(n for n in z.namelist() if n.endswith(".mscx"))
        root = ET.fromstring(z.read(name))
    score_el = root.find(".//Score")
    staves = [c for c in score_el if c.tag == "Staff"]
    parts = mscz_inject._collect_events(staves, -3)
    out = []
    for ev in parts[0]:
        if ev.is_rest:
            continue
        text = ev.target.find("Lyrics/text")
        if text is not None:
            midis = tuple(p.midi for p in ev.pitches)
            out.append((ev.measure, round(ev.beat, 3), midis, text.text))
    return out


def test_guided_labels_same_notes_and_spelling_both_paths(tmp_path):
    key_info = {"sharps_flats_count": -3}

    annotated = annotate.annotate_score(
        _flat_key_score(), get_profile("guided"), key_info=key_info,
    )
    m21_labels = _music21_labels(annotated)

    src = tmp_path / "flat.mscz"
    with zipfile.ZipFile(str(src), "w") as z:
        z.writestr("flat.mscx", _flat_key_mscx())
    out = tmp_path / "flat_out.mscz"
    mscz_inject.inject_mscz(
        str(src), str(out), profile_name="guided", key_info=key_info,
    )

    assert _mscz_labels(out) == m21_labels
    # First note and the re-entry, spelled as flats in a flat key.
    assert [text for *_, text in m21_labels] == ["Eb", "Ab"]
