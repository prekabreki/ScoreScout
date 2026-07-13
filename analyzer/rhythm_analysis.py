"""Rhythm and tempo analysis — time signatures, tempo, syncopation."""

import logging

from music21.stream import Score

log = logging.getLogger(__name__)


def analyze_rhythm(score: Score) -> dict:
    """Extract time signatures, tempo markings, and rhythmic features."""
    log.info("Running rhythm analysis...")
    # Time signatures
    time_sigs = []
    seen = set()
    for ts in score.recurse().getElementsByClass("TimeSignature"):
        label = ts.ratioString
        if label not in seen:
            time_sigs.append({
                "signature": label,
                "measure": ts.measureNumber,
            })
            seen.add(label)

    # Tempo markings
    tempos = []
    for mm in score.recurse().getElementsByClass("MetronomeMark"):
        tempos.append({
            "bpm": mm.number if mm.number else None,
            "text": mm.text if mm.text else None,
            "measure": mm.measureNumber if hasattr(mm, "measureNumber") else None,
        })

    # If no explicit tempo, try to infer
    if not tempos:
        for mm in score.metronomeMarkBoundaries():
            if mm[2] and mm[2].number:
                tempos.append({"bpm": mm[2].number, "text": None, "measure": None})
                break

    # Estimate duration (M13). For tempo-varying pieces a single constant BPM
    # is wrong, so integrate seconds across each metronome-mark segment:
    # sum over boundaries of (segment quarter-length / bpm * 60). Fall back to
    # the first usable BPM × total length when boundaries are unavailable.
    duration_seconds = None
    if score.highestTime:
        duration_seconds = _estimate_duration_seconds(score, tempos)

    # Detect rhythmic features
    has_syncopation = False
    has_triplets = False
    has_dotted = False
    duration_types = set()

    for n in score.recurse().notes:
        d = n.duration
        duration_types.add(d.type)
        if d.dots > 0:
            has_dotted = True
        if hasattr(d, "tuplets") and d.tuplets:
            for t in d.tuplets:
                if t.numberNotesActual == 3:
                    has_triplets = True
        # Simple syncopation check: note starting on weak beat with long duration
        if hasattr(n, "beat") and n.beat and n.duration.quarterLength:
            beat = float(n.beat)
            if beat % 1 != 0 and n.duration.quarterLength >= 1.0:
                has_syncopation = True

    time_sig_changes = len(time_sigs) > 1

    # Compress tempo marks (accel/rit encoded as individual marks)
    if len(tempos) > 3:
        raw_count = len(tempos)
        tempos = _compress_tempo_marks(tempos)
        log.debug("Tempo compression: %d -> %d entries", raw_count, len(tempos))

    log.debug("Time sigs: %s | Tempos: %d entries | Syncopation: %s | Triplets: %s | Dotted: %s",
              time_sigs, len(tempos), has_syncopation, has_triplets, has_dotted)

    return {
        "time_signatures": time_sigs if time_sigs else [{"signature": "4/4", "measure": 1}],
        "time_signature_changes": time_sig_changes,
        "tempos": tempos,
        "estimated_duration_seconds": duration_seconds,
        "rhythmic_features": {
            "has_syncopation": has_syncopation,
            "has_triplets": has_triplets,
            "has_dotted_rhythms": has_dotted,
            "duration_types_used": sorted(duration_types),
        },
    }


def _first_usable_bpm(tempos: list[dict]) -> float | None:
    """First positive BPM among tempo entries (handles range-shaped dicts too)."""
    for entry in tempos:
        bpm = entry.get("bpm") or entry.get("from_bpm")
        if bpm and bpm > 0:
            return bpm
    return None


def _estimate_duration_seconds(score: Score, tempos: list[dict]) -> float | None:
    """Estimate playback seconds, integrating across tempo changes (M13).

    Uses ``metronomeMarkBoundaries()`` so multi-tempo pieces (accel/rit, section
    changes) are summed segment-by-segment instead of assuming one constant BPM.
    Falls back to ``highestTime / first_bpm`` if boundaries can't be computed.
    """
    total_ql = score.highestTime
    if not total_ql:
        return None

    try:
        boundaries = score.metronomeMarkBoundaries()
    except Exception as e:
        log.debug("metronomeMarkBoundaries failed (%s); using constant-tempo estimate", e)
        boundaries = None

    if boundaries:
        seconds = 0.0
        covered = False
        for start, end, mm in boundaries:
            bpm = mm.number if mm is not None and mm.number else None
            if not bpm or bpm <= 0:
                continue
            segment_ql = max(0.0, float(end) - float(start))
            seconds += segment_ql / bpm * 60
            covered = True
        if covered and seconds > 0:
            return round(seconds, 1)

    # Fallback: single-tempo estimate from the first usable BPM.
    bpm = _first_usable_bpm(tempos)
    if bpm:
        return round(total_ql / bpm * 60, 1)
    return None


def _compress_tempo_marks(tempos: list[dict]) -> list[dict]:
    """Collapse monotonic runs of tempo marks (accel/rit) into single range entries.

    Standalone marks:  {bpm, text, measure}
    Compressed ranges: {type: "accel"/"rit", from_bpm, to_bpm, from_measure, to_measure}
    """
    if len(tempos) <= 1:
        return tempos

    compressed = []
    i = 0

    while i < len(tempos):
        # Start a potential run
        run_start = i
        i += 1

        if tempos[run_start].get("bpm") is None:
            compressed.append(tempos[run_start])
            continue

        # Extend run while direction is consistent and measures are close
        while i < len(tempos) and tempos[i].get("bpm") is not None:
            prev_bpm = tempos[i - 1]["bpm"]
            curr_bpm = tempos[i]["bpm"]
            prev_m = tempos[i - 1].get("measure") or 0
            curr_m = tempos[i].get("measure") or 0

            # Same direction (both increasing or both decreasing) and close measures
            if curr_m - prev_m > 3:
                break  # gap too large, end run

            run_bpms = [tempos[j]["bpm"] for j in range(run_start, i + 1)]
            diffs = [run_bpms[k + 1] - run_bpms[k] for k in range(len(run_bpms) - 1)]

            # Check if all diffs have the same sign (monotonic)
            if all(d > 0 for d in diffs) or all(d < 0 for d in diffs):
                i += 1
            else:
                break

        run_len = i - run_start

        if run_len >= 3:
            # Compress this run
            from_bpm = tempos[run_start]["bpm"]
            to_bpm = tempos[i - 1]["bpm"]
            direction = "accel" if to_bpm > from_bpm else "rit"
            compressed.append({
                "type": direction,
                "from_bpm": from_bpm,
                "to_bpm": to_bpm,
                "from_measure": tempos[run_start].get("measure"),
                "to_measure": tempos[i - 1].get("measure"),
            })
        else:
            # Keep individual marks
            for j in range(run_start, i):
                compressed.append(tempos[j])

    return compressed
