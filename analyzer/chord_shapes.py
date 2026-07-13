"""Pitch-class-set to chord name lookup table.

Scope: ``lookup_chord`` is a *fallback* identifier for triads, 7th chords,
6th/sus/add chords, and 2-note dyads. The extended-chord rows (maj9/9/m9/11/13)
match only an *exact, complete* pitch-class set; in the live analysis pipeline
(``analyzer/chord_analysis.py``) Tier-1 ``harmony.chordSymbolFromChord`` usually
labels real 9th-13th voicings before this fallback runs, so those rows fire only
for clean, fully-spelled voicings. They are kept (and exercised by the harness in
issue #13) so the table is a complete, correct standalone lookup.

Quality-string namespaces (important — see issue #13 / audit M15):
  * Dyad intervals use the ``"int:"`` prefix (e.g. ``"int:m7"``).
  * Chord qualities are bare (e.g. ``"m7"``).
This keeps the two namespaces disjoint. Previously both a minor-7th *interval*
and a minor-7th *chord* used the bare quality ``"m7"`` (likewise ``"m6"``), so the
flat ``display`` table in ``format_chord_symbol`` had colliding keys: the chord
entry silently overwrote the dyad entry, and an m6/m7 dyad was mislabelled as a
minor-six / minor-seven *chord* instead of the interval ``(m6)`` / ``(m7)``.
"""

from music21 import pitch as m21pitch

# Interval names for 2-note dyads (semitone distance -> quality token).
# The bare interval names (used for display) are namespaced with an "int:"
# prefix as the lookup quality so they never collide with chord qualities of
# the same spelling (e.g. the m6/m7 *interval* vs the m6/m7 *chord*).
_INTERVAL_QUALITY_PREFIX = "int:"
INTERVAL_NAMES: dict[int, str] = {
    1: "m2", 2: "M2", 3: "m3", 4: "M3", 5: "P4", 6: "tri",
    7: "P5", 8: "m6", 9: "M6", 10: "m7", 11: "M7",
}

# Maps frozenset of semitone intervals (relative to root, 0-11) to chord quality name.
CHORD_SHAPES: dict[frozenset, str] = {
    # Triads
    frozenset({0, 4, 7}): "major",
    frozenset({0, 3, 7}): "minor",
    frozenset({0, 3, 6}): "dim",
    frozenset({0, 4, 8}): "aug",
    frozenset({0, 5, 7}): "sus4",
    frozenset({0, 2, 7}): "sus2",
    # Power chord
    frozenset({0, 7}): "5",
    # Seventh chords
    frozenset({0, 4, 7, 11}): "maj7",
    frozenset({0, 3, 7, 10}): "m7",
    frozenset({0, 4, 7, 10}): "7",
    frozenset({0, 3, 6, 10}): "m7b5",
    frozenset({0, 3, 6, 9}): "dim7",
    frozenset({0, 4, 8, 11}): "augmaj7",
    frozenset({0, 3, 7, 11}): "mMaj7",
    # Sixth chords
    frozenset({0, 4, 7, 9}): "6",
    frozenset({0, 3, 7, 9}): "m6",
    # Extended chords
    frozenset({0, 4, 7, 11, 2}): "maj9",
    frozenset({0, 4, 7, 10, 2}): "9",
    frozenset({0, 3, 7, 10, 2}): "m9",
    frozenset({0, 4, 7, 10, 2, 5}): "11",
    frozenset({0, 4, 7, 10, 2, 9}): "13",
    # Add chords
    frozenset({0, 4, 7, 2}): "add9",
    frozenset({0, 3, 7, 2}): "madd9",
    frozenset({0, 4, 7, 5}): "add11",
    # Sus with 7th
    frozenset({0, 5, 7, 10}): "7sus4",
    frozenset({0, 2, 7, 10}): "7sus2",
}


def lookup_chord(pitch_classes: set[int]) -> tuple[str, str] | None:
    """Try all possible roots and return (root_name, quality) or None.

    Args:
        pitch_classes: Set of MIDI pitch classes (0-11), e.g. {0, 4, 7} for C major.

    Returns:
        Tuple of (root_name, quality_string) like ("C", "major"), or None.
    """
    if len(pitch_classes) < 2:
        return None

    # For dyads (2 notes), use interval naming. The returned quality is
    # namespaced ("int:m7") so it can't collide with a same-named chord
    # quality ("m7") in format_chord_symbol's display table.
    if len(pitch_classes) == 2:
        pcs = sorted(pitch_classes)
        interval = (pcs[1] - pcs[0]) % 12
        if interval in INTERVAL_NAMES:
            root_name = m21pitch.Pitch(pcs[0]).name
            return (root_name, _INTERVAL_QUALITY_PREFIX + INTERVAL_NAMES[interval])
        return None

    for candidate_root in sorted(pitch_classes):
        intervals = frozenset((pc - candidate_root) % 12 for pc in pitch_classes)
        quality = CHORD_SHAPES.get(intervals)
        if quality is not None:
            root_name = m21pitch.Pitch(candidate_root).name
            return (root_name, quality)

    return None


def format_chord_symbol(root: str, quality: str) -> str:
    """Format a chord root + quality into a readable symbol like 'Cmaj7' or 'Am'."""
    # Dyad-interval keys are namespaced with "int:" so they don't collide with
    # chord qualities of the same spelling (the m6/m7 chord rows below used to
    # overwrite the m6/m7 *interval* rows — issue #13 / audit M15).
    p = _INTERVAL_QUALITY_PREFIX
    display = {
        # Intervals (dyads)
        p + "m2": "(m2)", p + "M2": "(M2)", p + "m3": "(m3)", p + "M3": "(M3)",
        # A bare perfect-5th dyad is a power chord, displayed "C5" (unchanged).
        p + "P4": "(P4)", p + "tri": "(tri)", p + "P5": "5", p + "m6": "(m6)",
        p + "M6": "(M6)", p + "m7": "(m7)", p + "M7": "(M7)",
        # Triads+
        "major": "",
        "minor": "m",
        "dim": "dim",
        "aug": "aug",
        "sus4": "sus4",
        "sus2": "sus2",
        "5": "5",
        "maj7": "maj7",
        "m7": "m7",
        "7": "7",
        "m7b5": "m7b5",
        "dim7": "dim7",
        "augmaj7": "augmaj7",
        "mMaj7": "mMaj7",
        "6": "6",
        "m6": "m6",
        "maj9": "maj9",
        "9": "9",
        "m9": "m9",
        "11": "11",
        "13": "13",
        "add9": "add9",
        "madd9": "madd9",
        "add11": "add11",
        "7sus4": "7sus4",
        "7sus2": "7sus2",
    }
    return root + display.get(quality, quality)
