"""Shared analysis pipeline for the CLI and web front ends.

``run_analysis`` is the single implementation of the analyzer stages. The CLI
(``cli.py``) and Flask app (``app.py``) both call it, so the two front ends can
no longer drift apart on which stages run or in what order.
"""

import logging
import time
from pathlib import Path

from music21.stream import Score

from analyzer.parser import parse_score, extract_metadata
from analyzer.key_analysis import analyze_key
from analyzer.chord_analysis import analyze_chords
from analyzer.rhythm_analysis import analyze_rhythm
from analyzer.stats import analyze_stats
from analyzer.structure import analyze_structure
from analyzer.difficulty import analyze_difficulty
from analyzer.note_annotations import generate_annotations

log = logging.getLogger("sheet_music_analyzer")

UNKNOWN_TITLE = "Unknown"


def run_analysis(
    filepath: str,
    use_llm: bool = True,
    title_fallback: str | None = None,
) -> tuple[dict, Score]:
    """Run the full analysis pipeline. Returns (analysis_dict, score).

    When ``use_llm`` is False, the Tier-4 Claude chord-identification call is
    skipped (no network request) in addition to the explanation step.

    ``title_fallback`` is used as the score title when the file carries no
    title metadata. The web app passes the uploaded/original filename so an
    untitled upload still gets a meaningful name; the CLI passes the input
    filename so the two front ends agree.
    """
    t0 = time.perf_counter()
    log.info("=== Starting analysis of %s ===", filepath)

    score = parse_score(filepath)
    metadata = extract_metadata(score)
    if title_fallback and metadata["title"] == UNKNOWN_TITLE:
        metadata["title"] = Path(title_fallback).stem
    log.info("Title: %s | Composer: %s | Parts: %d",
             metadata["title"], metadata["composer"], metadata["number_of_parts"])

    key_info = analyze_key(score)
    rhythm_info = analyze_rhythm(score)
    stats_info = analyze_stats(score)
    chord_info = analyze_chords(score, key_info, use_llm=use_llm)
    structure_info = analyze_structure(score)
    difficulty_info = analyze_difficulty(score, rhythm_info, stats_info, key_info)
    annotations = generate_annotations(score)

    elapsed = time.perf_counter() - t0
    log.info("=== Analysis complete in %.2fs ===", elapsed)

    analysis = {
        "metadata": metadata,
        "key": key_info,
        "rhythm": rhythm_info,
        "stats": stats_info,
        "chords": chord_info,
        "structure": structure_info,
        "difficulty": difficulty_info,
        "annotations": annotations,
    }

    return analysis, score
