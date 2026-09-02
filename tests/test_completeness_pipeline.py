"""The pipeline reports a document whose rows did not all come back."""

from typing import Any

from fpdf import FPDF

from schemagate.extract.base import Extracted, Usage
from schemagate.pipeline import process
from schemagate.schema.spec import ColumnSpec, TableSchema

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="invoice_number", data_type="varchar", nullable=False, ordinal=1),
        ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=2, numeric_scale=2),
    ),
)


def statement() -> bytes:
    document = FPDF()
    document.add_page()
    document.set_font("helvetica", size=11)
    for number, total in [
        ("F20260372", "100.00"),
        ("F20260374", "200.00"),
        ("F20260380", "300.00"),
    ]:
        document.cell(60, 8, number)
        document.cell(40, 8, total, new_x="LMARGIN", new_y="NEXT")
    return bytes(document.output())


class TwoOfThree:
    async def extract(self, document: str, model: Any, images: Any = ()) -> Any:
        rows = [
            {"invoice_number": "F20260372", "total": "100.00"},
            {"invoice_number": "F20260374", "total": "200.00"},
        ]
        return Extracted(value=model.model_validate({"rows": rows}), usage=Usage(model="stub"))


class AllThree:
    async def extract(self, document: str, model: Any, images: Any = ()) -> Any:
        rows = [
            {"invoice_number": "F20260372", "total": "100.00"},
            {"invoice_number": "F20260374", "total": "200.00"},
            {"invoice_number": "F20260380", "total": "300.00"},
        ]
        return Extracted(value=model.model_validate({"rows": rows}), usage=Usage(model="stub"))


async def test_a_dropped_row_is_reported_against_the_document() -> None:
    result = await process(statement(), "statement.pdf", INVOICES, extractor=TwoOfThree())

    assert result.status == "flagged"
    incomplete = [failure for failure in result.failures if failure.rule == "incomplete"]
    assert len(incomplete) == 1
    assert incomplete[0].row == -1, "a finding about the whole document carries no row"
    assert "F20260380" in incomplete[0].detail
    assert len(result.rows) == 2, "the rows that did come back are still returned"


async def test_a_complete_answer_is_clean() -> None:
    result = await process(statement(), "statement.pdf", INVOICES, extractor=AllThree())

    assert result.status == "ok"


async def test_the_check_stage_counts_it() -> None:
    result = await process(statement(), "statement.pdf", INVOICES, extractor=TwoOfThree())

    check = next(stage for stage in result.stages if stage.name == "check")
    assert "incomplete" in check.detail
