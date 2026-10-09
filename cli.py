#!/usr/bin/env python3
"""Sheet Music Analyzer — CLI entry point."""

import argparse
import json
import logging
import sys
import os
import traceback
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.pipeline import run_analysis
from core.export import export_annotated
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
        analysis, score = run_analysis(
            args.file,
            use_llm=not args.no_llm,
            title_fallback=Path(args.file).name,
        )
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
        from config import EXPORT_DIR

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
        fmt = "pdf" if ext == ".pdf" else "png" if ext == ".png" else "musicxml"

        try:
            export_annotated(
                score, analysis, fmt,
                out_path=str(export_path),
                profile_name=args.annotations,
                include_octaves=args.octaves,
                source_path=args.file,
                include_chord_guide=args.chord_guide,
                include_text_guide=args.text_guide,
                allow_llm=not args.no_llm,
            )
        except RuntimeError as e:
            log.error("Export failed: %s", e)

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
