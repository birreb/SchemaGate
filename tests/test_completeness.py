"""A document that lists five invoices and a model that returns two.

Nothing in a row says how many rows there should have been. The document does:
it names every invoice, and the numbers share a shape. When the text carries
more values of that shape than the rows returned, the document is reported as
incomplete, with the values nobody returned. Found by the ingestion benchmark,
where two statements lost three rows of five and nothing said so.
"""

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.completeness import find_uncounted

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="invoice_number", data_type="varchar", nullable=False, ordinal=1),
        ColumnSpec(name="supplier", data_type="text", nullable=False, ordinal=2),
        ColumnSpec(name="vat_id", data_type="text", nullable=True, ordinal=3),
    ),
)

STATEMENT = """KONTOUTDRAG
Northgate Supply Co.  Momsreg.nr: SE556000000001
Fakturamottagare Halvard Industri AB  VAT: SE559012345601
F20260372  2026-07-16  31 361,58
F20260374  2026-07-10  65 076,55
F20260380  2026-07-03  61 483,64
F20260389  2026-03-12  93 217,15
F20260398  2026-06-20  36 191,38
Att betala: 287 330,30 SEK. Ange fakturanummer F20260372 vid betalning.
"""


def rows(*numbers: str) -> list[dict[str, object]]:
    return [
        {"invoice_number": number, "supplier": "Northgate Supply Co.", "vat_id": "SE556000000001"}
        for number in numbers
    ]


def test_rows_the_document_names_but_the_model_skipped_are_reported() -> None:
    failure = find_uncounted(STATEMENT, rows("F20260372", "F20260374"), INVOICES)

    assert failure is not None
    assert failure.rule == "incomplete"
    assert failure.column == "invoice_number"
    for number in ("F20260380", "F20260389", "F20260398"):
        assert number in failure.detail
    assert "F20260372" not in failure.detail.split(":")[-1]


def test_a_complete_answer_is_not_reported() -> None:
    complete = rows("F20260372", "F20260374", "F20260380", "F20260389", "F20260398")

    assert find_uncounted(STATEMENT, complete, INVOICES) is None


def test_a_number_repeated_in_the_footer_is_not_a_missing_row() -> None:
    text = "FAKTURA F20260105\nBetalning: ange fakturanummer F20260105.\nF20260106 is another.\n"

    failure = find_uncounted(text, rows("F20260105", "F20260106"), INVOICES)

    assert failure is None


def test_a_column_whose_values_repeat_across_rows_is_not_a_key() -> None:
    # Every row carries the same VAT number, so the buyer's number in the
    # text, which has the same shape, must not be read as a missing row.
    same_vat = rows("F20260372", "F20260374")

    failure = find_uncounted(STATEMENT, same_vat, INVOICES)

    assert failure is not None
    assert failure.column == "invoice_number"


def test_values_without_a_letter_prefix_are_not_used() -> None:
    # Pure digits would match postcodes, account numbers and amounts.
    text = "Invoice 10535 for 49503 Grand Rapids, account 9988776655. Invoice 10536.\n"
    numeric = [
        {"invoice_number": "10535", "supplier": "s", "vat_id": None},
        {"invoice_number": "10536", "supplier": "s", "vat_id": None},
    ]

    assert find_uncounted(text, numeric, INVOICES) is None


def test_one_row_from_a_statement_that_names_five_is_reported() -> None:
    failure = find_uncounted(STATEMENT, rows("F20260372"), INVOICES)

    assert failure is not None
    assert failure.rule == "incomplete"


def test_one_row_beside_one_other_reference_is_not_reported() -> None:
    text = "FAKTURA F20260105\nErsätter faktura F20260099.\nAtt betala 100,00\n"

    assert find_uncounted(text, rows("F20260105"), INVOICES) is None


def test_the_buyers_vat_number_is_not_a_missing_row_on_a_single_invoice() -> None:
    text = (
        "FAKTURA F20260105\nMomsreg.nr SE556000000001\nVAT: SE559012345601\nVAT: SE559012345602\n"
    )

    assert find_uncounted(text, rows("F20260105"), INVOICES) is None


def test_no_rows_from_a_document_with_text_is_reported_as_empty() -> None:
    failure = find_uncounted(STATEMENT, [], INVOICES)

    assert failure is not None
    assert failure.rule == "empty"
    assert failure.column is None


def test_no_rows_from_almost_no_text_is_not_reported() -> None:
    assert find_uncounted("Page 1", [], INVOICES) is None
