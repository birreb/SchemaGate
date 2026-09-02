"""The text layer keeps the columns apart.

A parser's markdown can put a tax rate and an amount into one cell, and on a
Swedish invoice `25` beside `26 618,54` then reads as one number, since the
thousands separator is also a space. The parser knows where every piece of
text sits on the page, so lines are rebuilt from those positions: a gap wider
than a word space between two pieces becomes a column break, a space inside a
piece stays a space. Found by the ingestion benchmark, where every line item on
such a layout was refused by the product rule.
"""

from dataclasses import dataclass

from fpdf import FPDF

from schemagate.ingest.pdf import layout_lines, read_pdf


@dataclass
class Item:
    text: str
    x: float
    y: float
    width: float
    font_size: float = 9.0
    page: int = 1


def test_a_wide_gap_becomes_a_column_break() -> None:
    items = [Item("25", 300, 500, 10), Item("26 618,54", 360, 500, 45)]

    assert layout_lines(items) == "25 | 26 618,54"


def test_a_word_space_stays_a_space() -> None:
    items = [Item("Safety", 50, 500, 30), Item("relay", 83, 500, 25)]

    assert layout_lines(items) == "Safety relay"


def test_pieces_on_one_line_are_ordered_by_position_not_arrival() -> None:
    items = [Item("right", 300, 500, 25), Item("left", 50, 500, 20)]

    assert layout_lines(items) == "left | right"


def test_lines_run_top_to_bottom() -> None:
    items = [Item("lower", 50, 400, 25), Item("upper", 50, 500, 25)]

    assert layout_lines(items) == "upper\nlower"


def test_a_slight_baseline_difference_is_still_one_line() -> None:
    items = [Item("a", 50, 500.0, 5), Item("b", 120, 501.5, 5)]

    assert layout_lines(items) == "a | b"


def test_pages_are_kept_in_order_and_apart() -> None:
    items = [Item("second", 50, 700, 30, page=2), Item("first", 50, 100, 25, page=1)]

    assert layout_lines(items) == "first\n\nsecond"


def test_blank_pieces_are_dropped() -> None:
    items = [Item("  ", 50, 500, 5), Item("x", 60, 500, 5)]

    assert layout_lines(items) == "x"


def test_a_real_pdf_keeps_a_rate_apart_from_an_amount() -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(80, 8, "Safety relay 24V")
    pdf.cell(20, 8, "25", align="R")
    pdf.cell(40, 8, "12 579,25", align="R")

    text = read_pdf(bytes(pdf.output())).markdown

    assert "25 | 12 579,25" in text
    assert "25 12 579,25" not in text
