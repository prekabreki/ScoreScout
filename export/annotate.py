"""Inject annotations into a music21 Score for export.

Two annotation modes:
- Full: every note labeled, chords get simplified names, no theory jargon
- Guided: sparse labels on tricky spots only, chord symbols at changes, difficulty markers
"""

import copy
import logging

from music21.stream import Score
from music21 import expressions, harmony, tempo
from music21.exceptions21 import StreamException

from export.labeling import (
    _simplify_chord_name,
    events_from_music21,
    label_for,
    select_labels,
)
from export.profiles import AnnotationProfile

log = logging.getLogger(__name__)

# --- Granular-tempo cleanup tuning (see _strip_granular_tempos) -------------
# Below this many metronome marks a score isn't "granular" — leave it alone.
TEMPO_CLEANUP_MIN_MARKS = 5
# A monotonic run of at least this many marks is an accel/rit ramp: keep only
# its first and last mark, drop the interpolated middle.
TEMPO_RUN_MIN_LEN = 3
# Proximity filter: drop a mark within this many BPM of the last kept one...
TEMPO_PROXIMITY_BPM = 10
# ...unless it is at least this many measures away (preserves real changes).
TEMPO_PROXIMITY_MEASURES = 4


def annotate_score(
    score: Score,
    profile: AnnotationProfile,
    chord_info: dict | None = None,
    difficulty_info: dict | None = None,
    key_info: dict | None = None,
) -> Score:
    """Return a deep copy of the score with annotations injected."""
    log.info("Annotating score with profile '%s' (octaves=%s)...",
             profile.name, profile.include_octaves)
    annotated = copy.deepcopy(score)

    # Clean up granular tempo marks (accel/rit interpolation) before any annotation
    _strip_granular_tempos(annotated)

    if profile.note_letters == "all":
        _inject_full_labels(annotated, chord_info, profile.include_octaves)
    elif profile.note_letters == "guided":
        _inject_guided_labels(annotated, key_info, chord_info, profile.include_octaves)

    if profile.chord_symbols and chord_info:
        _inject_guided_chord_symbols(annotated, chord_info)

    if profile.difficulty_markers and difficulty_info:
        _inject_difficulty_markers(annotated, difficulty_info)

    log.info("Annotation complete")
    return annotated


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _strip_granular_tempos(score: Score) -> None:
    """Remove fractional-BPM metronome marks (interpolated accel/rit) and
    collapse monotonic runs of 3+ integer-BPM marks to just first and last."""
    marks = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    if len(marks) <= TEMPO_CLEANUP_MIN_MARKS:
        return

    removed = 0
    # Pass 1: remove fractional BPM marks (e.g. 127.5, 110.3)
    for m in marks:
        if m.number is not None and m.number != int(m.number):
            removed += _remove_mark(m)

    # Pass 2: collapse monotonic runs of integer marks
    remaining = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    if len(remaining) <= TEMPO_CLEANUP_MIN_MARKS:
        log.debug("Tempo cleanup: removed %d fractional marks, %d remain", removed, len(remaining))
        return

    i = 0
    while i < len(remaining):
        run = [remaining[i]]
        j = i + 1
        while j < len(remaining):
            prev_bpm = run[-1].number
            curr_bpm = remaining[j].number
            if prev_bpm is None or curr_bpm is None:
                break
            if len(run) >= 2:
                prev_dir = run[-1].number - run[-2].number
                curr_dir = curr_bpm - prev_bpm
                if (prev_dir > 0 and curr_dir > 0) or (prev_dir < 0 and curr_dir < 0):
                    run.append(remaining[j])
                    j += 1
                    continue
            else:
                run.append(remaining[j])
                j += 1
                continue
            break
        if len(run) >= TEMPO_RUN_MIN_LEN:
            for m in run[1:-1]:
                removed += _remove_mark(m)
        i = j

    # Pass 3: proximity filter — remove marks within ±TEMPO_PROXIMITY_BPM of the
    # last kept one, unless TEMPO_PROXIMITY_MEASURES+ measures apart (catches
    # rubato micro-fluctuations).
    remaining = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    if len(remaining) > TEMPO_RUN_MIN_LEN:
        last_kept = remaining[0]
        for mark in remaining[1:]:
            bpm_diff = abs(mark.number - last_kept.number)
            measure_diff = abs(mark.measureNumber - last_kept.measureNumber)
            if bpm_diff <= TEMPO_PROXIMITY_BPM and measure_diff < TEMPO_PROXIMITY_MEASURES:
                removed += _remove_mark(mark)
            else:
                last_kept = mark

    log.debug("Tempo cleanup: removed %d marks total", removed)


def _remove_mark(mark) -> int:
    """Remove a MetronomeMark from its active stream. Returns 1 on success, 0 otherwise.

    The only expected failures are a mark with no ``activeSite`` (AttributeError)
    or a stream that rejects the removal (StreamException); anything else
    propagates so genuine bugs aren't swallowed.
    """
    try:
        mark.activeSite.remove(mark)
        return 1
    except (AttributeError, StreamException):
        return 0


def _find_free_lyric_number(note_obj) -> int:
    used = {ly.number for ly in note_obj.lyrics if ly.number is not None}
    num = 1
    while num in used:
        num += 1
    return num


# ---------------------------------------------------------------------------
# Full mode: label every note
# ---------------------------------------------------------------------------

def _apply_note_labels(events, chord_info: dict | None, octaves: bool) -> int:
    """Label each selected event through the shared ``label_for`` core."""
    count = 0
    for ev in events:
        label = label_for(ev.pitches, chord_info, ev.measure, ev.beat, octaves)
        if not label:
            continue
        n = ev.target
        lyric_num = _find_free_lyric_number(n)
        n.addLyric(label, lyricNumber=lyric_num)
        count += 1
    return count


def _inject_full_labels(score: Score, chord_info: dict | None, octaves: bool) -> None:
    """Full profile: every note gets a letter. Chords 3+ get chord name."""
    parts = [events_from_music21(p) for p in (score.parts or [score])]
    count = _apply_note_labels(select_labels(parts, "all"), chord_info, octaves)
    log.debug("Full mode: injected labels on %d notes/chords", count)


# ---------------------------------------------------------------------------
# Guided mode: sparse labels on tricky spots only
# ---------------------------------------------------------------------------

def _inject_guided_labels(
    score: Score, key_info: dict | None, chord_info: dict | None, octaves: bool,
) -> None:
    """Guided profile: annotate ~15-20% of notes — only the genuinely tricky ones."""
    key_sharps = key_info.get("sharps_flats_count", 0) if key_info else 0
    parts = [events_from_music21(p) for p in (score.parts or [score])]
    selected = select_labels(parts, "guided", key_sharps=key_sharps)
    count = _apply_note_labels(selected, chord_info, octaves)

    total = sum(1 for part in parts for ev in part if not ev.is_rest and ev.pitches)
    pct = round(count / max(total, 1) * 100, 1)
    log.debug("Guided mode: labeled %d/%d notes (%.1f%%)", count, total, pct)


# ---------------------------------------------------------------------------
# Guided mode: chord symbols (sparse, simplified)
# ---------------------------------------------------------------------------

def _inject_guided_chord_symbols(score: Score, chord_info: dict) -> None:
    """Add simplified chord symbols above staff, max 1 per measure, only on changes."""
    progression = chord_info.get("chord_progression_full", [])
    if not progression:
        return

    top_part = score.parts[0] if score.parts else None
    if top_part is None:
        return

    prev_symbol = None
    last_measure = None
    count = 0

    for entry in progression:
        name = entry.get("name", "")
        if not name or name.startswith("["):
            continue

        simple = _simplify_chord_name(name)
        measure_num = entry.get("measure")

        # Skip if same chord as previous or already placed one this measure
        if simple == prev_symbol:
            continue
        if measure_num == last_measure:
            continue

        try:
            measure = top_part.measure(measure_num)
            if measure is None:
                continue
            beat = entry.get("beat", 1.0)
            offset = beat - 1.0

            try:
                cs = harmony.ChordSymbol(simple)
                measure.insert(offset, cs)
                count += 1
                prev_symbol = simple
                last_measure = measure_num
            except Exception:
                log.debug("Could not create ChordSymbol for '%s'", simple)
        except Exception:
            continue

    log.debug("Guided mode: injected %d chord symbols", count)


# ---------------------------------------------------------------------------
# Guided mode: difficulty markers (text labels, not colors)
# ---------------------------------------------------------------------------

def _inject_difficulty_markers(score: Score, difficulty_info: dict) -> None:
    """Add text markers at hard sections instead of per-note coloring."""
    component = difficulty_info.get("component_scores", {})
    overall = difficulty_info.get("overall_difficulty", 0)

    # We don't have per-section difficulty yet, so use a simpler approach:
    # scan for locally difficult passages and mark them
    top_part = score.parts[0] if score.parts else None
    if not top_part:
        return

    measures = list(top_part.getElementsByClass("Measure"))
    count = 0

    # Sliding window: score each group of 4 measures
    window = 4
    marked_measures = set()

    for i in range(0, len(measures), window):
        chunk = measures[i:i + window]
        local_score = 0

        for m in chunk:
            for n in m.recurse().notes:
                if n.isChord and len(n.pitches) >= 2:
                    pitches = sorted(n.pitches, key=lambda p: p.midi)
                    span = pitches[-1].midi - pitches[0].midi
                    if span > 12:
                        local_score += 2

                if n.isNote and n.pitch.accidental and n.pitch.accidental.alter != 0:
                    local_score += 0.5

            # Note density
            notes_in_measure = len(list(m.recurse().notes))
            if notes_in_measure > 12:
                local_score += 2

        # Normalize by window size
        avg = local_score / max(len(chunk), 1)

        if avg >= 4:
            label = "!! Hard"
            marker_measure = chunk[0]
        elif avg >= 2:
            label = "> Moderate"
            marker_measure = chunk[0]
        else:
            continue

        m_num = marker_measure.number
        if m_num in marked_measures:
            continue

        te = expressions.TextExpression(label)
        te.style.fontSize = 9
        te.style.fontWeight = "bold"
        marker_measure.insert(0, te)
        marked_measures.add(m_num)
        count += 1

    log.debug("Guided mode: placed %d difficulty markers", count)
