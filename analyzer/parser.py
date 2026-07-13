"""File ingestion — accepts .musicxml, .mscz, .mscx and returns a music21 Stream."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from music21 import converter
from music21.stream import Score

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".musicxml", ".mxl", ".xml", ".mscz", ".mscx"}


def _convert_mscz_to_musicxml(mscz_path: str) -> str:
    """Convert .mscz to .musicxml via MuseScore CLI. Returns temp file path."""
    from export.render import find_musescore
    mscore = find_musescore()
    if not mscore:
        raise RuntimeError(
            "MuseScore not found. Required to parse .mscz files. "
            "Install MuseScore 4 and ensure it's on PATH."
        )
    tmp = tempfile.NamedTemporaryFile(suffix=".musicxml", delete=False)
    tmp.close()
    try:
        log.info("Converting .mscz to MusicXML via MuseScore CLI...")
        result = subprocess.run(
            [mscore, "-o", tmp.name, mscz_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            os.unlink(tmp.name)
            raise RuntimeError(f"MuseScore conversion failed: {result.stderr}")
        log.info("Converted .mscz to MusicXML: %s", tmp.name)
        return tmp.name
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def parse_score(filepath: str) -> Score:
    """Parse a music file and return a validated music21 Score."""
    path = Path(filepath)
    log.info("Parsing file: %s", path)

    if not path.exists():
        log.error("File not found: %s", filepath)
        raise FileNotFoundError(f"File not found: {filepath}")

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        log.error("Unsupported extension '%s'", path.suffix)
        raise ValueError(
            f"Unsupported file type '{path.suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # .mscz files need conversion via MuseScore CLI first
    tmp_musicxml = None
    parse_path = str(path)
    if path.suffix.lower() == ".mscz":
        tmp_musicxml = _convert_mscz_to_musicxml(str(path))
        parse_path = tmp_musicxml

    log.debug("Calling music21 converter.parse()...")
    try:
        score = converter.parse(parse_path)
    finally:
        if tmp_musicxml:
            try:
                os.unlink(tmp_musicxml)
            except OSError:
                pass
    log.debug("converter.parse() returned type: %s", type(score).__name__)

    if not isinstance(score, Score):
        log.debug("Wrapping non-Score object in Score container")
        score = Score([score])

    # Validate: at least one part with notes
    parts = score.parts
    log.debug("Found %d part(s)", len(parts))
    if not parts:
        log.error("Parsed score contains no parts")
        raise ValueError("Parsed score contains no parts.")

    has_notes = any(part.recurse().notes for part in parts)
    if not has_notes:
        log.error("Parsed score contains no notes")
        raise ValueError("Parsed score contains no notes.")

    log.info("Parse OK — %d part(s) with notes", len(parts))
    return score


def extract_metadata(score: Score) -> dict:
    """Extract title, composer, and other metadata from the score."""
    md = score.metadata
    result = {
        "title": md.title if md and md.title else "Unknown",
        "composer": md.composer if md and md.composer else "Unknown",
        "number_of_parts": len(score.parts),
        "part_names": [p.partName or f"Part {i+1}" for i, p in enumerate(score.parts)],
    }
    log.debug("Metadata: %s", result)
    return result
