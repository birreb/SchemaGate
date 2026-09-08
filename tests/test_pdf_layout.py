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


def test_an_empty_cell_keeps_its_place_in_the_row() -> None:
    """A blank cell in a table is a column that has to survive the text layer.

    An invoice line printed as a fixed fee carries an amount and no quantity
    or unit price. Dropping the empty cells slides the tax rate into the
    quantity's place, and a reader of the text alone cannot tell. Found by the
    ingestion benchmark, on the line-item case whose layout has a rate column.
    """
    header = [
        Item("Bezeichnung", 50, 520, 55),
        Item("Menge", 200, 520, 28),
        Item("Einzelpreis", 260, 520, 50),
        Item("MwSt %", 340, 520, 32),
        Item("Betrag", 410, 520, 30),
    ]
    goods = [
        Item("Servo motor 750W", 50, 508, 80),
        Item("9", 220, 508, 8),
        Item("25,14", 285, 508, 25),
        Item("19", 360, 508, 12),
        Item("226,26", 414, 508, 26),
    ]
    fee = [
        Item("Installation labour", 50, 496, 78),
        Item("19", 360, 496, 12),
        Item("4 833,50", 405, 496, 35),
    ]

    rows = layout_lines(header + goods + fee).splitlines()

    assert rows[1] == "Servo motor 750W | 9 | 25,14 | 19 | 226,26"
    assert rows[2] == "Installation labour | | | 19 | 4 833,50"


def test_a_label_and_its_value_are_not_read_as_a_table() -> None:
    """Pairs down a page make columns nobody is short of.

    A value starts where its label ends, so three pairs with labels of three
    lengths look like three columns of which every row is missing one. A block
    is only read as a table once one of its rows has a cell in every column.
    """
    items = [
        Item("Invoice date", 50, 520, 50),
        Item("11 May 2026", 200, 520, 40),
        Item("Ship date", 50, 508, 40),
        Item("6 June 2026", 300, 508, 40),
        Item("Due date", 50, 496, 38),
        Item("10 June 2026", 200, 496, 42),
    ]

    assert layout_lines(items).splitlines()[1] == "Ship date | 6 June 2026"


def test_a_row_that_runs_across_two_columns_keeps_the_breaks_of_the_others() -> None:
    """One wide cell joins two columns, and the rows below still have both."""
    items = [
        Item("a", 50, 520, 40),
        Item("b", 120, 520, 40),
        Item("c", 200, 520, 40),
        Item("d", 280, 520, 40),
        Item("one wide cell", 50, 508, 110),
        Item("c2", 200, 508, 40),
        Item("d2", 280, 508, 40),
        Item("e", 50, 496, 40),
        Item("f", 120, 496, 40),
        Item("g", 200, 496, 40),
        Item("h", 280, 496, 40),
    ]

    assert layout_lines(items).splitlines()[0] == "a | b | c | d"


def test_a_total_under_a_table_is_not_a_row_of_it() -> None:
    """The step down to a total is wider than the rows leave between them."""
    items = [
        Item("Item", 50, 520, 40),
        Item("Qty", 200, 520, 30),
        Item("Amount", 300, 520, 40),
        Item("Relay", 50, 508, 40),
        Item("2", 200, 508, 8),
        Item("18,00", 300, 508, 30),
        Item("Bolt", 50, 496, 35),
        Item("7", 200, 496, 8),
        Item("21,00", 300, 496, 30),
        Item("Summa", 50, 460, 40),
        Item("39,00", 300, 460, 30),
    ]

    assert layout_lines(items).splitlines()[-1] == "Summa | 39,00"


def test_a_real_pdf_keeps_an_empty_cell_in_a_row() -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    rows = [
        ("Description", "Qty", "Unit", "VAT", "Amount"),
        ("Servo motor 750W", "9", "25,14", "19", "226,26"),
        ("Installation labour", "", "", "19", "4 833,50"),
        ("Safety relay 24V", "3", "12,00", "19", "36,00"),
    ]
    for description, quantity, unit, vat, amount in rows:
        pdf.cell(70, 8, description)
        pdf.cell(20, 8, quantity, align="R")
        pdf.cell(30, 8, unit, align="R")
        pdf.cell(20, 8, vat, align="R")
        pdf.cell(30, 8, amount, align="R")
        pdf.ln()

    text = read_pdf(bytes(pdf.output())).markdown

    assert "Installation labour | | | 19 | 4 833,50" in text
