"""The word `null` is not a name.

A model asked for a value it cannot find sometimes writes the word instead of
the JSON null, and a text column would store it. Found by the ingestion
benchmark: a supplier stored as the string `null`. The gate reads the usual
spellings of nothing as nothing, and a NOT NULL column then reports it.
"""

import pytest

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.gate import validate


def table(nullable: bool) -> TableSchema:
    return TableSchema(
        schema="public",
        name="t",
        columns=(ColumnSpec(name="supplier", data_type="text", nullable=nullable, ordinal=1),),
    )


@pytest.mark.parametrize("word", ["null", "NULL", "None", "n/a", "N/A", "nan", ""])
def test_a_spelling_of_nothing_is_read_as_nothing(word: str) -> None:
    report = validate([{"supplier": word}], table(nullable=True), [])

    assert report.rows[0]["supplier"] is None
    assert report.failures == ()


def test_a_spelling_of_nothing_in_a_required_column_is_reported() -> None:
    report = validate([{"supplier": "null"}], table(nullable=False), [])

    assert report.rows[0]["supplier"] is None
    assert [f.rule for f in report.failures] == ["not_null"]


def test_a_real_name_is_kept() -> None:
    report = validate([{"supplier": "Nullmeyer GmbH"}], table(nullable=False), [])

    assert report.rows[0]["supplier"] == "Nullmeyer GmbH"
