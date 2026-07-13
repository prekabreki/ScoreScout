"""Render beginner-friendly PDF pages: text guide and/or chord reference.

These are standalone PDFs that get merged after MuseScore renders the score,
so we have full control over formatting without fighting MusicXML.

NOTE (audit M10): this module mixes several concerns — a hand-rolled
word-wrapping/text-layout engine (_split_rich_words, _parse_sections, the
manual line breaking in render_guide_pdf) alongside the chord-reference and
PDF-merge helpers. The audit flags splitting it into guide / chord-ref / merge
modules and replacing the bespoke layout with fpdf's multi_cell. That is a
structural refactor tracked separately and intentionally OUT OF SCOPE for the
M1-M13 bug-fix bundle; left as-is here to avoid destabilizing PDF output.
"""

import logging
import re
from collections import Counter
from pathlib import Path

from fpdf import FPDF

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared PDF base
# ---------------------------------------------------------------------------

class _StyledPDF(FPDF):
    def __init__(self, header_text: str = ""):
        super().__init__()
        self._header_text = _sanitize(header_text) if header_text else ""
        self.set_auto_page_break(auto=True, margin=12)

    def header(self):
        if self._header_text:
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(160, 160, 160)
            self.cell(0, 5, self._header_text, align="R")
            self.ln(6)

    def footer(self):
        pass  # no footer — saves vertical space


# ---------------------------------------------------------------------------
# Text guide (Claude explanation) — compact single-page layout
# ---------------------------------------------------------------------------

def render_guide_pdf(explanation: str, output_path: str, title: str = "Beginner's Guide") -> str:
    """Render the Claude explanation as a well-formatted two-column PDF.

    Aims for one page but allows overflow to a second if the text is long.
    Readability is prioritised over cramming.
    """
    title = _sanitize(title) or "Beginner's Guide"
    explanation = _sanitize(explanation)

    BODY_SIZE = 8.5
    HEADING_SIZE = 10
    LINE_H = BODY_SIZE * 0.48      # line height in mm (fpdf units)
    PARA_GAP = 3.0                  # space after a paragraph
    HEADING_GAP_BEFORE = 4.5        # space before a heading
    HEADING_GAP_AFTER = 1.5         # space after a heading

    pdf = _StyledPDF(header_text="")
    pdf.set_auto_page_break(auto=False)  # we handle column breaks manually
    pdf.add_page()

    # Title bar
    pdf.set_fill_color(55, 65, 81)
    pdf.rect(0, 10, 210, 12, style="F")
    pdf.set_xy(10, 11)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 10, title)
    pdf.set_y(26)

    # Two-column geometry
    margin = 10
    gutter = 8
    col_w = (pdf.w - 2 * margin - gutter) / 2
    left_x = margin
    right_x = left_x + col_w + gutter
    col_bottom = pdf.h - 12  # leave margin at bottom

    # Collect content blocks
    blocks: list[tuple[str, str]] = []
    for heading, body in _parse_sections(explanation):
        if heading:
            blocks.append(("heading", heading))
        if body:
            for para in body.strip().split("\n\n"):
                clean = para.strip().replace("\n", " ")
                if clean:
                    blocks.append(("body", clean))

    col = 0  # 0 = left, 1 = right
    col_top = pdf.get_y()

    def _cur_x():
        return left_x if col == 0 else right_x

    def _advance_col():
        nonlocal col
        if col == 0:
            col = 1
            pdf.set_xy(right_x, col_top)
            return True
        else:
            # Both columns full — new page
            pdf.add_page()
            col = 0
            pdf.set_y(14)
            return True

    for kind, text in blocks:
        if kind == "heading":
            # Space before heading (unless we're at the very top of a column)
            if pdf.get_y() > col_top + 2:
                pdf.set_y(pdf.get_y() + HEADING_GAP_BEFORE)

            # Check if heading + a few lines of body fit
            if pdf.get_y() + 12 > col_bottom:
                _advance_col()

            pdf.set_x(_cur_x())
            pdf.set_font("Helvetica", "B", HEADING_SIZE)
            pdf.set_text_color(55, 65, 81)
            pdf.cell(col_w, 5, text, new_x="LMARGIN", new_y="NEXT")
            pdf.set_y(pdf.get_y() + HEADING_GAP_AFTER)

        else:  # body
            cx = _cur_x()
            cy = pdf.get_y()
            pdf.set_font("Helvetica", "", BODY_SIZE)
            pdf.set_text_color(70, 70, 70)

            # Fully manual word placement — no pdf.write() to avoid
            # fpdf's internal cursor drifting outside our column.
            words = _split_rich_words(text)
            for word, bold in words:
                if bold:
                    pdf.set_font("Helvetica", "B", BODY_SIZE)
                else:
                    pdf.set_font("Helvetica", "", BODY_SIZE)

                w = pdf.get_string_width(word + " ")

                # Wrap to next line if word exceeds column width
                if cx - _cur_x() + w > col_w:
                    cy += LINE_H
                    cx = _cur_x()
                    # Column overflow check
                    if cy + LINE_H > col_bottom:
                        _advance_col()
                        cx = _cur_x()
                        cy = pdf.get_y()

                pdf.text(cx, cy, word + " ")
                cx += w

            pdf.set_font("Helvetica", "", BODY_SIZE)
            # Move cursor below the last line of this paragraph
            pdf.set_xy(_cur_x(), cy + LINE_H + PARA_GAP)
            if pdf.get_y() > col_bottom:
                _advance_col()

    pdf.output(output_path)
    log.info("Guide PDF written: %s (%.1f KB)", output_path, Path(output_path).stat().st_size / 1024)
    return output_path


# ---------------------------------------------------------------------------
# Chord reference — two-column grid, one page
# ---------------------------------------------------------------------------

_CHORD_DESCRIPTIONS: dict[str, str] = {
    "":      "Bright, happy",
    "m":     "Dark, sad",
    "7":     "Bluesy, wants to resolve",
    "m7":    "Jazzy, mellow",
    "maj7":  "Dreamy, lush",
    "dim":   "Tense, spooky",
    "aug":   "Mysterious, unsettled",
    "sus2":  "Open, airy",
    "sus4":  "Anticipation, unresolved",
    "sus":   "Anticipation, unresolved",
    "add9":  "Bright with extra color",
    "9":     "Rich, complex",
    "power": "Strong, simple",
    "dim7":  "Very tense",
}

_CHORD_INTERVALS: dict[str, list[int]] = {
    "":      [0, 4, 7],
    "m":     [0, 3, 7],
    "7":     [0, 4, 7, 10],
    "m7":    [0, 3, 7, 10],
    "maj7":  [0, 4, 7, 11],
    "dim":   [0, 3, 6],
    "aug":   [0, 4, 8],
    "sus2":  [0, 2, 7],
    "sus4":  [0, 5, 7],
    "sus":   [0, 5, 7],
    "add9":  [0, 4, 7, 14],
    "9":     [0, 4, 7, 10, 14],
    "power": [0, 7],
    "dim7":  [0, 3, 6, 9],
}

_NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def render_chord_guide_pdf(chord_info: dict, output_path: str,
                            key_info: dict | None = None,
                            title: str = "Chord Reference") -> str:
    """Render a chord reference as a compact two-column single-page PDF."""
    from export.annotate import _fix_flats
    title = _sanitize(title) or "Chord Reference"
    top_chords = _get_top_chords(chord_info, max_chords=12)
    if not top_chords:
        log.debug("Chord guide: no usable chords, skipping")
        return ""

    pdf = _StyledPDF(header_text="")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    # Title bar
    pdf.set_fill_color(55, 65, 81)
    pdf.rect(0, 10, 210, 12, style="F")
    pdf.set_xy(10, 11)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(255, 255, 255)
    title_line = title
    if key_info:
        detected = key_info.get("detected_key", "")
        mode = key_info.get("detected_mode", "")
        if detected:
            title_line += f"   |   Key: {detected} {mode}".strip()
    pdf.cell(0, 10, title_line)

    # Subtitle
    pdf.set_xy(10, 24)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(130, 130, 130)
    pdf.cell(0, 5, "Most common chords in this piece, ranked by frequency.")
    pdf.ln(7)

    # Two-column grid
    col_w = (pdf.w - 20 - 8) / 2  # 10mm margins, 8mm gutter
    left_x = 10
    right_x = left_x + col_w + 8
    start_y = pdf.get_y()

    half = (len(top_chords) + 1) // 2  # left column gets the extra one if odd

    for idx, (symbol, count) in enumerate(top_chords):
        root, quality = _parse_chord_symbol(symbol)
        if root is None:
            continue

        # Power chords: display as root/fifth instead of "Xpower"
        if quality == "power" and root in _NOTE_NAMES:
            root_midi = _NOTE_NAMES.index(root)
            fifth = _NOTE_NAMES[(root_midi + 7) % 12]
            symbol = f"{root}/{fifth}"

        # Decide column
        if idx < half:
            col_x = left_x
            row_in_col = idx
        else:
            col_x = right_x
            row_in_col = idx - half

        card_h = 20  # height per chord card
        card_y = start_y + row_in_col * (card_h + 3)

        # Card background
        pdf.set_fill_color(245, 247, 250)
        pdf.rect(col_x, card_y, col_w, card_h, style="F")

        # Chord name (large) — fix music21 flat notation for display
        display_symbol = _fix_flats(symbol)
        pdf.set_xy(col_x + 3, card_y + 1)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(40, 40, 40)
        pdf.cell(30, 7, display_symbol)

        # Frequency badge
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 7, f"x{count}")

        # Notes row
        intervals = _CHORD_INTERVALS.get(quality, _CHORD_INTERVALS.get("", [0, 4, 7]))
        root_midi = _NOTE_NAMES.index(root) if root in _NOTE_NAMES else 0
        concrete = [_NOTE_NAMES[(root_midi + i) % 12] for i in intervals]

        pdf.set_xy(col_x + 3, card_y + 8.5)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(55, 65, 81)
        pdf.cell(0, 5, " - ".join(concrete))

        # Description
        desc = _CHORD_DESCRIPTIONS.get(quality, "")
        if desc:
            pdf.set_xy(col_x + 3, card_y + 14)
            pdf.set_font("Helvetica", "I", 7.5)
            pdf.set_text_color(110, 110, 110)
            pdf.cell(0, 4, desc)

    pdf.output(output_path)
    log.info("Chord guide PDF written: %s (%.1f KB)", output_path, Path(output_path).stat().st_size / 1024)
    return output_path


# ---------------------------------------------------------------------------
# PDF merge
# ---------------------------------------------------------------------------

def merge_pdfs(paths: list[str], output_path: str) -> str:
    """Merge multiple PDF files into one.

    Safe when ``output_path`` is also one of ``paths``: all inputs are read in
    full (each input's bytes are buffered and pages cloned into the writer)
    before any input handle is closed, then the merged result is written to a
    temporary file and atomically moved onto ``output_path``.
    """
    import os
    import tempfile
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for path in paths:
        # Buffer the whole input so we never stream from a file we may overwrite.
        with open(path, "rb") as src:
            reader = PdfReader(BytesIO(src.read()))
        for page in reader.pages:
            writer.add_page(page)

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            writer.write(f)
        os.replace(tmp_path, output_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    log.info("Merged %d PDFs into %s (%.1f KB)", len(paths), output_path, Path(output_path).stat().st_size / 1024)
    return output_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize(text: str) -> str:
    """Replace Unicode chars that Helvetica can't render.

    Helvetica only reliably supports ASCII + Latin-1 Supplement (U+0000-U+00FF).
    Anything outside that range gets transliterated or dropped.
    """
    import unicodedata
    text = (text
            .replace("\u2014", " - ")
            .replace("\u2013", " - ")
            .replace("\u2018", "'")
            .replace("\u2019", "'")
            .replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2026", "...")
            .replace("\u00b7", "-")
            )
    normalized = unicodedata.normalize("NFKD", text)
    out = []
    for ch in normalized:
        cp = ord(ch)
        if cp < 0x80 or 0x00A0 <= cp <= 0x00FF:
            out.append(ch)
        # Anything else (combining marks, other non-Latin-1 codepoints) is dropped.
    return "".join(out).strip()


def _get_top_chords(chord_info: dict, max_chords: int = 12) -> list[tuple[str, int]]:
    """Extract the most common usable chord symbols from analysis."""
    from export.annotate import _simplify_chord_name, _is_usable_chord_name

    progression = chord_info.get("chord_progression_full", [])
    counts: Counter = Counter()
    for entry in progression:
        name = entry.get("name", "")
        if not name or name.startswith("["):
            continue
        simple = _simplify_chord_name(name)
        if simple and _is_usable_chord_name(simple):
            counts[simple] += 1
    return counts.most_common(max_chords)


def _parse_chord_symbol(symbol: str) -> tuple[str | None, str]:
    """Parse 'Cmaj7' -> ('C', 'maj7'), 'Bm' -> ('B', 'm')."""
    if not symbol:
        return None, ""
    i = 1
    if len(symbol) > 1 and symbol[1] in '#b-':
        i = 2
        root = symbol[0] + ('b' if symbol[1] in 'b-' else '#')
    else:
        root = symbol[:i]
    quality = symbol[i:]
    return root, quality


def _parse_sections(text: str) -> list[tuple[str, str]]:
    """Parse markdown-ish text into (heading, body) tuples."""
    sections: list[tuple[str, str]] = []
    lines = text.split("\n")
    current_heading = ""
    current_body: list[str] = []

    for line in lines:
        heading_match = re.match(r"^#{1,3}\s+(.+)$", line.strip())
        if not heading_match:
            heading_match = re.match(r"^\*\*([^*]+)\*\*\s*$", line.strip())
        if heading_match:
            if current_heading or current_body:
                sections.append((current_heading, "\n".join(current_body)))
            current_heading = heading_match.group(1).strip()
            current_body = []
        else:
            current_body.append(line)

    if current_heading or current_body:
        sections.append((current_heading, "\n".join(current_body)))
    return sections


def _split_rich_words(text: str) -> list[tuple[str, bool]]:
    """Split text into (word, is_bold) tuples, handling **bold** markers."""
    result: list[tuple[str, bool]] = []
    parts = re.split(r'(\*\*[^*]+\*\*)', text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            for w in part[2:-2].split():
                result.append((w, True))
        else:
            for w in part.split():
                result.append((w, False))
    return result
