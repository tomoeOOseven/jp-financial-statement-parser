import difflib
from pathlib import Path

import pdfplumber
import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from fsparse import parse_statement
from fsparse.extract import page_to_lines


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.pdf"
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    document = canvas.Canvas(str(path), pagesize=(595, 842))
    document.setFont("HeiseiMin-W3", 12)

    document.drawString(40, 790, "（単位：千円）")
    document.drawString(40, 750, "現金及び預金")
    document.drawString(300, 748, "2,002,429")
    document.drawString(440, 750, "608,281")
    document.drawString(40, 710, "繰延税金資産")
    document.drawString(300, 696, "－")
    document.drawString(440, 696, "△1,234")

    document.drawString(40, 660, "連結")
    document.drawString(160, 658, "貸借対照表")
    document.drawString(40, 600, "Near A")
    document.drawString(40, 592, "Near B")
    document.save()
    return path


def test_page_to_lines_and_wrapped_label_parse(synthetic_pdf: Path):
    lines = page_to_lines(str(synthetic_pdf), page=0)

    assert "現金及び預金 2,002,429 608,281" in lines
    assert "繰延税金資産 - △1,234" in lines

    statement = parse_statement("\n".join(lines))
    cash = next(row for row in statement.rows if row.label == "現金及び預金")
    deferred_tax = next(row for row in statement.rows if row.label == "繰延税金資産")
    assert cash.raw == ["2,002,429", "608,281"]
    assert cash.values == [2_002_429, 608_281]
    assert deferred_tax.raw == ["-", "△1,234"]
    assert deferred_tax.values == [None, -1_234]


def test_y_tolerance_is_configurable(synthetic_pdf: Path):
    default_lines = page_to_lines(str(synthetic_pdf), page=0)
    zero_tolerance_lines = page_to_lines(
        str(synthetic_pdf), page=0, y_tolerance=0
    )
    large_tolerance_lines = page_to_lines(
        str(synthetic_pdf), page=0, y_tolerance=10
    )

    assert "連結 貸借対照表" in default_lines
    assert "現金及び預金 2,002,429 608,281" in default_lines
    assert "現金及び預金 608,281" in zero_tolerance_lines
    assert "2,002,429" in zero_tolerance_lines
    assert "Near A" in default_lines
    assert "Near B" in default_lines
    assert "Near Near A B" in large_tolerance_lines


def test_page_is_zero_based_and_out_of_range_is_reported(synthetic_pdf: Path):
    with pytest.raises(ValueError, match="zero-based"):
        page_to_lines(str(synthetic_pdf), page=-1)
    with pytest.raises(IndexError, match="outside the PDF"):
        page_to_lines(str(synthetic_pdf), page=1)


def test_page4_pdf_matches_golden_values():
    pdf_path = Path(__file__).with_name("page4.pdf")
    lines = page_to_lines(str(pdf_path), page=0)
    statement = parse_statement("\n".join(lines))
    text_statement = parse_statement(
        Path(__file__).with_name("page4.txt").read_text(encoding="utf-8")
    )

    assert statement.periods == text_statement.periods
    assert [(row.path, row.raw, row.values) for row in statement.rows] == [
        (row.path, row.raw, row.values) for row in text_statement.rows
    ]


def test_pdf_matches_text_fixture():
    pdf_lines = page_to_lines("tests/page4.pdf", 0)
    text = Path("tests/page4.txt").read_text(encoding="utf-8")
    text_lines = text.splitlines()
    pdf_stmt = parse_statement("\n".join(pdf_lines))
    text_stmt = parse_statement(text)
    diff = "\n".join(
        difflib.unified_diff(
            text_lines, pdf_lines, fromfile="tests/page4.txt", tofile="page_to_lines"
        )
    )

    assert pdf_stmt.periods == text_stmt.periods, diff
    assert pdf_stmt.unit == text_stmt.unit, diff
    assert pdf_stmt.flags == text_stmt.flags, diff
    assert pdf_stmt.warnings == text_stmt.warnings == [], diff
    assert [(r.path, r.values) for r in pdf_stmt.rows] == [
        (r.path, r.values) for r in text_stmt.rows
    ], diff


def test_page4_extracted_body_lines_match_text_fixture():
    pdf_lines = page_to_lines("tests/page4.pdf", 0)
    text_lines = Path("tests/page4.txt").read_text(encoding="utf-8").splitlines()
    body_start = "資産の部"
    body_end = "資産合計 7,534,110 5,135,249"
    pdf_body = pdf_lines[pdf_lines.index(body_start):pdf_lines.index(body_end) + 1]
    text_body = text_lines[text_lines.index(body_start):text_lines.index(body_end) + 1]
    diff = "\n".join(
        difflib.unified_diff(
            text_body, pdf_body, fromfile="page4.txt body", tofile="page_to_lines body"
        )
    )

    assert pdf_body == text_body, diff


def test_extracted_words_have_bounding_boxes(synthetic_pdf: Path):
    with pdfplumber.open(synthetic_pdf) as pdf:
        words = pdf.pages[0].extract_words()

    assert words
    assert all({"x0", "x1", "top", "bottom"} <= word.keys() for word in words)
