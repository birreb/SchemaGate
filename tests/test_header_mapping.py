from collections.abc import Sequence
from typing import Any

import pytest

from schemagate.extract.base import Extracted, ModelT, Usage
from schemagate.ingest.headers import forget_mappings, map_headers
from schemagate.ingest.images import NormalisedImage
from schemagate.ingest.tabular import Table
from schemagate.schema.spec import ColumnSpec, TableSchema


@pytest.fixture(autouse=True)
def _fresh() -> None:
    """Each test starts without whatever an earlier one taught it."""
    forget_mappings()


INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="invoice_number", data_type="text", nullable=False, ordinal=1),
        ColumnSpec(name="supplier", data_type="text", nullable=False, ordinal=2),
        ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=3),
    ),
)

SWEDISH = Table(
    headers=("Fakturanr", "Leverantor", "Att betala"),
    rows=(("INV-3001", "Bauer GmbH", "1250,00"),),
)


class Mapper:
    """Answers with a fixed mapping, and records what it was shown."""

    def __init__(self, pairs: dict[str, str] | None = None) -> None:
        self.pairs = pairs or {
            "Fakturanr": "invoice_number",
            "Leverantor": "supplier",
            "Att betala": "total",
        }
        self.shown: list[str] = []

    async def extract(
        self, document: str, model: type[ModelT], images: Sequence[NormalisedImage] = ()
    ) -> Extracted[ModelT]:
        self.shown.append(document)
        return Extracted(
            value=model.model_validate(
                {"pairs": [{"header": h, "column": c} for h, c in self.pairs.items()]}
            ),
            usage=Usage(model="mapper", input_tokens=60, output_tokens=25),
        )


async def test_headers_in_another_language_are_mapped() -> None:
    mapper = Mapper()

    mapping = await map_headers(SWEDISH.headers, INVOICES, mapper)

    assert mapping.aliases == {
        "Fakturanr": "invoice_number",
        "Leverantor": "supplier",
        "Att betala": "total",
    }


async def test_the_values_are_never_shown_to_the_model() -> None:
    mapper = Mapper()

    await map_headers(SWEDISH.headers, INVOICES, mapper)

    shown = mapper.shown[0]
    assert "Fakturanr" in shown, "the header names are the whole question"
    assert "INV-3001" not in shown, (
        "mapping needs column names, not data; the rows stay on this machine"
    )
    assert "1250,00" not in shown


async def test_the_column_names_are_offered_so_none_can_be_invented() -> None:
    mapper = Mapper()

    await map_headers(SWEDISH.headers, INVOICES, mapper)

    shown = mapper.shown[0]
    for column in ("invoice_number", "supplier", "total"):
        assert column in shown


async def test_a_column_that_does_not_exist_cannot_be_named() -> None:
    """The same constraint as extraction: only real columns are expressible."""
    from pydantic import ValidationError

    mapper = Mapper({"Fakturanr": "not_a_column"})

    with pytest.raises(ValidationError):
        await map_headers(SWEDISH.headers, INVOICES, mapper)


async def test_a_heading_the_file_does_not_have_is_discarded() -> None:
    mapper = Mapper({"Fakturanr": "invoice_number", "Invented Heading": "supplier"})

    mapping = await map_headers(SWEDISH.headers, INVOICES, mapper)

    assert mapping.aliases == {"Fakturanr": "invoice_number"}, (
        "a mapping from a heading that is not in the file cannot be acted on"
    )


async def test_two_headers_cannot_claim_the_same_column() -> None:
    mapper = Mapper({"Fakturanr": "invoice_number", "Leverantor": "invoice_number"})

    mapping = await map_headers(SWEDISH.headers, INVOICES, mapper)

    assert list(mapping.aliases.values()) == ["invoice_number"], (
        "the second claim is ambiguous, and guessing which wins would be worse "
        "than leaving it unmapped"
    )


async def test_asking_twice_for_the_same_file_shape_only_asks_once() -> None:
    mapper = Mapper()

    await map_headers(SWEDISH.headers, INVOICES, mapper)
    await map_headers(SWEDISH.headers, INVOICES, mapper)

    assert len(mapper.shown) == 1, (
        "supplier files repeat, and the same headers against the same table "
        "always mean the same thing"
    )


async def test_nothing_is_asked_when_there_is_no_model() -> None:
    assert (await map_headers(SWEDISH.headers, INVOICES, None)).aliases == {}


async def test_the_mapping_call_reports_what_it_cost() -> None:
    """The call nobody expects is the one worth reporting."""
    mapper = Mapper()

    mapping = await map_headers(SWEDISH.headers, INVOICES, mapper)

    assert [usage.model for usage in mapping.usage] == ["mapper"]
    assert mapping.usage[0].input_tokens == 60


async def test_a_remembered_mapping_costs_nothing() -> None:
    mapper = Mapper()

    await map_headers(SWEDISH.headers, INVOICES, mapper)
    again = await map_headers(SWEDISH.headers, INVOICES, mapper)

    assert again.aliases, "the answer is still there"
    assert again.usage == (), (
        "billing a cached answer for the call that filled the cache would "
        "report the same tokens twice"
    )


async def test_the_remembered_answers_are_bounded() -> None:
    """A service reads whatever people upload, so distinct headings have no ceiling."""
    from schemagate.ingest.headers import MAX_REMEMBERED, _answers

    mapper = Mapper()
    for index in range(MAX_REMEMBERED + 20):
        await map_headers((f"Kolumn{index}", "Leverantor"), INVOICES, mapper)

    assert len(_answers) <= MAX_REMEMBERED


async def test_the_pipeline_maps_headings_it_could_not_match() -> None:
    """End to end: a Swedish file against an English table."""
    from schemagate.ingest.headers import forget_mappings
    from schemagate.pipeline import Route, process

    forget_mappings()
    mapper = Mapper()
    csv = b"Fakturanr;Leverantor;Att betala\nINV-3001;Bauer GmbH;1250,00\n"

    result: Any = await process(csv, "faktura.csv", INVOICES, extractor=mapper)

    assert result.route is Route.TABULAR, "a data grid is still a data grid"
    assert result.rows[0]["invoice_number"] == "INV-3001"
    assert result.rows[0]["supplier"] == "Bauer GmbH"
    assert result.unmatched_headers == ()


async def test_the_rows_are_never_sent_even_when_a_model_is_asked() -> None:
    from schemagate.ingest.headers import forget_mappings
    from schemagate.pipeline import process

    forget_mappings()
    mapper = Mapper()
    csv = b"Fakturanr;Leverantor;Att betala\nINV-3001;Bauer GmbH;1250,00\n"

    await process(csv, "faktura.csv", INVOICES, extractor=mapper)

    everything = "\n".join(mapper.shown)
    assert "INV-3001" not in everything
    assert "Bauer GmbH" not in everything
    assert "1250,00" not in everything, (
        "the point of the tabular path is that the document never reaches a "
        "provider; only the column headings do"
    )


async def test_a_file_that_matches_by_spelling_never_asks() -> None:
    from schemagate.ingest.headers import forget_mappings
    from schemagate.pipeline import process

    forget_mappings()
    mapper = Mapper()
    csv = b"invoice_number,supplier,total\nINV-1,Acme,10.00\n"

    await process(csv, "invoices.csv", INVOICES, extractor=mapper)

    assert mapper.shown == [], "plain matching worked, so there was nothing to ask"
