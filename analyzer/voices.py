"""Per-voice note sequences for analyses that compare consecutive notes.

Walking ``part.recurse().notes`` interleaves the voices measure by measure
(voice 1 for a whole measure, then voice 2), so a voice change looks like a
leap. That inflates jump distance and invents melodic intervals. This helper
keeps each voice's notes together instead.
"""

from __future__ import annotations


def _voice_key(voice):
    """Group separate ``Voice`` objects that represent the same logical voice.

    Music21 nests one ``Voice`` per measure, so the same voice carries the same
    ``id`` across measures. Fall back to object identity when no id is set so
    distinct anonymous voices never collapse into one sequence.
    """
    voice_id = getattr(voice, "id", None)
    if voice_id is not None and str(voice_id) != "":
        return ("id", str(voice_id))
    return ("object", id(voice))


def _offset_in_part(note, part):
    try:
        return note.getOffsetInHierarchy(part)
    except Exception:
        return note.offset


def voice_sequences(part) -> list[list]:
    """Return each voice's notes/chords in time order.

    Voices nested inside measures (``Measure > Voice``) are included, unlike
    ``part.voices`` alone. A part without any ``Voice`` streams is returned as a
    single sequence.
    """
    voices = list(part.recurse().voices)
    if not voices:
        return [list(part.recurse().notes)]

    grouped: dict = {}
    order = []
    for voice in voices:
        key = _voice_key(voice)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].extend(voice.recurse().notes)

    sequences = []
    for key in order:
        notes = grouped[key]
        notes.sort(key=lambda n: _offset_in_part(n, part))
        sequences.append(notes)
    return sequences
