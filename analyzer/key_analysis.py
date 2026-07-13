"""Key detection — algorithmic analysis + explicit key signature extraction."""

import logging

from music21.stream import Score
from music21.analysis.discrete import KrumhanslSchmuckler

log = logging.getLogger(__name__)


_SHARPS_FLATS = {
    0: [],
    1: ["F#"], 2: ["F#", "C#"], 3: ["F#", "C#", "G#"],
    4: ["F#", "C#", "G#", "D#"], 5: ["F#", "C#", "G#", "D#", "A#"],
    6: ["F#", "C#", "G#", "D#", "A#", "E#"],
    7: ["F#", "C#", "G#", "D#", "A#", "E#", "B#"],
    -1: ["Bb"], -2: ["Bb", "Eb"], -3: ["Bb", "Eb", "Ab"],
    -4: ["Bb", "Eb", "Ab", "Db"], -5: ["Bb", "Eb", "Ab", "Db", "Gb"],
    -6: ["Bb", "Eb", "Ab", "Db", "Gb", "Cb"],
    -7: ["Bb", "Eb", "Ab", "Db", "Gb", "Cb", "Fb"],
}


def key_from_string(detected: str | None):
    """Parse a detected-key string (e.g. "e minor" or "C") into a music21 Key.

    Returns None if the string is empty or cannot be parsed. This is the inverse
    of the ``str(Key)`` used to populate ``detected_key`` in ``analyze_key``;
    keeping both sides here means callers never re-implement the split.
    """
    if not detected:
        return None
    from music21 import key as m21key
    try:
        parts = detected.strip().split()
        if len(parts) == 2:
            return m21key.Key(parts[0], parts[1])
        return m21key.Key(parts[0])
    except Exception as e:
        log.debug("Could not create Key from '%s': %s", detected, e)
        return None


def analyze_key(score: Score) -> dict:
    """Detect key using both algorithmic analysis and explicit key signature."""
    log.info("Running key analysis...")

    # Algorithmic detection
    log.debug("Running Krumhansl-Schmuckler algorithm")
    ks_analyzer = KrumhanslSchmuckler()
    result = ks_analyzer.getSolution(score)

    algo_key = str(result) if result else None
    algo_mode = result.mode if result else None

    # Explicit key signature from score.
    #
    # M12: rather than blindly trusting the *first* KeySignature in document
    # order (which, in a multi-part score with a transposing instrument, could
    # be that instrument's written sig rather than concert pitch), take the most
    # common sharp-count among all parts' opening signatures. For the common
    # case of a single signature — or a piano grand staff where every part
    # shares one — this is identical to "the first one", so key detection for
    # existing scores is unchanged. ``has_key_signature`` distinguishes a real
    # 0-sharp signature (C major / A minor) from a score with no signature at
    # all, instead of silently defaulting both to 0.
    explicit_key = None
    explicit_sharps = 0
    all_sigs = list(score.recurse().getElementsByClass("KeySignature"))
    has_key_signature = bool(all_sigs)
    if all_sigs:
        from collections import Counter
        sharp_counts = Counter(ks.sharps for ks in all_sigs)
        explicit_sharps = sharp_counts.most_common(1)[0][0]
        # Describe using the first signature that matches the winning sharp count.
        for ks in all_sigs:
            if ks.sharps == explicit_sharps and hasattr(ks, "asKey"):
                explicit_key = str(ks.asKey())
                break

    # Build accidentals list
    accidentals = _SHARPS_FLATS.get(explicit_sharps, [])

    disagree = False
    if algo_key and explicit_key:
        # Normalize for comparison (strip whitespace)
        disagree = algo_key.replace(" ", "").lower() != explicit_key.replace(" ", "").lower()

    log.debug("Detected key: %s | Explicit key sig: %s | Disagree: %s", algo_key, explicit_key, disagree)
    if disagree:
        log.info("Key disagreement — algorithmic=%s vs explicit=%s", algo_key, explicit_key)

    return {
        "detected_key": algo_key,
        "detected_mode": algo_mode,
        "explicit_key_signature": explicit_key,
        "sharps_flats_count": explicit_sharps,
        "has_key_signature": has_key_signature,
        "accidentals": accidentals,
        "key_disagreement": disagree,
    }
