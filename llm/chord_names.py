"""Tier-4 chord identification via a single Claude batch call."""

import json
import logging

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from llm.client import get_client

log = logging.getLogger(__name__)


def identify_chords(unidentified: list[dict], key_context: str) -> dict[str, str]:
    """Send unidentified chords to Claude in one batch call.

    Args:
        unidentified: list of {pitch_classes_label, measure, pitches}
        key_context: e.g. "e minor"

    Returns:
        dict mapping pitch_classes_label -> identified chord name. Returns {}
        on any failure so callers can fall back to lower tiers.
    """
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
        client = get_client()
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
