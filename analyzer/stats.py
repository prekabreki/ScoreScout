"""Note statistics — counts, ranges, density, common intervals."""

import logging
from collections import Counter

from music21.stream import Score
from music21 import interval

log = logging.getLogger(__name__)


def analyze_stats(score: Score) -> dict:
    """Compute note counts, pitch ranges, density, and common intervals."""
    log.info("Running note statistics...")
    parts = score.parts
    per_hand = []

    all_pitches = []
    total_notes = 0
    intervals_list = []

    for i, part in enumerate(parts):
        notes = list(part.recurse().notes)
        pitches = []
        for n in notes:
            if n.isChord:
                pitches.extend([str(p) for p in n.pitches])
            else:
                pitches.append(str(n.pitch))

        lowest = min((p.midi for n in notes for p in (n.pitches if n.isChord else [n.pitch])), default=None)
        highest = max((p.midi for n in notes for p in (n.pitches if n.isChord else [n.pitch])), default=None)

        from music21 import pitch as m21pitch
        per_hand.append({
            "part_index": i,
            "part_name": part.partName or f"Part {i+1}",
            "note_count": len(notes),
            "lowest_note": m21pitch.Pitch(midi=lowest).nameWithOctave if lowest is not None else None,
            "highest_note": m21pitch.Pitch(midi=highest).nameWithOctave if highest is not None else None,
        })
        total_notes += len(notes)
        all_pitches.extend(pitches)

        # Collect melodic intervals
        prev = None
        for n in notes:
            if n.isChord:
                curr = n.pitches[-1]  # top note
            else:
                curr = n.pitch
            if prev:
                try:
                    iv = interval.Interval(prev, curr)
                    intervals_list.append(iv.simpleName)
                except Exception:
                    pass
            prev = curr

    # Most common pitches
    pitch_counter = Counter(all_pitches)
    most_common_pitches = [{"pitch": p, "count": c} for p, c in pitch_counter.most_common(10)]

    # Most common intervals
    interval_counter = Counter(intervals_list)
    most_common_intervals = [{"interval": iv, "count": c} for iv, c in interval_counter.most_common(10)]

    # Note density (notes per measure)
    measures = score.measureOffsetMap()
    num_measures = len(measures) if measures else 1
    density = round(total_notes / num_measures, 2)

    log.debug("Total notes: %d | Measures: %d | Density: %.2f notes/measure", total_notes, num_measures, density)

    return {
        "total_notes": total_notes,
        "num_measures": num_measures,
        "notes_per_measure": density,
        "per_hand": per_hand,
        "most_common_pitches": most_common_pitches,
        "most_common_intervals": most_common_intervals,
    }
