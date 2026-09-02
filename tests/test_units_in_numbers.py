"""A number printed with its unit or currency code is still that number.

Invoices print `2 st`, `3,5 Std`, `25 %` and `34 768,38 SEK`, and a model told
to copy values exactly copies the unit too. The gate already ignores currency
symbols; a code or a unit word at either end is the same kind of presentation.
Letters inside the digits are not, and stay refused. Found by the ingestion
benchmark, where every German line item was lost to `Stk`.
"""

from decimal import Decimal

import pytest

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.gate import validate


def one(data_type: str, text: str, scale: int | None = None) -> tuple[object, list[str]]:
    column = ColumnSpec(
        name="v", data_type=data_type, nullable=True, ordinal=1, numeric_scale=scale
    )
    report = validate([{"v": text}], TableSchema(schema="public", name="t", columns=(column,)))
    return report.rows[0]["v"], [failure.rule for failure in report.failures]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 st", Decimal(2)),
        ("8 tim", Decimal(8)),
        ("3,5 Std", Decimal("3.5")),
        ("12 pcs", Decimal(12)),
        ("34 768,38 SEK", Decimal("34768.38")),
        ("USD 1,234.56", Decimal("1234.56")),
        ("EUR 24.031,71", Decimal("24031.71")),
        ("25 %", Decimal(25)),
        ("19%", Decimal(19)),
        ("(1 200,00) SEK", Decimal("-1200.00")),
    ],
)
def test_a_unit_or_currency_code_beside_the_number_is_ignored(text: str, expected: Decimal) -> None:
    value, failures = one("numeric", text, scale=2)

    assert failures == []
    assert value == expected


def test_an_integer_column_reads_past_the_unit_too() -> None:
    value, failures = one("int4", "31 st")

    assert failures == []
    assert value == 31


def test_letters_inside_the_digits_are_still_refused() -> None:
    value, failures = one("numeric", "12a34", scale=2)

    assert value is None
    assert failures == ["type"]


def test_a_word_alone_is_still_refused() -> None:
    value, failures = one("numeric", "pcs", scale=2)

    assert value is None
    assert failures == ["type"]
