"""Shared annotated-export orchestrator.

Both front ends export through :func:`export_annotated`, so the .mscz-vs-music21
decision, the format dispatch, the extra guide-page merge and the MusicXML
fallback live in exactly one place. Previously ``cli.py`` and ``app.py`` each
held a slightly different copy of that tree and had already diverged (the app
normalised unknown formats to MusicXML, the CLI wrote them to the given path;
the app tracked a download filename, the CLI a filesystem path).
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from music21.stream import Score

from export.profiles import get_profile
from export.annotate import annotate_score
from export.render import (
    export_musicxml,
    export_pdf,
    export_png,
    export_pdf_from_mscz,
)
from export.guide_pdf import render_chord_guide_pdf, render_guide_pdf, merge_pdfs
from llm.explain import generate_explanation

log = logging.getLogger("sheet_music_analyzer")

_RENDER_FORMATS = ("pdf", "png")


@dataclass(frozen=True)
class ExportResult:
    """Where an export landed, after any format coercion or fallback."""

    path: str
    fmt: str
    fallback: bool = False


def _annotate(score: Score, analysis: dict, profile):
    """Build the annotated music21 score shared by every non-injection path."""
    return annotate_score(
        score, profile,
        chord_info=analysis.get("chords"),
        difficulty_info=analysis.get("difficulty"),
        key_info=analysis.get("key"),
    )


def export_annotated(
    score: Score,
    analysis: dict,
    fmt: str,
    *,
    out_path: str,
    profile_name: str = "full",
    include_octaves: bool = False,
    source_path: str | None = None,
    include_chord_guide: bool = False,
    include_text_guide: bool = False,
    explanation: str | None = None,
    allow_llm: bool = True,
    on_explanation=None,
) -> ExportResult:
    """Annotate ``score`` and export it in ``fmt`` to ``out_path``.

    ``source_path`` is the original score file, if known: a ``.mscz`` PDF export
    uses direct XML injection to preserve layout (unless the ``clean`` profile
    is requested, which skips annotation entirely). Otherwise the score is
    annotated through music21.

    ``include_chord_guide`` / ``include_text_guide`` append extra pages to a
    PDF. A text guide reuses ``explanation`` when provided; otherwise it is
    generated via Claude when ``allow_llm`` is set (``generate_explanation``
    itself no-ops without an API key). ``on_explanation`` is called with any
    freshly generated explanation so a caller can persist it.

    Returns an :class:`ExportResult`. If a PDF/PNG export needs MuseScore and
    it is unavailable, the export falls back to annotated MusicXML and the
    result reports ``fallback=True``.
    """
    profile = get_profile(profile_name, include_octaves=include_octaves)
    title = analysis.get("metadata", {}).get("title", "Score")

    is_mscz = bool(source_path) and Path(source_path).suffix.lower() == ".mscz"
    effective_fmt = fmt if fmt in _RENDER_FORMATS else "musicxml"
    if effective_fmt == "musicxml" and Path(out_path).suffix.lower() != ".musicxml":
        out_path = str(Path(out_path).with_suffix(".musicxml"))

    annotated = None
    try:
        if effective_fmt == "pdf":
            if is_mscz and profile_name != "clean":
                log.info("Using .mscz injection path for %s", Path(source_path).name)
                export_pdf_from_mscz(
                    source_path, out_path,
                    profile_name=profile_name,
                    octaves=include_octaves,
                    chord_info=analysis.get("chords"),
                )
            else:
                annotated = _annotate(score, analysis, profile)
                export_pdf(annotated, out_path)
        elif effective_fmt == "png":
            annotated = _annotate(score, analysis, profile)
            export_png(annotated, out_path)
        else:
            annotated = _annotate(score, analysis, profile)
            export_musicxml(annotated, out_path)

        if effective_fmt == "pdf" and (include_chord_guide or include_text_guide):
            out_path = _append_guides(
                out_path, analysis, title,
                include_chord_guide=include_chord_guide,
                include_text_guide=include_text_guide,
                explanation=explanation,
                allow_llm=allow_llm,
                on_explanation=on_explanation,
            )

        log.info("Exported annotated score to %s", out_path)
        return ExportResult(path=out_path, fmt=effective_fmt)

    except RuntimeError as e:
        # Only PDF/PNG depend on MuseScore; a MusicXML failure is a real error.
        if effective_fmt not in _RENDER_FORMATS:
            raise
        log.error("Export failed: %s", e)
        if annotated is None:
            annotated = _annotate(score, analysis, profile)
        fallback = str(Path(out_path).with_suffix(".musicxml"))
        export_musicxml(annotated, fallback)
        log.info("Fell back to MusicXML export: %s", fallback)
        return ExportResult(path=fallback, fmt="musicxml", fallback=True)


def _append_guides(
    out_path: str,
    analysis: dict,
    title: str,
    *,
    include_chord_guide: bool,
    include_text_guide: bool,
    explanation: str | None,
    allow_llm: bool,
    on_explanation,
) -> str:
    """Merge chord-reference and/or text-guide pages into the score PDF.

    Guide failures never sink the score PDF: they are logged and the original
    export is returned. Merging writes back onto ``out_path`` (``merge_pdfs``
    reads every input fully before writing), so the returned path is unchanged.
    """
    stem_path = Path(out_path)
    pdfs_to_merge = [out_path]
    tmp_paths: list[str] = []

    try:
        if include_chord_guide:
            chord_path = str(stem_path.with_stem(f"{stem_path.stem}_chords_tmp"))
            render_chord_guide_pdf(
                analysis.get("chords", {}), chord_path,
                key_info=analysis.get("key"),
                title=f"{title} \u2014 Chord Reference",
            )
            pdfs_to_merge.append(chord_path)
            tmp_paths.append(chord_path)

        if include_text_guide:
            if explanation is None and allow_llm:
                explanation = generate_explanation(analysis)
                if explanation and on_explanation:
                    on_explanation(explanation)
            if explanation:
                guide_path = str(stem_path.with_stem(f"{stem_path.stem}_guide_tmp"))
                render_guide_pdf(explanation, guide_path,
                                 title=f"{title} \u2014 Beginner's Guide")
                pdfs_to_merge.append(guide_path)
                tmp_paths.append(guide_path)
            else:
                log.warning("Text guide requested but no explanation available; skipping")

        if len(pdfs_to_merge) > 1:
            merge_pdfs(pdfs_to_merge, out_path)
            log.info("Appended guide pages to %s", out_path)

    except Exception as e:
        log.warning("Guide PDF failed: %s - score PDF still available", e)
    finally:
        for tmp in tmp_paths:
            Path(tmp).unlink(missing_ok=True)

    return out_path
