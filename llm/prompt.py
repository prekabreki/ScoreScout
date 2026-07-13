"""System prompt and JSON formatting for Claude API."""

import json

SYSTEM_PROMPT = """\
You are a friendly piano teacher explaining a piece of sheet music to a beginner.
The user has never studied music theory formally. Explain everything in plain, \
encouraging language. Avoid jargon — when you must use a musical term, define it \
immediately. Use analogies to everyday things when helpful.

You will receive a JSON summary of a music analysis. Based on this data, produce \
a report with EXACTLY these sections (use these headers):

## Overview
What this piece is, its overall vibe, and a plain-English difficulty summary. \
IMPORTANT: The difficulty rating in the data uses a 1-10 scale. Always reference \
difficulty as X/10 — never use a different scale like /5. For example if \
overall_difficulty is 4.0, say "4/10" not "4/5".

## Key Explanation
What key it's in, what that means in practice (which notes to watch), and why it matters.

## Chord Guide
The main chords used, what they sound like, and their role in the piece.

## Tricky Spots
Specific measures or sections that will be challenging, and why.

## Practice Plan
Suggested order to learn the piece, starting with the easiest parts.

## Musical Vocabulary
Define any terms or markings found in the score.

## What to Listen For
Musical features that make this piece interesting or beautiful.

Keep each section concise — aim for 3-6 sentences each. Be specific (reference \
measure numbers). Be encouraging but honest about difficulty.\
"""


def format_analysis_for_llm(analysis: dict) -> str:
    """Compress the analysis dict to a token-efficient JSON string."""
    # Select only the most relevant fields to keep token usage low
    compact = {
        "metadata": analysis.get("metadata", {}),
        "key": analysis.get("key", {}),
        "rhythm": {
            "time_signatures": analysis.get("rhythm", {}).get("time_signatures", []),
            "tempos": analysis.get("rhythm", {}).get("tempos", []),
            "features": analysis.get("rhythm", {}).get("rhythmic_features", {}),
        },
        "chords": {
            "unique": analysis.get("chords", {}).get("unique_chords", []),
            "most_common": analysis.get("chords", {}).get("most_common_chords", [])[:10],
            "roman_numerals": analysis.get("chords", {}).get("roman_numerals_sample", [])[:16],
            "complex": analysis.get("chords", {}).get("complex_chords", []),
            "identification_stats": analysis.get("chords", {}).get("identification_stats", {}),
        },
        "stats": {
            "total_notes": analysis.get("stats", {}).get("total_notes"),
            "measures": analysis.get("stats", {}).get("num_measures"),
            "per_hand": analysis.get("stats", {}).get("per_hand", []),
            "common_pitches": analysis.get("stats", {}).get("most_common_pitches", [])[:5],
        },
        "difficulty": analysis.get("difficulty", {}),
        "structure": {
            "key_changes": analysis.get("structure", {}).get("key_changes", []),
            "dynamics": analysis.get("structure", {}).get("dynamics", []),
            "sections": analysis.get("structure", {}).get("sections", []),
        },
    }

    return json.dumps(compact, indent=None, separators=(",", ":"))
