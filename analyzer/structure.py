"""Structure detection — section boundaries, key changes, repeats."""

import logging

from music21.stream import Score
from music21.analysis.discrete import KrumhanslSchmuckler

log = logging.getLogger(__name__)


def analyze_structure(score: Score) -> dict:
    """Detect sections, key changes, and structural features."""
    log.info("Running structure analysis...")
    parts = score.parts
    if not parts:
        log.debug("Score has no parts — no structure to analyze")
        return {"sections": [], "key_changes": [], "repeats": [], "num_measures": 0}
    measures = list(parts[0].getElementsByClass("Measure"))
    num_measures = len(measures)

    if num_measures == 0:
        return {"sections": [], "key_changes": [], "repeats": [], "num_measures": 0}

    # Windowed key analysis to find modulations
    key_changes = []
    window_size = max(4, num_measures // 8)
    ks_analyzer = KrumhanslSchmuckler()
    prev_key = None

    for start in range(0, num_measures, window_size):
        end = min(start + window_size, num_measures)
        excerpt = score.measures(start + 1, end)
        try:
            result = ks_analyzer.getSolution(excerpt)
            current_key = str(result) if result else None
        except Exception as exc:
            log.debug("Key analysis failed for window m.%d-%d: %s", start + 1, end, exc)
            current_key = None

        if current_key and current_key != prev_key:
            if prev_key is not None:
                key_changes.append({
                    "from_key": prev_key,
                    "to_key": current_key,
                    "at_measure": start + 1,
                })
            prev_key = current_key

    # Detect rehearsal marks or section labels
    sections = []
    for el in score.recurse().getElementsByClass("RehearsalMark"):
        sections.append({
            "label": el.content if hasattr(el, "content") else str(el),
            "measure": el.measureNumber if hasattr(el, "measureNumber") else None,
        })

    # Detect repeat signs
    repeats = []
    for rb in score.recurse().getElementsByClass("Repeat"):
        repeats.append({
            "direction": rb.direction if hasattr(rb, "direction") else str(rb),
            "measure": rb.measureNumber if hasattr(rb, "measureNumber") else None,
        })
    for rb in score.recurse().getElementsByClass("RepeatBracket"):
        repeats.append({
            "type": "bracket",
            "number": rb.number if hasattr(rb, "number") else None,
            "measure": rb.measureNumber if hasattr(rb, "measureNumber") else None,
        })

    # Dynamic changes as section hints
    dynamics = []
    for dyn in score.recurse().getElementsByClass("Dynamic"):
        dynamics.append({
            "marking": dyn.value if hasattr(dyn, "value") else str(dyn),
            "measure": dyn.measureNumber if hasattr(dyn, "measureNumber") else None,
        })

    log.debug("Sections: %d | Key changes: %d | Repeats: %d | Dynamics: %d",
              len(sections), len(key_changes), len(repeats), len(dynamics))

    return {
        "num_measures": num_measures,
        "sections": sections,
        "key_changes": key_changes,
        "repeats": repeats,
        "dynamics": dynamics,
    }
