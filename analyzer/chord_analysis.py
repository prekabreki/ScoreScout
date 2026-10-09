"""Chord analysis — tiered identification pipeline, progression tracking."""

import json
import logging
from collections import Counter

import anthropic
from music21.stream import Score
from music21 import chord as m21chord, harmony, pitch as m21pitch, roman

from analyzer.chord_shapes import lookup_chord, format_chord_symbol
from analyzer.key_analysis import key_from_string
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

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
        (m21pitch.Pitch(pc).name for pc in pcs),
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
        name = m21pitch.Pitch(next(iter(pcs))).name
        return name, "", "harmony"

    # Tier 1: music21 harmony (on normalized chord — single octave, no doublings)
    try:
        normalized = m21chord.Chord([m21pitch.Pitch(pc) for pc in sorted(pcs)])
        cs = harmony.chordSymbolFromChord(normalized)
        if cs.figure and "Cannot Be Identified" not in cs.figure:
            rn = _try_roman(ch, key_obj)
            return cs.figure, rn, "harmony"
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
                return name, rn.figure, "roman"
        except Exception:
            pass

    # Tier 3: Pitch class set lookup
    result = lookup_chord(pcs)
    if result:
        root, quality = result
        symbol = format_chord_symbol(root, quality)
        rn = _try_roman(ch, key_obj)
        return symbol, rn, "lookup"

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
# Tier 4: Batch Claude API fallback
# ---------------------------------------------------------------------------

def _batch_identify_with_claude(
    unidentified: list[dict], key_context: str, use_llm: bool = True
) -> dict[str, str]:
    """Send unidentified chords to Claude in one batch call.

    Args:
        unidentified: list of {pitch_classes_label, measure, pitches}
        key_context: e.g. "e minor"
        use_llm: when False, makes no network call and returns {} (Tier 4 disabled)

    Returns:
        dict mapping pitch_classes_label -> identified chord name
    """
    if not use_llm:
        log.info("Tier 4: skipped (LLM disabled)")
        return {}
    if not ANTHROPIC_API_KEY or not unidentified:
        return {}

    # Deduplicate by pitch class label
    unique_sets = {}
    for entry in unidentified:
        label = entry["pitch_classes_label"]
        if label not in unique_sets:
            unique_sets[label] = entry["pitches"]

    if not unique_sets:
        return {}

    log.info("Tier 4: sending %d unique unidentified chords to Claude", len(unique_sets))

    prompt_data = [
        {"pitches": pitches, "label": label}
        for label, pitches in unique_sets.items()
    ]

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=(
                "You are a music theory expert. Identify each chord from its pitches. "
                "Return ONLY a JSON object mapping each label to a chord symbol "
                '(e.g., {"[C E G]": "C", "[D F# A C]": "D7"}). '
                'Use "?" for genuinely unidentifiable chords.'
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Key context: {key_context}\n\n"
                    f"Identify these chords:\n{json.dumps(prompt_data)}"
                ),
            }],
        )

    except Exception as e:
        log.warning("Tier 4 Claude API call failed: %s — falling back to lower tiers", e)
        return {}

    # Find the first text block rather than blindly indexing content[0].
    text = ""
    for block in getattr(message, "content", None) or []:
        block_text = getattr(block, "text", None)
        if block_text:
            text = block_text.strip()
            break
    if not text:
        log.warning("Tier 4 Claude response had no text content — falling back to lower tiers")
        return {}

    # Extract JSON from response (may be wrapped in markdown code block)
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("Tier 4 Claude response was not valid JSON (%s); raw text: %r", e, text)
        return {}

    log.debug("Tier 4 Claude response: %d chords identified", len(result))
    return result


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

def analyze_chords(score: Score, key_info: dict, use_llm: bool = True) -> dict:
    """Analyze chords with tiered identification pipeline.

    When use_llm is False (or no API key is set), the Tier-4 Claude batch call
    is skipped entirely — no network request is made — and unidentified chords
    degrade to their pitch-class labels (Tier 5).
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
        log.info("%d chords unidentified after tiers 1-3, trying Claude batch...", len(unidentified_batch))
        claude_results = _batch_identify_with_claude(
            unidentified_batch, detected or "unknown", use_llm=use_llm
        )

        for entry in unidentified_batch:
            idx = entry["index"]
            label = entry["pitch_classes_label"]
            claude_name = claude_results.get(label)

            if claude_name and claude_name != "?":
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
