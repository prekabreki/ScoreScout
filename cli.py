#!/usr/bin/env python3
"""Sheet Music Analyzer — CLI entry point."""

import argparse
import json
import logging
import sys
import os
import time
import traceback
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from music21.stream import Score

from analyzer.parser import parse_score, extract_metadata
from analyzer.key_analysis import analyze_key
from analyzer.chord_analysis import analyze_chords
from analyzer.rhythm_analysis import analyze_rhythm
from analyzer.stats import analyze_stats
from analyzer.structure import analyze_structure
from analyzer.difficulty import analyze_difficulty
from analyzer.note_annotations import generate_annotations
from llm.explain import generate_explanation
from output.markdown import render_markdown
from output.html import render_html

log = logging.getLogger("sheet_music_analyzer")


def setup_logging(debug: bool = False, log_file: str | None = None):
    """Configure logging — always writes a fat debug log to file, optionally verbose on console."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-35s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — always DEBUG level, always on
    if log_file is None:
        log_file = "analyzer_debug.log"
    fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler — INFO normally, DEBUG with --debug
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)-8s  %(message)s"))
    root.addHandler(ch)

    # Quiet down music21's own logging unless we're in debug mode
    if not debug:
        logging.getLogger("music21").setLevel(logging.WARNING)


def run_analysis(filepath: str, use_llm: bool = True) -> tuple[dict, Score]:
    """Run the full analysis pipeline. Returns (analysis_dict, score).

    When use_llm is False, the Tier-4 Claude chord-identification call is skipped
    (no network request) in addition to the explanation step.
    """
    t0 = time.perf_counter()
    log.info("=== Starting analysis of %s ===", filepath)

    score = parse_score(filepath)
    metadata = extract_metadata(score)
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


def main():
    parser = argparse.ArgumentParser(
        description="Analyze piano sheet music and generate a beginner-friendly report."
    )
    parser.add_argument("file", help="Path to sheet music file (.musicxml, .mxl, .xml, .mscz, .mscx)")
    parser.add_argument("--output", "-o", help="Output file path (default: stdout)")
    parser.add_argument(
        "--format", "-f",
        choices=["markdown", "html", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable all Claude API calls (skips the explanation and the Tier-4 chord-identification call)",
    )
    parser.add_argument("--debug", action="store_true", help="Show debug messages on console")
    parser.add_argument(
        "--log-file",
        default="analyzer_debug.log",
        help="Debug log file path (default: analyzer_debug.log)",
    )
    parser.add_argument(
        "--export", "-e",
        help="Export annotated sheet music (.musicxml, .pdf, .png)",
    )
    parser.add_argument(
        "--annotations",
        choices=["full", "guided", "clean"],
        default="full",
        help="Annotation profile for export (default: full)",
    )
    parser.add_argument(
        "--octaves",
        action="store_true",
        help="Include octave numbers in note labels (e.g., C4 instead of C)",
    )
    parser.add_argument(
        "--chord-guide",
        action="store_true",
        help="Append a chord reference page to the exported score",
    )
    parser.add_argument(
        "--text-guide",
        action="store_true",
        help="Append Claude's beginner-friendly text guide to the exported PDF",
    )

    args = parser.parse_args()

    setup_logging(debug=args.debug, log_file=args.log_file)

    log.debug("CLI args: %s", vars(args))
    log.debug("Python %s on %s", sys.version, sys.platform)

    try:
        analysis, score = run_analysis(args.file, use_llm=not args.no_llm)
    except (FileNotFoundError, ValueError) as e:
        log.error("Fatal: %s", e)
        log.debug(traceback.format_exc())
        sys.exit(1)
    except Exception as e:
        log.error("Unexpected error during analysis: %s", e)
        log.debug(traceback.format_exc())
        sys.exit(1)

    # Export annotated sheet music if requested
    if args.export:
        from export.profiles import get_profile
        from export.annotate import annotate_score
        from export.render import export_musicxml, export_pdf, export_png, export_pdf_from_mscz
        from config import EXPORT_DIR

        profile = get_profile(args.annotations, include_octaves=args.octaves)

        # Append profile name to filename (e.g., "song.musicxml" -> "song_guided.musicxml")
        export_path = Path(args.export)
        stem = export_path.stem
        if not stem.endswith(f"_{args.annotations}"):
            export_path = export_path.with_stem(f"{stem}_{args.annotations}")

        # If just a filename (no directory), save to the hardcoded export dir
        if not export_path.parent.exists() or str(export_path.parent) == ".":
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            export_path = EXPORT_DIR / export_path.name

        ext = export_path.suffix.lower()

        is_mscz = Path(args.file).suffix.lower() == ".mscz"

        # Bind up front: the .mscz injection path never builds an annotated
        # music21 score, so the RuntimeError fallback must derive one itself.
        annotated = None

        try:
            if ext == ".pdf":
                if is_mscz and args.annotations != "clean":
                    log.info("Using .mscz injection path for %s", Path(args.file).name)
                    export_pdf_from_mscz(
                        args.file, str(export_path),
                        profile_name=args.annotations,
                        octaves=args.octaves,
                        chord_info=analysis.get("chords"),
                    )
                else:
                    annotated = annotate_score(
                        score, profile,
                        chord_info=analysis.get("chords"),
                        difficulty_info=analysis.get("difficulty"),
                        key_info=analysis.get("key"),
                    )
                    export_pdf(annotated, str(export_path))
            elif ext == ".png":
                annotated = annotate_score(
                    score, profile,
                    chord_info=analysis.get("chords"),
                    difficulty_info=analysis.get("difficulty"),
                    key_info=analysis.get("key"),
                )
                export_png(annotated, str(export_path))
            else:
                annotated = annotate_score(
                    score, profile,
                    chord_info=analysis.get("chords"),
                    difficulty_info=analysis.get("difficulty"),
                    key_info=analysis.get("key"),
                )
                export_musicxml(annotated, str(export_path))
            log.info("Exported annotated score to %s", export_path)

            # Append extra PDF pages after MuseScore renders
            if ext == ".pdf" and (args.chord_guide or args.text_guide):
                from export.guide_pdf import render_chord_guide_pdf, render_guide_pdf, merge_pdfs
                pdfs_to_merge = [str(export_path)]
                tmp_paths = []
                try:
                    if args.chord_guide:
                        chord_path = str(export_path.with_stem(f"{export_path.stem}_chords_tmp"))
                        title = analysis.get("metadata", {}).get("title", "Score")
                        render_chord_guide_pdf(
                            analysis.get("chords", {}), chord_path,
                            key_info=analysis.get("key"),
                            title=f"{title} — Chord Reference",
                        )
                        pdfs_to_merge.append(chord_path)
                        tmp_paths.append(chord_path)

                    if args.text_guide and not args.no_llm:
                        explanation = generate_explanation(analysis)
                        if explanation:
                            title = analysis.get("metadata", {}).get("title", "Score")
                            guide_path = str(export_path.with_stem(f"{export_path.stem}_guide_tmp"))
                            render_guide_pdf(explanation, guide_path,
                                             title=f"{title} — Beginner's Guide")
                            pdfs_to_merge.append(guide_path)
                            tmp_paths.append(guide_path)
                    elif args.text_guide:
                        log.warning("--text-guide requires LLM (don't use --no-llm)")

                    if len(pdfs_to_merge) > 1:
                        merge_pdfs(pdfs_to_merge, str(export_path))
                        log.info("Appended guide pages to %s", export_path)
                except Exception as e:
                    log.warning("Guide PDF failed: %s — score PDF still available", e)
                finally:
                    for tmp in tmp_paths:
                        Path(tmp).unlink(missing_ok=True)

        except RuntimeError as e:
            log.error("Export failed: %s", e)
            if ext in (".pdf", ".png"):
                if annotated is None:
                    annotated = annotate_score(
                        score, profile,
                        chord_info=analysis.get("chords"),
                        difficulty_info=analysis.get("difficulty"),
                        key_info=analysis.get("key"),
                    )
                fallback = str(export_path.with_suffix(".musicxml"))
                export_musicxml(annotated, fallback)
                log.info("Fell back to MusicXML export: %s", fallback)

    # Generate report
    if args.format == "json":
        output = json.dumps(analysis, indent=2, default=str)
    else:
        explanation = None
        if not args.no_llm:
            try:
                explanation = generate_explanation(analysis)
            except Exception as e:
                log.warning("LLM explanation failed: %s — continuing without it", e)
                log.debug(traceback.format_exc())

        if args.format == "html":
            output = render_html(analysis, explanation)
        else:
            output = render_markdown(analysis, explanation)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        log.info("Report written to %s", args.output)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(output)

    log.info("Done. Debug log at: %s", os.path.abspath(args.log_file))


if __name__ == "__main__":
    main()
