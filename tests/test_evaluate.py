"""The measuring harness itself, measured.

Every number this prints is an argument for or against a model, so the scoring
has to be right before the scores mean anything. Run here against a stub, which
is the only way to assert what a wrong answer scores without paying a provider
to give one.
"""

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from schemagate.errors import ConfigurationError
from schemagate.evaluate import Case, evaluate, load_cases, report, run_case
from schemagate.extract.base import Extracted, ModelT, Usage
from schemagate.extract.cost import Price
from schemagate.ingest.images import NormalisedImage
from schemagate.schema.spec import ColumnSpec, TableSchema

CASES = Path("evals/cases")

PDF_ANSWER = {
    "invoice_number": "INV-2026-0147",
    "supplier": "Northgate Supply Co.",
    "vat_id": "SE556000000001",
    "status": "sent",
    "subtotal": "9140.19",
    "tax": "2285.05",
    "total": "11425.24",
    "issued_on": "2026-09-01",
}


class Answering:
    """Returns a fixed row, so a known score can be asserted."""

    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row if row is not None else dict(PDF_ANSWER)

    async def extract(
        self, document: str, model: type[ModelT], images: Sequence[NormalisedImage] = ()
    ) -> Extracted[ModelT]:
        return Extracted(
            value=model.model_validate({"rows": [self.row]}),
            usage=Usage(model="stub", input_tokens=3000, output_tokens=200),
        )


def one_case() -> Case:
    return next(case for case in load_cases(CASES) if case.name == "invoice-pdf")


# --- Loading -----------------------------------------------------------------


def test_the_shipped_cases_load() -> None:
    cases = load_cases(CASES)

    assert {case.name for case in cases} == {
        "invoices-csv",
        "invoices-european",
        "invoice-pdf",
    }


def test_every_case_points_at_a_document_that_exists() -> None:
    for case in load_cases(CASES):
        assert case.document.exists(), case.document


def test_every_case_says_why_it_is_here() -> None:
    """A fixture nobody can explain is a fixture nobody will maintain."""
    for case in load_cases(CASES):
        assert case.why, case.name


def test_an_empty_directory_is_a_configuration_problem(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_cases(tmp_path)


# --- Scoring -----------------------------------------------------------------


async def test_a_correct_reading_scores_every_cell() -> None:
    result = await run_case(one_case(), Answering())

    assert result.cells_correct == result.cells_expected
    assert result.accuracy == 1.0
    assert result.ok


async def test_one_wrong_cell_costs_one_cell_and_not_the_row() -> None:
    """A misread date and an invented row are not the same failure."""
    wrong = dict(PDF_ANSWER, issued_on="2026-08-31")

    result = await run_case(one_case(), Answering(wrong))

    assert result.cells_correct == result.cells_expected - 1
    assert not result.ok


async def test_what_went_wrong_is_named() -> None:
    result = await run_case(one_case(), Answering(dict(PDF_ANSWER, total="99.00")))

    assert any("total" in detail and "11425.24" in detail for detail in result.wrong)


async def test_a_coerced_value_compares_equal_to_the_string_it_came_from() -> None:
    """The pipeline returns Decimal and date; a case file holds strings."""
    result = await run_case(one_case(), Answering())

    assert result.cells_correct > 0, "otherwise every numeric cell would score as a miss"


async def test_a_model_that_refuses_is_recorded_not_raised() -> None:
    """One provider failing one document must not discard the rest of the run."""
    from schemagate.errors import ExtractionError

    class Refusing:
        async def extract(self, document: str, model: Any, images: Any = ()) -> Any:
            raise ExtractionError("no")

    result = await run_case(one_case(), Refusing())

    assert result.error.startswith("ExtractionError")
    assert not result.ok


async def test_a_missing_document_is_reported_rather_than_crashing(tmp_path: Path) -> None:
    case = Case(
        name="gone",
        document=tmp_path / "nothing.pdf",
        table=TableSchema(
            schema="public",
            name="t",
            columns=(ColumnSpec(name="a", data_type="text", nullable=True, ordinal=1),),
        ),
        expected=({"a": "x"},),
    )

    assert "Missing document" in (await run_case(case, Answering())).error


# --- Flags -------------------------------------------------------------------


async def test_an_expected_flag_is_not_scored_as_a_failure() -> None:
    """The European fixture has a total that does not add up, on purpose."""
    european = next(case for case in load_cases(CASES) if case.name == "invoices-european")

    result = await run_case(european, None)

    assert result.flags == 1
    assert result.ok, "the gate catching a deliberate error is the case passing, not failing"


async def test_the_free_route_really_is_free() -> None:
    """The control case. Any spend reported here is a bug, not a price."""
    csv = next(case for case in load_cases(CASES) if case.name == "invoices-csv")

    result = await run_case(csv, None)

    assert result.spend.calls == 0
    assert result.spend.total_tokens == 0
    assert result.ok


# --- Reporting ---------------------------------------------------------------


async def test_the_report_puts_accuracy_beside_cost() -> None:
    """Either number alone picks the wrong model."""
    results = await evaluate(
        load_cases(CASES), Answering(), {"stub": Price(Decimal(5), Decimal(25))}
    )

    rendered = report(results)
    assert "cells" in rendered
    assert "cost" in rendered
    assert "$" in rendered
    assert "cells (" in rendered


async def test_the_report_names_a_case_that_failed() -> None:
    results = await evaluate(load_cases(CASES), Answering(dict(PDF_ANSWER, invoice_number="WRONG")))

    assert "invoice-pdf" in report(results)
    assert "wanted" in report(results)
