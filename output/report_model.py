"""Shared report view model.

`build_report_model` extracts, defaults and truncates everything the HTML and
Markdown renderers display, so the two reports cannot drift again. They used to
slice the same lists to different lengths (chords ``[:12]`` vs ``[:10]``, note
map ``[:60]`` vs ``[:48]``) and Markdown omitted the dynamics and per-hand
sections entirely. Renderers now consume this model and only escape/lay out.
"""

from dataclasses import dataclass

UNKNOWN = "Unknown"
MISSING = "?"
DEFAULT_TIME_SIGNATURE = "4/4"

# Unified truncation limits. These were the drifted values: HTML capped chords
# at 12 and the note map at 60; Markdown used 10 and 48. The larger, more
# informative limits win, and now live in exactly one place.
MAX_COMMON_CHORDS = 12
MAX_NOTE_MAP_LINES = 60
MAX_DYNAMICS = 20

DIFFICULTY_COMPONENTS = (
    ("rhythm_complexity", "Rhythm Complexity"),
    ("hand_span", "Hand Span"),
    ("tempo_density", "Tempo \u00d7 Density"),
    ("accidentals", "Accidentals"),
    ("jump_distance", "Jump Distance"),
    ("chord_density", "Chord Density"),
)


@dataclass(frozen=True)
class ReportModel:
    """Renderer-agnostic view of an analysis dict."""

    title: str
    composer: str
    has_composer: bool

    detected_key: str
    time_signature: str
    tempo: str
    has_tempo: bool
    duration: str
    has_duration: bool
    num_measures: object
    total_notes: object
    overall_difficulty: object

    difficulty_breakdown: tuple
    skills_required: tuple

    key_disagreement: bool
    explicit_key_signature: object

    common_chords: tuple
    key_changes: tuple
    dynamics: tuple
    per_hand: tuple

    note_map: tuple
    note_map_total: int
    note_map_omitted: int


def _format_duration(seconds) -> str:
    if not seconds:
        return "N/A"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}:{secs:02d}"


def _format_tempo(tempos: list) -> str:
    if not tempos:
        return "N/A"
    first = tempos[0]
    if first.get("bpm"):
        return f'{first["bpm"]} BPM'
    if first.get("from_bpm"):
        return f'{first["from_bpm"]} BPM'
    return "N/A"


def _format_time_signature(ts_list: list) -> str:
    if not ts_list:
        return DEFAULT_TIME_SIGNATURE
    return ", ".join(t.get("signature", MISSING) for t in ts_list)


def build_report_model(analysis: dict) -> ReportModel:
    """Build the shared view model from a raw analysis dict.

    Every default and truncation limit is applied here; renderers must not
    reach back into ``analysis`` or slice these lists themselves.
    """
    meta = analysis.get("metadata", {})
    diff = analysis.get("difficulty", {})
    stats = analysis.get("stats", {})
    rhythm = analysis.get("rhythm", {})
    key = analysis.get("key", {})
    chords = analysis.get("chords", {})
    struct = analysis.get("structure", {})
    annotations = analysis.get("annotations", {})

    composer = meta.get("composer", UNKNOWN)
    component = diff.get("component_scores", {})

    simplified = annotations.get("simplified", [])
    note_map = simplified[:MAX_NOTE_MAP_LINES]

    tempo = _format_tempo(rhythm.get("tempos", []))
    duration = _format_duration(rhythm.get("estimated_duration_seconds"))

    return ReportModel(
        title=meta.get("title", UNKNOWN),
        composer=composer,
        has_composer=composer != UNKNOWN,
        detected_key=key.get("detected_key", MISSING),
        time_signature=_format_time_signature(rhythm.get("time_signatures", [])),
        tempo=tempo,
        has_tempo=tempo != "N/A",
        duration=duration,
        has_duration=duration != "N/A",
        num_measures=stats.get("num_measures", MISSING),
        total_notes=stats.get("total_notes", MISSING),
        overall_difficulty=diff.get("overall_difficulty", MISSING),
        difficulty_breakdown=tuple(
            (label, component.get(k, 0)) for k, label in DIFFICULTY_COMPONENTS
        ),
        skills_required=tuple(diff.get("skills_required", [])),
        key_disagreement=bool(key.get("key_disagreement")),
        explicit_key_signature=key.get("explicit_key_signature"),
        common_chords=tuple(chords.get("most_common_chords", [])[:MAX_COMMON_CHORDS]),
        key_changes=tuple(struct.get("key_changes", [])),
        dynamics=tuple(struct.get("dynamics", [])[:MAX_DYNAMICS]),
        per_hand=tuple(stats.get("per_hand", [])),
        note_map=tuple(note_map),
        note_map_total=len(simplified),
        note_map_omitted=len(simplified) - len(note_map),
    )
