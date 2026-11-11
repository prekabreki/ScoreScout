"""Chord analysis — tiered identification pipeline, progression tracking."""

import logging
from collections import Counter

from music21.stream import Score
from music21 import chord as m21chord, harmony, pitch as m21pitch, roman

from analyzer.chord_shapes import lookup_chord, format_chord_symbol
from analyzer.key_analysis import key_from_string
from analyzer.spelling import display_name

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: normalize chord to pitch classes
# ---------------------------------------------------------------------------

def _normalize_to_pitch_classes(ch) -> set[int]:
    """Collapse a chord's pitches to unique pitch classes (0-11)."""
    return set(p.midi % 12 for p in ch.pitches)


def _pitch_classes_to_label(pcs: set[int]) -> str:
    """Format a set of pitch classes as a readable label like '[C E G#]'."""
    names = sorted(
        (display_name(m21pitch.Pitch(pc).name) for pc in pcs),
        key=lambda n: m21pitch.Pitch(n).midi % 12,
    )
    return "[" + " ".join(names) + "]"


def _is_verbose_interval_name(name: str) -> bool:
    """True for music21's descriptive interval names rather than chord symbols.

    `RomanNumeral.pitchedCommonName` falls back to strings like
    "Major Sixth with octave doublings above F" when a chord cannot be named
    as an ordinary symbol. These read as prose and pollute the unique-chords
    list and every report that consumes it; callers should use the compact
    Roman figure instead.
    """
    return " above " in name


# ---------------------------------------------------------------------------
# Tiered identification
# ---------------------------------------------------------------------------

def _identify_chord_tiered(ch, pcs: set[int], key_obj) -> tuple[str, str, str]:
    """Identify a chord through the tiered pipeline.

    Returns (chord_name, roman_numeral_or_empty, tier_used).
    Tier names: 'harmony', 'roman', 'lookup', 'fallback'.
    """
    # Single pitch class = unison, just name the note
    if len(pcs) == 1:
        name = display_name(m21pitch.Pitch(next(iter(pcs))).name)
        return name, "", "harmony"

    # Tier 1: music21 harmony (on a voicing above the chord's real bass,
    # de-duplicated to one pitch per class so doublings don't skew the symbol).
    try:
        bass = m21pitch.Pitch(ch.bass().midi)
        bass_pc = bass.midi % 12
        voiced = [bass]
        for pc in sorted(pcs):
            if pc == bass_pc:
                continue
            p = m21pitch.Pitch(pc, octave=bass.octave)
            while p.midi <= bass.midi:
                p.octave += 1
            voiced.append(p)
        normalized = m21chord.Chord(voiced)
        cs = harmony.chordSymbolFromChord(normalized)
        if cs.figure and "Cannot Be Identified" not in cs.figure:
            rn = _try_roman(ch, key_obj)
            return display_name(cs.figure), rn, "harmony"
    except Exception:
        pass

    # Tier 2: Roman numeral analysis (more tolerant of voicings)
    if key_obj:
        try:
            rn = roman.romanNumeralFromChord(ch, key_obj)
            if rn.figure and rn.figure != "?":
                name = rn.pitchedCommonName or rn.figure
                # pitchedCommonName degrades to verbose interval prose for
                # unusual voicings; keep the compact Roman figure instead.
                if _is_verbose_interval_name(name):
                    name = rn.figure
                return display_name(name), rn.figure, "roman"
        except Exception:
            pass

    # Tier 3: Pitch class set lookup
    result = lookup_chord(pcs)
    if result:
        root, quality = result
        symbol = format_chord_symbol(root, quality)
        rn = _try_roman(ch, key_obj)
        return display_name(symbol), rn, "lookup"

    # Not identified — will be collected for Tier 4 (batch Claude) or Tier 5 (fallback)
    return "", "", "unidentified"


def _try_roman(ch, key_obj) -> str:
    """Best-effort Roman numeral, returns '' on failure."""
    if not key_obj:
        return ""
    try:
        rn = roman.romanNumeralFromChord(ch, key_obj)
        return rn.figure if rn.figure else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

def analyze_chords(
    score: Score,
    key_info: dict,
    use_llm: bool = True,
    chord_namer=None,
) -> dict:
    """Analyze chords with tiered identification pipeline.

    When use_llm is False (or no chord_namer is supplied), the Tier-4 Claude
    batch call is skipped entirely — no network request is made — and
    unidentified chords degrade to their pitch-class labels (Tier 5).

    ``chord_namer`` is the Tier-4 callable, e.g.
    ``llm.chord_names.identify_chords(unidentified, key_context) -> dict``.
    It is injected by ``core.pipeline.run_analysis`` so this module stays free
    of Anthropic/config imports.
    """
    log.info("Running chord analysis...")
    log.debug("Chordifying score...")
    chordified = score.chordify()
    chords = list(chordified.recurse().getElementsByClass("Chord"))
    log.debug("Found %d chord events after chordify", len(chords))

    if not chords:
        return {
            "total_chords": 0,
            "unique_chords": [],
            "chord_progression": [],
            "most_common_chords": [],
            "complex_chords": [],
            "identification_stats": {},
        }

    # Resolve key for Roman numeral analysis (shared parser — see key_analysis)
    detected = key_info.get("detected_key")
    key_obj = key_from_string(detected)

    # Run tiered identification on each chord
    chord_names = []
    roman_numerals = []
    chord_progression = []
    complex_chords = []
    unidentified_batch = []
    tier_counts = {"harmony": 0, "roman": 0, "lookup": 0, "claude": 0, "fallback": 0}

    for ch in chords:
        pcs = _normalize_to_pitch_classes(ch)
        name, rn_fig, tier = _identify_chord_tiered(ch, pcs, key_obj)

        if tier == "unidentified":
            # Collect for Tier 4 batch
            label = _pitch_classes_to_label(pcs)
            unidentified_batch.append({
                "pitch_classes_label": label,
                "pitches": [str(p) for p in ch.pitches],
                "measure": ch.measureNumber,
                "beat": float(ch.beat) if ch.beat else 1.0,
                "index": len(chord_names),  # to patch back later
            })
            chord_names.append(label)  # placeholder
            roman_numerals.append(rn_fig)
        else:
            tier_counts[tier] += 1
            chord_names.append(name)
            roman_numerals.append(rn_fig)

        chord_progression.append({
            "name": name if tier != "unidentified" else "",
            "measure": ch.measureNumber,
            "beat": float(ch.beat) if ch.beat else 1.0,
            "pitches": [str(p) for p in ch.pitches],
        })

        if len(ch.pitches) > 4:
            complex_chords.append({
                "name": name if tier != "unidentified" else _pitch_classes_to_label(pcs),
                "measure": ch.measureNumber,
                "num_notes": len(ch.pitches),
            })

    # Tier 4: Batch Claude API call for remaining unknowns
    if unidentified_batch:
        claude_results = {}
        if use_llm and chord_namer is not None:
            log.info("%d chords unidentified after tiers 1-3, trying Claude batch...", len(unidentified_batch))
            claude_results = chord_namer(
                unidentified_batch, detected or "unknown"
            )

        for entry in unidentified_batch:
            idx = entry["index"]
            label = entry["pitch_classes_label"]
            claude_name = claude_results.get(label)

            if claude_name and claude_name != "?":
                claude_name = display_name(claude_name)
                chord_names[idx] = claude_name
                chord_progression[idx]["name"] = claude_name
                tier_counts["claude"] += 1
            else:
                # Tier 5: keep the pitch class label
                chord_progression[idx]["name"] = label
                tier_counts["fallback"] += 1

    # Summary stats — exclude Tier-5 bracket placeholders (e.g. "[C E G#]"),
    # which are pitch-class labels for unidentified chords, not real chord names,
    # and any residual verbose interval prose (e.g. "Perfect Octave above D").
    named_chords = [
        n
        for n in chord_names
        if n and not n.startswith("[") and not _is_verbose_interval_name(n)
    ]
    counter = Counter(named_chords)
    most_common = [{"name": n, "count": c} for n, c in counter.most_common(15)]
    unique = sorted(set(named_chords))

    total_identified = tier_counts["harmony"] + tier_counts["roman"] + tier_counts["lookup"] + tier_counts["claude"]
    pct = round(total_identified / max(len(chords), 1) * 100, 1)
    log.info(
        "Chord identification: %d/%d (%.1f%%) — harmony=%d, roman=%d, lookup=%d, claude=%d, fallback=%d",
        total_identified, len(chords), pct,
        tier_counts["harmony"], tier_counts["roman"], tier_counts["lookup"],
        tier_counts["claude"], tier_counts["fallback"],
    )

    return {
        "total_chords": len(chords),
        "unique_chords": unique,
        "most_common_chords": most_common,
        # Single canonical progression list (M11): consumers slice as needed.
        # The former "chord_progression_sample" (a [:64] slice) duplicated this
        # and bloated the cached/serialized dict, so it was dropped.
        "chord_progression_full": chord_progression,
        "roman_numerals_sample": roman_numerals[:64],
        "complex_chords": complex_chords,
        "identification_stats": tier_counts,
    }
