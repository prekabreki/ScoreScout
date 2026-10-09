"""The single labeling core shared by the music21 and .mscz export paths.

Historically the beginner label for a note or chord was computed twice: once
over music21 objects in :mod:`export.annotate` and once over raw .mscx XML with
a fixed MIDI-to-name table in :mod:`export.mscz_inject`. The two disagreed on
enharmonic spelling and on which notes the ``guided`` profile labels.

This module owns both decisions:

* :func:`label_for` turns a list of music21 pitches into the beginner text.
* :func:`select_labels` is the profile-keyed "which notes to label" policy,
  operating on a normalized :class:`PartEvent` stream that either path can
  build (music21 notes/chords, or XML ``Chord``/``Rest`` elements).

The .mscz path keeps only its output adapter: it spells each MuseScore note
from its ``<tpc>`` (exact spelling) with :func:`pitch_from_tpc`, falling back to
:func:`spell_midi` when no tpc is present, then labels through the same two
functions as the music21 path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from music21 import key as m21key
from music21 import pitch as m21pitch


# ---------------------------------------------------------------------------
# Label text
# ---------------------------------------------------------------------------

def _fix_flats(text: str) -> str:
    """Convert music21's flat notation to beginner-friendly: B- -> Bb, A-- -> Abb."""
    text = text.replace('--', 'bb')
    return re.sub(r'([A-G])-(?=[^a-zA-Z]|$)', r'\1b', text)


def _note_name(pitch_obj, octaves: bool) -> str:
    return _fix_flats(pitch_obj.nameWithOctave if octaves else pitch_obj.name)


def _simplify_chord_name(symbol: str) -> str:
    """Strip inversions for beginner readability: 'Bm7/D' -> 'Bm7'."""
    if not symbol:
        return symbol
    return _fix_flats(symbol.split("/")[0])


# Words that signal music21 internal classification, not a usable chord name
_JARGON_WORDS = frozenset([
    'incomplete', 'quartal', 'tetramirror', 'augmented-major',
    'diminished-major', 'whole-tone', 'chromatic', 'pentatonic',
    'secundal', 'tertian', 'altered', 'mirror', 'forte',
    'chord', 'triad',
])

# music21 "CaddD" / "EaddF#" style — a note letter immediately after "add".
_ADD_NOTE_RE = re.compile(r'add[A-G]', re.IGNORECASE)


def _is_usable_chord_name(name: str) -> bool:
    """Check if a chord name is short and beginner-friendly."""
    if not name or name.startswith("["):
        return False
    # Too long — no beginner chord name exceeds ~8 chars (e.g. "Cmaj7b5")
    if len(name) > 8:
        return False
    # Contains academic jargon
    low = name.lower()
    for word in _JARGON_WORDS:
        if word in low:
            return False
    if _ADD_NOTE_RE.search(name):
        return False
    return True


def _lookup_chord_name(chord_info: dict | None, measure, beat) -> str | None:
    """Try to find a chord name for a measure+beat from chord_info."""
    if not chord_info:
        return None
    progression = chord_info.get("chord_progression_full", [])
    if not progression:
        return None

    b = float(beat) if beat else 1.0

    # Find closest chord event at this measure+beat
    for entry in progression:
        if entry.get("measure") == measure and abs(entry.get("beat", 1.0) - b) < 0.25:
            name = entry.get("name", "")
            simplified = _simplify_chord_name(name) if name else ""
            if simplified and _is_usable_chord_name(simplified):
                return _fix_flats(simplified)
    return None


def label_for(
    pitches,
    chord_info: dict | None = None,
    measure=0,
    beat=1.0,
    octaves: bool = False,
) -> str | None:
    """Return the beginner label for one note or chord, or ``None`` for nothing.

    ``pitches`` is any iterable of music21 ``Pitch`` objects. The single source
    of truth for both export paths: a 3+ note chord prefers an analyzed chord
    name, a dyad shows both notes (collapsing an octave/pitch-class duplicate),
    and a single note shows its spelled name.
    """
    if not pitches:
        return None

    ordered = sorted(pitches, key=lambda p: p.midi)
    count = len(ordered)
    names = [_note_name(p, octaves) for p in ordered]

    if count == 1:
        return names[0]
    if count == 2:
        if ordered[0].pitchClass == ordered[1].pitchClass:
            return names[0]
        return "/".join(names)

    chord_name = _lookup_chord_name(chord_info, measure, beat)
    if chord_name and "power" in chord_name.lower():
        # Power chords are guitar jargon — show actual note names
        return "/".join(names)
    if chord_name:
        return chord_name
    if count > 3:
        return "/".join(names[:3]) + "+"
    return "/".join(names)


# ---------------------------------------------------------------------------
# Key-aware MIDI spelling (for the .mscz path)
# ---------------------------------------------------------------------------

# Tonic of the major key for each signed key-signature count. music21 spells a
# key signature's scale correctly from this, which is how a MIDI int becomes a
# flat or sharp the way the music21 path (reading spelled notes) would.
_MAJOR_TONIC = {
    0: 'C', 1: 'G', 2: 'D', 3: 'A', 4: 'E', 5: 'B', 6: 'F#', 7: 'C#',
    -1: 'F', -2: 'B-', -3: 'E-', -4: 'A-', -5: 'D-', -6: 'G-', -7: 'C-',
}


@lru_cache(maxsize=16)
def _diatonic_names(key_sharps: int) -> dict[int, str]:
    """Map pitch class -> spelled note name for a key signature's notes."""
    if key_sharps not in _MAJOR_TONIC:
        return {}
    scale = m21key.KeySignature(key_sharps).getScale()
    names: dict[int, str] = {}
    for p in scale.getPitches('C4', 'B4'):
        names.setdefault(p.pitchClass, p.name)
    return names


def spell_midi(midi: int, key_sharps: int = 0) -> m21pitch.Pitch:
    """Convert a MuseScore MIDI int to a music21 Pitch spelled for the key.

    Diatonic pitch classes take the key signature's spelling, so a flat-key
    piece prints ``Ab`` rather than music21's default ``G#``. Chromatic pitch
    classes keep music21's default MIDI spelling so the accidental stays
    explicit. This is the fallback when MuseScore did not write a ``<tpc>``
    (see :func:`pitch_from_tpc`), and it is what lets the .mscz path agree with
    the music21 path, which reads spelled notes straight out of the source.
    """
    octave = (midi // 12) - 1
    if key_sharps:
        name = _diatonic_names(key_sharps).get(midi % 12)
        if name:
            p = m21pitch.Pitch(name)
            p.octave = octave
            return p
    return m21pitch.Pitch(midi=midi)


# MuseScore writes a tonal pitch class ("tpc") on the line of fifths with C at
# 14; letter and accidental follow straight from the offset, so a Db in a flat
# key stays Db even though its MIDI int would default to C#.
_TPC_LETTERS = "FCGDAEB"
# Semitones above C within an octave for each natural letter (scientific pitch).
_LETTER_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def pitch_from_tpc(tpc: int, midi: int) -> m21pitch.Pitch:
    """Spell a MuseScore ``<tpc>`` at its sounding MIDI pitch.

    ``tpc`` is MuseScore's exact spelling as a position on the line of fifths
    (14 = C, 15 = G, 13 = F, 12 = Bb, 11 = Eb, 9 = Db, 21 = C#, 20 = F#, ...).
    That preserves the source's enharmonic spelling, which a bare MIDI int
    cannot. The octave comes from the MIDI pitch but is recomputed against the
    spelled letter, so edge spellings that cross C keep the right octave: a
    B#3 sounds as MIDI 60 and a Cb4 as MIDI 59.
    """
    letter = _TPC_LETTERS[(tpc + 1) % 7]
    alter = (tpc + 1) // 7 - 2
    accidental = "#" * alter if alter > 0 else "-" * -alter
    octave = (midi - _LETTER_SEMITONES[letter] - alter) // 12 - 1
    p = m21pitch.Pitch(letter + accidental)
    p.octave = octave
    return p


# ---------------------------------------------------------------------------
# "Which notes to label" policy
# ---------------------------------------------------------------------------

@dataclass
class PartEvent:
    """One note, chord or rest in document order within a part.

    Both paths build a list of these (one list per part) and hand them to
    :func:`select_labels`, so the density and priority rules live in exactly
    one place. ``target`` is the opaque object the caller attaches a label to
    (a music21 ``Note``/``Chord`` or an XML ``Chord`` element).
    """

    pitches: list
    measure: int
    beat: float
    quarter_length: float = 0.0
    is_rest: bool = False
    target: object = None


def _is_accidental_outside_key(pitch_obj, key_sharps: int) -> bool:
    """True if a pitch carries an accidental the key signature does NOT account for.

    ``key_sharps`` is the signed count of sharps (>0) or flats (<0) in the key
    signature. A pitch is "outside the key" when its sounding accidental differs
    from whatever the key signature already applies to that letter name — e.g.
    in G major (1 sharp) an F# is diatonic (returns False) but an F-natural or
    G# is chromatic (returns True). Naturals on letters the signature does not
    alter (e.g. F-natural in C major) carry no real accidental and return False.
    """
    acc = pitch_obj.accidental
    if acc is None or acc.alter == 0:
        return False

    # What alteration does the key signature impose on this pitch's letter?
    ks = m21key.KeySignature(key_sharps)
    expected = ks.accidentalByStep(pitch_obj.step)
    expected_alter = expected.alter if expected is not None else 0.0

    # Diatonic iff the pitch's accidental matches the signature's for that step.
    return acc.alter != expected_alter


def _select_all(parts: list[list[PartEvent]]) -> list[PartEvent]:
    return [ev for part in parts for ev in part if not ev.is_rest and ev.pitches]


def _select_guided(parts: list[list[PartEvent]], key_sharps: int) -> list[PartEvent]:
    """Sparse labels on the genuinely tricky notes, capped at 20% per piece."""
    candidates: list[tuple[int, PartEvent]] = []
    total = 0

    for part in parts:
        prev_midi = None
        rest_beats = 0.0
        seen_accidentals: set[str] = set()  # pitch names already labeled (per part)
        note_index = 0

        for ev in part:
            if ev.is_rest:
                rest_beats += ev.quarter_length
                continue

            total += 1
            priority = None  # None = don't label; lower number = higher priority

            # Rule 1: First note of the piece (per part)
            if note_index == 0:
                priority = 1

            # Rule 2: Accidental not in key — but only FIRST occurrence per part
            if priority is None:
                acc_pitches = [p for p in ev.pitches if _is_accidental_outside_key(p, key_sharps)]
                if any(p.name not in seen_accidentals for p in acc_pitches):
                    priority = 3
                for p in acc_pitches:
                    seen_accidentals.add(p.name)

            # Rule 3: Re-entry after a rest >= 2 beats
            if priority is None and rest_beats >= 2.0:
                priority = 2

            # Rule 4: Large interval jump (> 12th = 19 semitones)
            if priority is None and prev_midi is not None and ev.pitches:
                if abs(ev.pitches[0].midi - prev_midi) > 19:
                    priority = 4

            if priority is not None:
                candidates.append((priority, ev))

            # Update state
            if ev.pitches:
                prev_midi = ev.pitches[0].midi
            rest_beats = 0.0
            note_index += 1

    # Density cap: keep at most 20% of notes, preferring higher priority
    max_labels = int(total * 0.20)
    candidates.sort(key=lambda c: c[0])
    return [ev for _, ev in candidates[:max_labels]]


_POLICIES = {
    "all": _select_all,
    "guided": _select_guided,
}


def select_labels(parts: list[list[PartEvent]], profile, key_sharps: int = 0) -> list[PartEvent]:
    """Profile-keyed policy: return the events that should carry a label.

    ``profile`` may be an :class:`export.profiles.AnnotationProfile` or just the
    note-letters mode string (``"all"``/``"guided"``/``"none"``).
    """
    mode = getattr(profile, "note_letters", profile)
    policy = _POLICIES.get(mode)
    if policy is None:
        return []
    if mode == "guided":
        return policy(parts, key_sharps)
    return policy(parts)


def events_from_music21(stream_obj) -> list[PartEvent]:
    """Flatten a music21 part/score into the shared :class:`PartEvent` stream.

    Rests are kept so the guided policy can see re-entries; notes and chords
    carry their sorted pitches as targets for the caller to attach lyrics to.
    """
    events: list[PartEvent] = []
    for n in stream_obj.recurse().notesAndRests:
        measure = n.measureNumber if n.measureNumber is not None else 0
        beat = float(n.beat) if n.beat else 1.0
        if n.isRest:
            events.append(PartEvent(
                [], measure, beat, n.duration.quarterLength, is_rest=True,
            ))
        else:
            events.append(PartEvent(
                sorted(n.pitches, key=lambda p: p.midi), measure, beat,
                n.duration.quarterLength, target=n,
            ))
    return events
