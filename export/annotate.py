"""Inject annotations into a music21 Score for export.

Two annotation modes:
- Full: every note labeled, chords get simplified names, no theory jargon
- Guided: sparse labels on tricky spots only, chord symbols at changes, difficulty markers
"""

import copy
import logging
import re

from music21.stream import Score
from music21 import expressions, harmony, key, tempo
from music21.exceptions21 import StreamException

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
        _inject_guided_labels(annotated, key_info, profile.include_octaves)

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


def _fix_flats(text: str) -> str:
    """Convert music21's flat notation to beginner-friendly: B- → Bb, A-- → Abb."""
    text = text.replace('--', 'bb')
    return re.sub(r'([A-G])-(?=[^a-zA-Z]|$)', r'\1b', text)


def _find_free_lyric_number(note_obj) -> int:
    used = {ly.number for ly in note_obj.lyrics if ly.number is not None}
    num = 1
    while num in used:
        num += 1
    return num


def _note_name(pitch_obj, octaves: bool) -> str:
    return _fix_flats(pitch_obj.nameWithOctave if octaves else pitch_obj.name)


def _simplify_chord_name(symbol: str) -> str:
    """Strip inversions for beginner readability: 'Bm7/D' -> 'Bm7'."""
    if not symbol:
        return symbol
    return _fix_flats(symbol.split("/")[0])


# Words that signal music21 internal classification, not a usable chord name
_JARGON_WORDS = frozenset([
    'incomplete', 'quartal', 'tetramirror', 'augmented-major',
    'diminished-major', 'whole-tone', 'chromatic', 'pentatonic',
    'secundal', 'tertian', 'altered', 'mirror', 'forte',
    'chord', 'triad',
])

# music21 "CaddD" / "EaddF#" style — a note letter immediately after "add".
_ADD_NOTE_RE = re.compile(r'add[A-G]', re.IGNORECASE)


def _is_usable_chord_name(name: str) -> bool:
    """Check if a chord name is short and beginner-friendly."""
    if not name or name.startswith("["):
        return False
    # Too long — no beginner chord name exceeds ~8 chars (e.g. "Cmaj7b5")
    if len(name) > 8:
        return False
    # Contains academic jargon
    low = name.lower()
    for word in _JARGON_WORDS:
        if word in low:
            return False
    if _ADD_NOTE_RE.search(name):
        return False
    return True


def _lookup_chord_name_at(note_obj, chord_info: dict | None) -> str | None:
    """Try to find a chord name for this note's measure+beat from chord_info."""
    if not chord_info:
        return None
    progression = chord_info.get("chord_progression_full", [])
    if not progression:
        return None

    m = note_obj.measureNumber
    b = float(note_obj.beat) if note_obj.beat else 1.0

    # Find closest chord event at this measure+beat
    for entry in progression:
        if entry.get("measure") == m and abs(entry.get("beat", 1.0) - b) < 0.25:
            name = entry.get("name", "")
            simplified = _simplify_chord_name(name) if name else ""
            if simplified and _is_usable_chord_name(simplified):
                return _fix_flats(simplified)
    return None


# ---------------------------------------------------------------------------
# Full mode: label every note
# ---------------------------------------------------------------------------

def _inject_full_labels(score: Score, chord_info: dict | None, octaves: bool) -> None:
    """Full profile: every note gets a letter. Chords 3+ get chord name."""
    count = 0
    for n in score.recurse().notes:
        if n.isNote:
            label = _note_name(n.pitch, octaves)
        elif n.isChord:
            num = len(n.pitches)
            if num == 2:
                # Dyad: show both notes, collapse if same pitch class (G/G → G)
                sorted_p = sorted(n.pitches, key=lambda p: p.midi)
                names = [_note_name(p, octaves) for p in sorted_p]
                if sorted_p[0].name == sorted_p[1].name:
                    label = names[0]
                else:
                    label = "/".join(names)
            elif num >= 3:
                # Try to get a chord name
                chord_name = _lookup_chord_name_at(n, chord_info)
                if chord_name and "power" in chord_name.lower():
                    # Power chords are guitar jargon — show actual note names
                    names = [_note_name(p, octaves) for p in sorted(n.pitches, key=lambda p: p.midi)]
                    label = "/".join(names)
                elif chord_name:
                    label = chord_name
                else:
                    # Fallback: show up to 3 pitch names
                    names = [_note_name(p, octaves) for p in sorted(n.pitches, key=lambda p: p.midi)]
                    if len(names) > 3:
                        label = "/".join(names[:3]) + "+"
                    else:
                        label = "/".join(names)
            else:
                continue
        else:
            continue

        lyric_num = _find_free_lyric_number(n)
        n.addLyric(label, lyricNumber=lyric_num)
        count += 1

    log.debug("Full mode: injected labels on %d notes/chords", count)


# ---------------------------------------------------------------------------
# Guided mode: sparse labels on tricky spots only
# ---------------------------------------------------------------------------

def _inject_guided_labels(score: Score, key_info: dict | None, octaves: bool) -> None:
    """Guided profile: annotate ~15-20% of notes — only the genuinely tricky ones."""
    key_sharps = key_info.get("sharps_flats_count", 0) if key_info else 0

    # Collect candidates with priority, then apply density cap
    candidates: list[tuple] = []  # (priority, note_obj, label)
    total = 0

    for part in score.parts:
        prev_midi = None
        rest_beats = 0.0
        seen_accidentals: set[str] = set()  # pitch names already labeled (per part)
        note_index = 0

        for n in part.recurse().notesAndRests:
            if n.isRest:
                rest_beats += n.duration.quarterLength
                continue

            total += 1
            priority = None  # None = don't label; lower number = higher priority

            # Rule 1: First note of the piece (per part)
            if note_index == 0:
                priority = 1

            # Rule 2: Accidental not in key — but only FIRST occurrence per part
            if priority is None:
                acc_pitches = []
                if n.isNote and _is_accidental_outside_key(n.pitch, key_sharps):
                    acc_pitches = [n.pitch]
                elif n.isChord:
                    acc_pitches = [p for p in n.pitches if _is_accidental_outside_key(p, key_sharps)]
                if acc_pitches:
                    unseen = [p for p in acc_pitches if p.name not in seen_accidentals]
                    if unseen:
                        priority = 3
                    for p in acc_pitches:
                        seen_accidentals.add(p.name)

            # Rule 3: Re-entry after a rest >= 2 beats
            if priority is None and rest_beats >= 2.0:
                priority = 2

            # Rule 4: Large interval jump (> 12th = 19 semitones)
            if priority is None and prev_midi is not None:
                curr_midi = n.pitch.midi if n.isNote else n.pitches[0].midi
                if abs(curr_midi - prev_midi) > 19:
                    priority = 4

            if priority is not None:
                if n.isNote:
                    label = _note_name(n.pitch, octaves)
                elif n.isChord:
                    label = "/".join(_note_name(p, octaves) for p in sorted(n.pitches, key=lambda p: p.midi))
                else:
                    label = ""
                if label:
                    candidates.append((priority, n, label))

            # Update state
            if n.isNote:
                prev_midi = n.pitch.midi
            elif n.isChord:
                prev_midi = n.pitches[0].midi
            rest_beats = 0.0
            note_index += 1

    # Density cap: keep at most 20% of notes, preferring higher priority
    max_labels = int(total * 0.20)
    candidates.sort(key=lambda c: c[0])
    kept = candidates[:max_labels]

    for _, n, label in kept:
        lyric_num = _find_free_lyric_number(n)
        n.addLyric(label, lyricNumber=lyric_num)

    pct = round(len(kept) / max(total, 1) * 100, 1)
    log.debug("Guided mode: labeled %d/%d notes (%.1f%%) [cap=%d]", len(kept), total, pct, max_labels)


def _is_accidental_outside_key(pitch_obj, key_sharps: int) -> bool:
    """True if a pitch carries an accidental the key signature does NOT account for.

    ``key_sharps`` is the signed count of sharps (>0) or flats (<0) in the key
    signature. A pitch is "outside the key" when its sounding accidental differs
    from whatever the key signature already applies to that letter name — e.g.
    in G major (1 sharp) an F# is diatonic (returns False) but an F-natural or
    G# is chromatic (returns True). Naturals on letters the signature does not
    alter (e.g. F-natural in C major) carry no real accidental and return False.
    """
    acc = pitch_obj.accidental
    if acc is None or acc.alter == 0:
        return False

    # What alteration does the key signature impose on this pitch's letter?
    ks = key.KeySignature(key_sharps)
    expected = ks.accidentalByStep(pitch_obj.step)
    expected_alter = expected.alter if expected is not None else 0.0

    # Diatonic iff the pitch's accidental matches the signature's for that step.
    return acc.alter != expected_alter


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
