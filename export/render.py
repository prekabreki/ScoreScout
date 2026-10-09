"""Export annotated scores to MusicXML, PDF, and PNG."""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from music21.stream import Score

log = logging.getLogger(__name__)

# Common MuseScore install paths, by OS. PATH is checked first (below), so
# these are only the fallbacks for GUI installs that don't put a binary on PATH.
_MUSESCORE_CANDIDATES = [
    # Windows
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files (x86)\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe",
    # Linux (distro packages, Flatpak export, common AppImage drop)
    "/usr/bin/mscore",
    "/usr/bin/musescore",
    "/usr/local/bin/mscore",
    "/var/lib/flatpak/exports/bin/org.musescore.MuseScore",
    str(Path.home() / ".local/share/flatpak/exports/bin/org.musescore.MuseScore"),
    str(Path.home() / "Applications/MuseScore.AppImage"),
    # macOS
    "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
    "/Applications/MuseScore 3.app/Contents/MacOS/mscore",
]


def find_musescore() -> str | None:
    """Find MuseScore CLI executable. Returns path or None."""
    # Check PATH first (covers Linux distro/Flatpak `mscore`/`musescore` too)
    for name in ["musescore4", "musescore", "mscore", "MuseScore4.exe"]:
        path = shutil.which(name)
        if path:
            log.debug("Found MuseScore on PATH: %s", path)
            return path

    # Check common install locations
    for candidate in _MUSESCORE_CANDIDATES:
        if os.path.isfile(candidate):
            log.debug("Found MuseScore at: %s", candidate)
            return candidate

    log.debug("MuseScore not found")
    return None


def export_musicxml(score: Score, output_path: str) -> str:
    """Export annotated score to MusicXML file."""
    log.info("Exporting annotated MusicXML to %s", output_path)
    score.write("musicxml", fp=output_path)
    log.info("MusicXML written: %s (%.1f KB)", output_path, os.path.getsize(output_path) / 1024)
    return output_path


def export_pdf(score: Score, output_path: str) -> str:
    """Export annotated score to PDF via MuseScore CLI.

    Raises RuntimeError if MuseScore is not found.
    """
    mscore = find_musescore()
    if not mscore:
        raise RuntimeError(
            "MuseScore not found. Install MuseScore 4 and ensure it's on PATH.\n"
            "The annotated .musicxml file can be exported instead and opened in MuseScore manually."
        )

    return _render_via_musescore(score, output_path, mscore)


def export_png(score: Score, output_path: str) -> str:
    """Export annotated score to PNG via MuseScore CLI."""
    mscore = find_musescore()
    if not mscore:
        raise RuntimeError("MuseScore not found. Required for PNG export.")

    return _render_via_musescore(score, output_path, mscore)


def _render_via_musescore(score: Score, output_path: str, mscore_path: str) -> str:
    """Write score to temp MusicXML, render via MuseScore CLI."""
    with tempfile.NamedTemporaryFile(suffix=".musicxml", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        score.write("musicxml", fp=tmp_path)
        log.info("Rendering via MuseScore: %s -> %s", tmp_path, output_path)

        result = subprocess.run(
            [mscore_path, "-o", output_path, tmp_path],
            capture_output=True, text=True, timeout=120,
        )

        if result.returncode != 0:
            log.error("MuseScore failed (code %d): %s", result.returncode, result.stderr)
            raise RuntimeError(f"MuseScore rendering failed: {result.stderr}")

        # PNG export of a multi-page score: MuseScore ignores the exact output
        # filename and emits per-page files "<stem>-1.png", "<stem>-2.png", ...
        # so output_path itself may not exist. Resolve to whatever was produced.
        produced = _resolve_musescore_output(output_path)
        log.info("Export complete: %s (%.1f KB)", produced, os.path.getsize(produced) / 1024)
        return produced

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _resolve_musescore_output(output_path: str) -> str:
    """Return the actual file MuseScore wrote.

    For single-file formats (PDF/MusicXML) and single-page PNGs this is
    ``output_path`` unchanged. For a multi-page PNG, MuseScore writes
    ``<stem>-1<ext>``, ``<stem>-2<ext>``, ...; return the first page so callers
    get a real, sizeable path instead of a FileNotFoundError on the bare name.
    """
    if os.path.exists(output_path):
        return output_path
    stem, ext = os.path.splitext(output_path)
    first_page = f"{stem}-1{ext}"
    if os.path.exists(first_page):
        log.debug("MuseScore wrote paginated output; first page: %s", first_page)
        return first_page
    raise RuntimeError(f"MuseScore reported success but no output found at {output_path}")


def export_pdf_from_mscz(
    mscz_path: str,
    output_path: str,
    profile_name: str = "full",
    octaves: bool = False,
    chord_info: dict | None = None,
    key_info: dict | None = None,
) -> str:
    """Export PDF by injecting annotations directly into a .mscz file.

    This preserves MuseScore's original layout instead of round-tripping
    through music21's MusicXML export.
    """
    mscore = find_musescore()
    if not mscore:
        raise RuntimeError("MuseScore not found. Required for PDF export.")

    from export.mscz_inject import inject_mscz

    with tempfile.NamedTemporaryFile(suffix=".mscz", delete=False) as tmp:
        tmp_mscz = tmp.name

    try:
        inject_mscz(mscz_path, tmp_mscz, profile_name, octaves, chord_info, key_info)

        log.info("Rendering injected .mscz via MuseScore: %s", output_path)
        result = subprocess.run(
            [mscore, "-o", output_path, tmp_mscz],
            capture_output=True, text=True, timeout=120,
        )

        if result.returncode != 0:
            log.error("MuseScore failed (code %d): %s", result.returncode, result.stderr)
            raise RuntimeError(f"MuseScore rendering failed: {result.stderr}")

        log.info("Export complete: %s (%.1f KB)", output_path, os.path.getsize(output_path) / 1024)
        return output_path

    finally:
        try:
            os.unlink(tmp_mscz)
        except OSError:
            pass


def get_available_formats() -> list[str]:
    """Return list of available export formats based on installed software."""
    formats = ["musicxml"]
    if find_musescore():
        formats.extend(["pdf", "png"])
    return formats
