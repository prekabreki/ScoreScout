"""Markdown report renderer."""

from output.report_model import build_report_model


def render_markdown(analysis: dict, explanation: str | None = None) -> str:
    """Render a full markdown report from analysis data and optional LLM explanation."""
    model = build_report_model(analysis)
    lines = []

    # Title
    lines.append(f"# {model.title}")
    if model.has_composer:
        lines.append(f"**Composer:** {model.composer}")
    lines.append("")

    # Quick stats bar
    lines.append("## At a Glance")
    lines.append(f"- **Key:** {model.detected_key}")
    lines.append(f"- **Time Signature:** {model.time_signature}")
    if model.has_tempo:
        lines.append(f"- **Tempo:** {model.tempo}")
    lines.append(f"- **Measures:** {model.num_measures}")
    lines.append(f"- **Total Notes:** {model.total_notes}")
    if model.has_duration:
        lines.append(f"- **Estimated Duration:** {model.duration}")
    lines.append(f"- **Difficulty:** {model.overall_difficulty}/10")
    lines.append("")

    # Difficulty breakdown
    lines.append("## Difficulty Breakdown")
    for label, val in model.difficulty_breakdown:
        bar = _bar(val, 10)
        lines.append(f"- {label}: {bar} {val}/10")
    lines.append("")

    # Skills required
    if model.skills_required:
        lines.append("## Skills Required")
        for s in model.skills_required:
            lines.append(f"- {s}")
        lines.append("")

    # Key info
    if model.key_disagreement:
        lines.append("## Key Note")
        lines.append(
            f"The written key signature suggests **{model.explicit_key_signature}**, "
            f"but the music sounds more like **{model.detected_key}**. "
            "This is common in anime/game music with modal or borrowed-chord writing."
        )
        lines.append("")

    # Hands
    if model.per_hand:
        lines.append("## Hands")
        lines.append("| Part | Notes | Lowest | Highest |")
        lines.append("| --- | --- | --- | --- |")
        for h in model.per_hand:
            lines.append(
                f"| {h.get('part_name', '?')} | {h.get('note_count', 0)} "
                f"| {h.get('lowest_note', '?')} | {h.get('highest_note', '?')} |"
            )
        lines.append("")

    # Chords
    if model.common_chords:
        lines.append("## Most Common Chords")
        for ch in model.common_chords:
            lines.append(f"- **{ch.get('name', '?')}** (x{ch.get('count', 0)})")
        lines.append("")

    # Structure
    if model.key_changes:
        lines.append("## Key Changes")
        for change in model.key_changes:
            lines.append(
                f"- Measure {change.get('at_measure', '?')}: "
                f"{change.get('from_key', '?')} -> {change.get('to_key', '?')}"
            )
        lines.append("")

    # Dynamics
    if model.dynamics:
        lines.append("## Dynamic Markings")
        for d in model.dynamics:
            lines.append(f"- m.{d.get('measure', '?')}: {d.get('marking', '?')}")
        lines.append("")

    # Note annotations (simplified)
    if model.note_map:
        lines.append("## Note Map (Simplified)")
        lines.append("```")
        for line in model.note_map:
            lines.append(line)
        if model.note_map_omitted:
            lines.append(f"... ({model.note_map_omitted} more measures)")
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
