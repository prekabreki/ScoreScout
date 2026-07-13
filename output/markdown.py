"""Markdown report renderer."""


def render_markdown(analysis: dict, explanation: str | None = None) -> str:
    """Render a full markdown report from analysis data and optional LLM explanation."""
    lines = []
    meta = analysis.get("metadata", {})

    # Title
    title = meta.get("title", "Unknown")
    composer = meta.get("composer", "Unknown")
    lines.append(f"# {title}")
    if composer != "Unknown":
        lines.append(f"**Composer:** {composer}")
    lines.append("")

    # Quick stats bar
    diff = analysis.get("difficulty", {})
    overall = diff.get("overall_difficulty", "?")
    stats = analysis.get("stats", {})
    rhythm = analysis.get("rhythm", {})
    key = analysis.get("key", {})

    lines.append("## At a Glance")
    lines.append(f"- **Key:** {key.get('detected_key', '?')}")
    ts_list = rhythm.get("time_signatures", [])
    ts_str = ", ".join(t.get("signature", "?") for t in ts_list) if ts_list else "4/4"
    lines.append(f"- **Time Signature:** {ts_str}")
    tempos = rhythm.get("tempos", [])
    if tempos:
        first = tempos[0]
        if first.get("bpm"):
            lines.append(f"- **Tempo:** {first.get('bpm')} BPM")
        elif first.get("from_bpm"):
            lines.append(f"- **Tempo:** {first.get('from_bpm')} BPM")
    lines.append(f"- **Measures:** {stats.get('num_measures', '?')}")
    lines.append(f"- **Total Notes:** {stats.get('total_notes', '?')}")
    dur = rhythm.get("estimated_duration_seconds")
    if dur:
        mins = int(dur // 60)
        secs = int(dur % 60)
        lines.append(f"- **Estimated Duration:** {mins}:{secs:02d}")
    lines.append(f"- **Difficulty:** {overall}/10")
    lines.append("")

    # Difficulty breakdown
    lines.append("## Difficulty Breakdown")
    component = diff.get("component_scores", {})
    labels = {
        "rhythm_complexity": "Rhythm Complexity",
        "hand_span": "Hand Span",
        "tempo_density": "Tempo x Density",
        "accidentals": "Accidentals",
        "jump_distance": "Jump Distance",
        "chord_density": "Chord Density",
    }
    for k, label in labels.items():
        val = component.get(k, 0)
        bar = _bar(val, 10)
        lines.append(f"- {label}: {bar} {val}/10")
    lines.append("")

    # Skills required
    skills = diff.get("skills_required", [])
    if skills:
        lines.append("## Skills Required")
        for s in skills:
            lines.append(f"- {s}")
        lines.append("")

    # Key info
    if key.get("key_disagreement"):
        lines.append("## Key Note")
        lines.append(
            f"The written key signature suggests **{key.get('explicit_key_signature')}**, "
            f"but the music sounds more like **{key.get('detected_key')}**. "
            "This is common in anime/game music with modal or borrowed-chord writing."
        )
        lines.append("")

    # Chords
    chords = analysis.get("chords", {})
    common_chords = chords.get("most_common_chords", [])
    if common_chords:
        lines.append("## Most Common Chords")
        for ch in common_chords[:10]:
            lines.append(f"- **{ch.get('name', '?')}** (x{ch.get('count', 0)})")
        lines.append("")

    # Structure
    struct = analysis.get("structure", {})
    kc = struct.get("key_changes", [])
    if kc:
        lines.append("## Key Changes")
        for change in kc:
            lines.append(f"- Measure {change.get('at_measure', '?')}: {change.get('from_key', '?')} -> {change.get('to_key', '?')}")
        lines.append("")

    # Note annotations (simplified)
    annotations = analysis.get("annotations", {})
    simplified = annotations.get("simplified", [])
    if simplified:
        lines.append("## Note Map (Simplified)")
        lines.append("```")
        for line in simplified[:48]:
            lines.append(line)
        if len(simplified) > 48:
            lines.append(f"... ({len(simplified) - 48} more measures)")
        lines.append("```")
        lines.append("")

    # LLM explanation
    if explanation:
        lines.append("---")
        lines.append("")
        lines.append(explanation)
        lines.append("")

    return "\n".join(lines)


def _bar(value: int | float, max_val: int, width: int = 10) -> str:
    filled = round(value / max_val * width)
    return "\u2588" * filled + "\u2591" * (width - filled)
