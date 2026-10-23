"""PDF text extraction for statement pages."""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass

import pdfplumber

from .core import parse_cell

_DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")


@dataclass
class _Row:
    words: list[tuple[float, float, float, str]]

    @property
    def top(self) -> float:
        return min(word[1] for word in self.words)

    @property
    def bottom(self) -> float:
        return max(word[2] for word in self.words)

    @property
    def left(self) -> float:
        return min(word[0] for word in self.words)

    @property
    def height(self) -> float:
        return statistics.median(word[2] - word[1] for word in self.words)

    @property
    def is_numeric(self) -> bool:
        return bool(self.words) and all(parse_cell(word[3])[0] for word in self.words)

    @property
    def is_text(self) -> bool:
        return bool(self.words) and not any(parse_cell(word[3])[0] for word in self.words)


def _parser_text(text: str) -> str:
    if parse_cell(text)[0]:
        return text
    normalized = text.replace("\N{MINUS SIGN}", "-")
    return normalized if parse_cell(normalized)[0] else text


def _format_row(words: list[tuple[float, float, float, str]]) -> str:
    return " ".join(
        word[3] for word in sorted(words, key=lambda word: word[0])
    )


def _close_rows(upper: _Row, lower: _Row) -> bool:
    line_height = statistics.median((upper.height, lower.height))
    upper_center = (upper.top + upper.bottom) / 2
    lower_center = (lower.top + lower.bottom) / 2
    return lower_center - upper_center <= 1.5 * line_height


def page_to_lines(
    pdf_path: str, page: int, y_tolerance: float = 3.0
) -> list[str]:
    """Extract one PDF page as parser-ready lines.

    ``page`` is zero-based. Words whose top coordinates are within
    ``y_tolerance`` points of a row's first word are grouped into that row.
    """
    if page < 0:
        raise ValueError("page must be a zero-based non-negative page number")
    if not math.isfinite(y_tolerance) or y_tolerance < 0:
        raise ValueError("y_tolerance must be a finite non-negative number")

    with pdfplumber.open(pdf_path) as pdf:
        if page >= len(pdf.pages):
            raise IndexError(f"page {page} is outside the PDF ({len(pdf.pages)} pages)")
        words = pdf.pages[page].extract_words()

    ordered_words = sorted(words, key=lambda word: (float(word["top"]), float(word["x0"])))
    rows: list[_Row] = []
    row_tops: list[float] = []
    for word in ordered_words:
        top = float(word["top"])
        if not rows or top - row_tops[-1] > y_tolerance:
            rows.append(_Row([]))
            row_tops.append(top)
        rows[-1].words.append(
            (
                float(word["x0"]),
                top,
                float(word["bottom"]),
                _parser_text(str(word["text"])),
            )
        )

    lines: list[str] = []
    row_index = 0
    while row_index < len(rows):
        row = rows[row_index]
        if (
            row.is_text
            and row_index + 2 < len(rows)
            and rows[row_index + 1].is_numeric
            and rows[row_index + 2].is_text
        ):
            amounts, label_end = rows[row_index + 1], rows[row_index + 2]
            x_tolerance = max(row.height, label_end.height) * 0.2
            if (
                abs(row.left - label_end.left) <= x_tolerance
                and _close_rows(row, amounts)
                and _close_rows(amounts, label_end)
            ):
                label = "".join(
                    word[3]
                    for part in (row, label_end)
                    for word in sorted(part.words, key=lambda item: item[0])
                )
                values = _format_row(amounts.words)
                lines.append(f"{label} {values}")
                row_index += 3
                continue
        if (
            row.is_text
            and row_index + 1 < len(rows)
            and rows[row_index + 1].is_numeric
            and _close_rows(row, rows[row_index + 1])
        ):
            row = _Row(row.words + rows[row_index + 1].words)
            row_index += 1
        lines.append(_format_row(row.words))
        row_index += 1
    return lines


def join_pages(pages: list[list[str]]) -> list[str]:
    """Join statement page lines, retaining period headers from the first page only."""
    if not pages:
        return []
    return pages[0] + [
        line
        for page_lines in pages[1:]
        for line in page_lines
        if not _DATE_RE.search(line)
    ]
