"""Regression test for merge_pdfs (export/guide_pdf.py, audit C2).

merge_pdfs must be safe when the output path is also one of the inputs:
buffer every input fully before writing the merged result.
"""

from fpdf import FPDF
from pypdf import PdfReader

from export.guide_pdf import merge_pdfs


def _make_pdf(path: str, text: str) -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, text)
    pdf.output(path)
    return path


def test_merge_pdfs_output_equals_input(tmp_path):
    a = _make_pdf(str(tmp_path / "a.pdf"), "page A1")
    b = _make_pdf(str(tmp_path / "b.pdf"), "page B1")
    # 'a' has 1 page, 'b' has 1 page -> but task asks for a 3-page result,
    # so build 'a' with two pages.
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "A page 1")
    pdf.add_page()
    pdf.cell(0, 10, "A page 2")
    pdf.output(a)

    # Output path == first input. Must not corrupt the merge.
    result = merge_pdfs([a, b], a)

    assert result == a
    reader = PdfReader(a)
    assert len(reader.pages) == 3  # 2 from a + 1 from b


def test_merge_pdfs_distinct_output(tmp_path):
    a = _make_pdf(str(tmp_path / "x.pdf"), "x")
    b = _make_pdf(str(tmp_path / "y.pdf"), "y")
    out = str(tmp_path / "merged.pdf")
    merge_pdfs([a, b], out)
    assert len(PdfReader(out).pages) == 2
