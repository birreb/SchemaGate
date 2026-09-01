"""What the response and the log say a document cost.

Reporting it is the whole point. A service that spends money per request and
returns only how long it took can answer every question except the one finance
asks, and the person who has to answer it is not the person who can read the
provider's dashboard.
"""

import logging
from collections.abc import Sequence
from typing import Any

from fastapi.testclient import TestClient
from fpdf import FPDF

from schemagate.api.app import create_app
from schemagate.config import Settings
from schemagate.extract.base import Extracted, ModelT, Usage
from schemagate.ingest.headers import forget_mappings
from schemagate.ingest.images import NormalisedImage
from schemagate.schema.spec import ColumnSpec, TableRef, TableSchema

DSN = "postgresql://user:password@localhost:5432/billing"

CSV = b"invoice_number,total\nINV-1,10.00\n"
SWEDISH = b"Fakturanr;Summa\nINV-1;10,00\n"

PRICES = {"opus": {"input": "5", "output": "25"}, "haiku": {"input": "1", "output": "5"}}

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="invoice_number", data_type="text", nullable=False, ordinal=1),
        ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=2, numeric_scale=2),
    ),
)


class FakeSchemas:
    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema:
        return INVOICES

    async def tables(self, connection: str) -> tuple[TableRef, ...]:
        return (TableRef(schema="public", name="invoices", kind="table"),)


class Spender:
    """Reports a fixed number of tokens, under a name a price can be attached to."""

    def __init__(self, model: str = "opus", answer: str = "INV-1") -> None:
        self.model = model
        self.answer = answer

    async def extract(
        self, document: str, model: type[ModelT], images: Sequence[NormalisedImage] = ()
    ) -> Extracted[ModelT]:
        payload: Any = (
            {"pairs": [{"header": "Fakturanr", "column": "invoice_number"}]}
            if "Match each column heading" in document
            else {"rows": [{"invoice_number": self.answer, "total": "10.00"}]}
        )
        return Extracted(
            value=model.model_validate(payload),
            usage=Usage(model=self.model, input_tokens=200_000, output_tokens=40_000),
        )


def client(extractor: Any = None, **overrides: Any) -> TestClient:
    settings = Settings(connections={"primary": DSN}, **overrides)
    app = create_app(settings=settings, schemas=FakeSchemas(), extractor=extractor or Spender())
    return TestClient(app, raise_server_exceptions=False)


def pdf() -> bytes:
    document = FPDF()
    document.add_page()
    document.set_font("helvetica", size=12)
    document.cell(0, 8, "Invoice INV-1 total 10.00")
    return bytes(document.output())


def extract(subject: TestClient, data: bytes = CSV, name: str = "invoices.csv") -> Any:
    return subject.post(
        "/v1/extract",
        files={"file": (name, data, "application/octet-stream")},
        data={"connection": "primary", "table": "invoices"},
    )


def test_a_free_route_reports_no_spend() -> None:
    """A CSV never reaches a provider, and the bill should say so rather than nothing."""
    body = extract(client()).json()

    assert body["usage"]["calls"] == 0
    assert body["usage"]["total_tokens"] == 0
    assert body["usage"]["cost_usd"] is None


def test_a_model_route_reports_its_tokens() -> None:
    body = extract(client(), pdf(), "invoice.pdf").json()

    assert body["usage"]["calls"] == 1
    assert body["usage"]["input_tokens"] == 200_000
    assert body["usage"]["output_tokens"] == 40_000


def test_cost_is_null_without_a_configured_price() -> None:
    body = extract(client(), pdf(), "invoice.pdf").json()

    assert body["usage"]["cost_usd"] is None, "a price nobody configured cannot be invented"


def test_cost_is_reported_when_the_model_is_priced() -> None:
    body = extract(client(prices=PRICES), pdf(), "invoice.pdf").json()

    assert body["usage"]["cost_usd"] == "2.000000", "200k in at $5 plus 40k out at $25"


def test_cost_is_a_string_like_every_other_exact_number() -> None:
    """A JSON number is a float in every client parser, and a bill is not a float."""
    body = extract(client(prices=PRICES), pdf(), "invoice.pdf").json()

    assert isinstance(body["usage"]["cost_usd"], str)


def test_the_breakdown_names_the_model_that_ran() -> None:
    body = extract(client(), pdf(), "invoice.pdf").json()

    assert body["usage"]["by_model"][0]["model"] == "opus"
    assert body["usage"]["by_model"][0]["calls"] == 1


def test_the_extract_stage_says_what_it_cost() -> None:
    body = extract(client(), pdf(), "invoice.pdf").json()

    detail = next(stage["detail"] for stage in body["stages"] if stage["name"] == "extract")
    assert "200000 tokens in" in detail
    assert "40000 out" in detail


def test_the_log_line_carries_the_spend(caplog: Any) -> None:
    subject = client(prices=PRICES)

    with caplog.at_level(logging.INFO, logger="schemagate"):
        extract(subject, pdf(), "invoice.pdf")

    recorded = next(record for record in caplog.records if record.name == "schemagate")
    assert recorded.input_tokens == 200_000
    assert recorded.output_tokens == 40_000
    assert recorded.cost_usd == "2.000000"


def test_the_heading_call_on_a_free_route_is_still_billed() -> None:
    """The call nobody expects: a CSV whose headings needed a model to understand."""
    forget_mappings()

    body = extract(client(), SWEDISH, "faktura.csv").json()

    assert body["usage"]["calls"] == 1, "the rows never left the machine, and the column names did"
    assert body["usage"]["by_model"][0]["model"] == "opus"


def test_the_match_stage_says_the_heading_call_cost_something() -> None:
    forget_mappings()

    body = extract(client(), SWEDISH, "faktura.csv").json()

    detail = next(stage["detail"] for stage in body["stages"] if stage["name"] == "match")
    assert "tokens in" in detail


def test_a_cheaper_model_can_be_configured_for_headings() -> None:
    """Matching two short lists of names is not work for the model that reads scans."""
    settings = Settings(connections={"primary": DSN}, header_model="haiku")
    app = create_app(settings=settings, schemas=FakeSchemas(), extractor=Spender("opus"))
    app.state.header_extractor = Spender("haiku")
    forget_mappings()

    body = extract(TestClient(app, raise_server_exceptions=False), SWEDISH, "faktura.csv").json()

    assert body["usage"]["by_model"][0]["model"] == "haiku"
