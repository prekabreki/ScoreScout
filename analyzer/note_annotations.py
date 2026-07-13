"""Note letter annotations — readable note-by-note breakdown."""

import logging

from music21.stream import Score

log = logging.getLogger(__name__)


def generate_annotations(score: Score) -> dict:
    """Generate per-measure note annotations for each hand."""
    log.info("Generating note annotations...")
    parts = score.parts
    detailed = []
    simplified = []

    for part_idx, part in enumerate(parts):
        hand_label = "RH" if part_idx == 0 else "LH" if part_idx == 1 else f"P{part_idx+1}"

        for measure in part.getElementsByClass("Measure"):
            m_num = measure.number
            notes_in_measure = []

            for n in measure.notes:
                if n.isChord:
                    pitch_names = [p.nameWithOctave for p in n.pitches]
                    letters = [p.name for p in n.pitches]
                    notes_in_measure.append({
                        "beat": float(n.beat) if n.beat else 1.0,
                        "pitches": pitch_names,
                        "duration": n.duration.type,
                        "is_chord": True,
                    })
                else:
                    notes_in_measure.append({
                        "beat": float(n.beat) if n.beat else 1.0,
                        "pitches": [n.pitch.nameWithOctave],
                        "duration": n.duration.type,
                        "is_chord": False,
                    })

            if notes_in_measure:
                detailed.append({
                    "measure": m_num,
                    "hand": hand_label,
                    "notes": notes_in_measure,
                })

    # Build simplified letter-only view
    # Group by measure number
    from collections import defaultdict
    by_measure = defaultdict(dict)
    for entry in detailed:
        m = entry["measure"]
        hand = entry["hand"]
        letters = []
        for n in entry["notes"]:
            # Strip octave for simplified view
            simple = [p.rstrip("0123456789") for p in n["pitches"]]
            if len(simple) == 1:
                letters.append(simple[0])
            else:
                letters.append("[" + " ".join(simple) + "]")
        by_measure[m][hand] = " ".join(letters)

    for m_num in sorted(by_measure.keys()):
        parts_str = " ".join(
            f"{h}({notes})" for h, notes in sorted(by_measure[m_num].items())
        )
        simplified.append(f"M{m_num}: {parts_str}")

    log.debug("Generated %d detailed entries, %d simplified lines", len(detailed), len(simplified))

    return {
        "detailed": detailed[:200],  # limit for large scores
        "simplified": simplified,
    }
