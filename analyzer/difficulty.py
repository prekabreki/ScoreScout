"""Difficulty scoring — heuristic-based difficulty assessment."""

import logging

from music21.stream import Score

log = logging.getLogger(__name__)

# --- Difficulty scoring tunables -------------------------------------------
# All component scores are clamped to this 0-10 range before weighting.
SCORE_MIN = 0
SCORE_MAX = 10

# 1. Rhythm complexity: baseline plus per-feature bumps.
RHYTHM_BASELINE = 2
RHYTHM_SYNCOPATION_BUMP = 2
RHYTHM_TRIPLET_BUMP = 2
RHYTHM_DOTTED_BUMP = 1
RHYTHM_METER_CHANGE_BUMP = 2
RHYTHM_FAST_NOTE_BUMP = 1  # presence of 32nd/64th notes

# 2. Hand span: an octave is 12 semitones; up to a 5th (7) is comfortable, so
#    only the reach beyond a 5th counts toward difficulty.
COMFORTABLE_SPAN_SEMITONES = 7

# 3. Tempo x density: normalized against a reference tempo and note density,
#    then scaled into the 0-10 band.
REFERENCE_BPM = 120
DEFAULT_BPM = 120
REFERENCE_DENSITY = 8  # notes per measure treated as "busy"
DEFAULT_DENSITY = 4
TEMPO_DENSITY_SCALE = 5

# 4. Accidentals: key-signature accidentals plus a ratio of out-of-key notes.
KEY_ACCIDENTAL_WEIGHT = 0.8
ACCIDENTAL_RATIO_SCALE = 20

# 5. Jump distance: position shifts wider than an octave, scaled by frequency.
OCTAVE_SEMITONES = 12
JUMP_RATIO_SCALE = 40

# 6. Chord density: extra notes beyond a single melodic voice.
SINGLE_VOICE_NOTES = 1

# Overall: per-component weights for the final weighted average (clamped 1-10).
COMPONENT_WEIGHTS = {
    "rhythm_complexity": 2.0,
    "hand_span": 1.5,
    "tempo_density": 2.0,
    "accidentals": 1.0,
    "jump_distance": 1.5,
    "chord_density": 1.0,
}
OVERALL_MIN = 1
OVERALL_MAX = 10

# Skill-flag thresholds (component-score cutoffs that surface a "skill required").
SKILL_HAND_SPAN_THRESHOLD = 4
SKILL_JUMP_THRESHOLD = 3
SKILL_CHORD_SIZE_THRESHOLD = 4


def analyze_difficulty(score: Score, rhythm_info: dict, stats_info: dict, key_info: dict) -> dict:
    """Score difficulty on a 1-10 scale using multiple heuristics."""
    log.info("Scoring difficulty...")
    scores = {}

    # 1. Rhythm complexity (0-10)
    rf = rhythm_info.get("rhythmic_features", {})
    rhythm_score = RHYTHM_BASELINE
    if rf.get("has_syncopation"):
        rhythm_score += RHYTHM_SYNCOPATION_BUMP
    if rf.get("has_triplets"):
        rhythm_score += RHYTHM_TRIPLET_BUMP
    if rf.get("has_dotted_rhythms"):
        rhythm_score += RHYTHM_DOTTED_BUMP
    if rhythm_info.get("time_signature_changes"):
        rhythm_score += RHYTHM_METER_CHANGE_BUMP
    dur_types = rf.get("duration_types_used", [])
    if "32nd" in dur_types or "64th" in dur_types:
        rhythm_score += RHYTHM_FAST_NOTE_BUMP
    scores["rhythm_complexity"] = min(rhythm_score, SCORE_MAX)

    # 2. Hand span — max simultaneous interval per hand
    max_span_semitones = 0
    for part in score.parts:
        for ch in part.recurse().getElementsByClass("Chord"):
            pitches = sorted(ch.pitches, key=lambda p: p.midi)
            if len(pitches) >= 2:
                span = pitches[-1].midi - pitches[0].midi
                max_span_semitones = max(max_span_semitones, span)

    # NOTE: span is read per Chord as-written (no cross-staff reach modelling, and
    # for a single notated voice it mixes melody/bass). Refining this to compute
    # span/jumps per voice is tracked separately; here we only ensure the score is
    # well-formed and non-negative.
    span_score = min(max(max_span_semitones - COMFORTABLE_SPAN_SEMITONES, SCORE_MIN), SCORE_MAX)
    scores["hand_span"] = span_score

    # 3. Tempo x density
    bpm = DEFAULT_BPM
    tempos = rhythm_info.get("tempos", [])
    # rhythm_analysis._compress_tempo_marks can turn entries into range dicts
    # (accel/rit) that carry "from_bpm"/"to_bpm" instead of "bpm". Scan for the
    # first entry that exposes a usable tempo under either shape so we don't
    # silently fall back to DEFAULT_BPM when tempo[0] happens to be a range.
    for entry in tempos:
        candidate = entry.get("bpm") or entry.get("from_bpm")
        if candidate:
            bpm = candidate
            break
    density = stats_info.get("notes_per_measure", DEFAULT_DENSITY)
    tempo_density = (bpm / REFERENCE_BPM) * (density / REFERENCE_DENSITY)  # normalized
    scores["tempo_density"] = max(SCORE_MIN, min(round(tempo_density * TEMPO_DENSITY_SCALE), SCORE_MAX))

    # 4. Accidentals frequency
    sharps_flats = abs(key_info.get("sharps_flats_count", 0))
    accidental_count = 0
    for n in score.recurse().notes:
        if not n.isChord:
            if n.pitch.accidental and n.pitch.accidental.alter != 0:
                accidental_count += 1
        else:
            for p in n.pitches:
                if p.accidental and p.accidental.alter != 0:
                    accidental_count += 1

    total = stats_info.get("total_notes", 1)
    accidental_ratio = accidental_count / max(total, 1)
    acc_score = max(SCORE_MIN, min(
        round(sharps_flats * KEY_ACCIDENTAL_WEIGHT + accidental_ratio * ACCIDENTAL_RATIO_SCALE),
        SCORE_MAX,
    ))
    scores["accidentals"] = acc_score

    # 5. Jump distance — large position shifts
    jump_count = 0
    for part in score.parts:
        prev_midi = None
        for n in part.recurse().notes:
            midi = n.pitch.midi if not n.isChord else n.pitches[0].midi
            if prev_midi is not None:
                if abs(midi - prev_midi) > OCTAVE_SEMITONES:  # more than an octave
                    jump_count += 1
            prev_midi = midi

    jump_ratio = jump_count / max(total, 1)
    scores["jump_distance"] = max(SCORE_MIN, min(round(jump_ratio * JUMP_RATIO_SCALE), SCORE_MAX))

    # 6. Chord density (simultaneous notes)
    max_chord_size = 0
    chord_sizes = []
    for ch in score.recurse().getElementsByClass("Chord"):
        size = len(ch.pitches)
        chord_sizes.append(size)
        max_chord_size = max(max_chord_size, size)

    # A purely-melodic score has no chords, so avg_chord_size==0 -> round(-1)==-1.
    # The "extra notes beyond a single voice" measure must floor at 0, not -1,
    # otherwise this component drags the weighted overall below the others.
    avg_chord_size = sum(chord_sizes) / max(len(chord_sizes), 1)
    scores["chord_density"] = max(SCORE_MIN, min(round(avg_chord_size - SINGLE_VOICE_NOTES), SCORE_MAX))

    # Overall weighted average
    weights = COMPONENT_WEIGHTS
    weighted_sum = sum(scores[k] * weights[k] for k in scores)
    total_weight = sum(weights.values())
    overall = round(weighted_sum / total_weight, 1)
    overall = max(OVERALL_MIN, min(OVERALL_MAX, overall))

    log.debug("Component scores: %s", scores)
    log.debug("Overall difficulty: %.1f/10", overall)

    # Skills required
    skills = []
    parts = score.parts
    if len(parts) >= 2:
        skills.append("Reading treble and bass clef simultaneously")
    if scores["hand_span"] >= SKILL_HAND_SPAN_THRESHOLD:
        skills.append(f"Hand span up to {max_span_semitones} semitones ({_semitones_to_interval(max_span_semitones)})")
    if rf.get("has_syncopation"):
        skills.append("Syncopated rhythms")
    if rf.get("has_triplets"):
        skills.append("Triplet figures")
    if scores["jump_distance"] >= SKILL_JUMP_THRESHOLD:
        skills.append("Large position jumps (octave+)")
    if max_chord_size >= SKILL_CHORD_SIZE_THRESHOLD:
        skills.append(f"Chords with up to {max_chord_size} simultaneous notes")
    if rhythm_info.get("time_signature_changes"):
        skills.append("Time signature changes")
    if key_info.get("key_disagreement"):
        skills.append("Modal or ambiguous key writing")

    return {
        "overall_difficulty": overall,
        "component_scores": scores,
        "max_hand_span_semitones": max_span_semitones,
        "skills_required": skills,
    }


def _semitones_to_interval(semitones: int) -> str:
    names = {
        0: "unison", 1: "minor 2nd", 2: "major 2nd", 3: "minor 3rd",
        4: "major 3rd", 5: "perfect 4th", 6: "tritone", 7: "perfect 5th",
        8: "minor 6th", 9: "major 6th", 10: "minor 7th", 11: "major 7th",
        12: "octave", 13: "minor 9th", 14: "major 9th", 15: "minor 10th",
        16: "major 10th",
    }
    return names.get(semitones, f"{semitones} semitones")
