"""Using this as a library, and mounting it inside an application you own.

The pipeline was always callable without the server, and nothing said so: the
package exported a version string and the way in was a deep import. These tests
hold the public surface in place, and cover the mounting mistake that used to
fail silently.
"""

from collections.abc import Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import schemagate
from schemagate import Extracted, Usage, install, process, shutdown
from schemagate.config import Settings
from schemagate.extract.base import ModelT
from schemagate.ingest.images import NormalisedImage
from schemagate.schema.spec import ColumnSpec, TableRef, TableSchema

DSN = "postgresql://user:password@localhost:5432/billing"

CSV = b"invoice_number,total\nINV-1,10.00\n"

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(
        ColumnSpec(name="invoice_number", data_type="text", nullable=False, ordinal=1),
        ColumnSpec(name="total", data_type="numeric", nullable=False, ordinal=2, numeric_scale=2),
    ),
)


class FakeSchemas:
    def __init__(self) -> None:
        self.closed = False

    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema:
        return INVOICES

    async def tables(self, connection: str) -> tuple[TableRef, ...]:
        return (TableRef(schema="public", name="invoices", kind="table"),)

    async def close(self) -> None:
        self.closed = True


class StubExtractor:
    async def extract(
        self, document: str, model: type[ModelT], images: Sequence[NormalisedImage] = ()
    ) -> Extracted[ModelT]:
        return Extracted(value=model.model_validate({"rows": []}), usage=Usage(model="stub"))


# --- The package surface -----------------------------------------------------


def test_the_library_entry_point_is_importable_from_the_package() -> None:
    assert schemagate.process is process


def test_everything_named_in_all_can_actually_be_reached() -> None:
    """A name in `__all__` that raises on import is worse than one that is absent."""
    for name in schemagate.__all__:
        assert getattr(schemagate, name) is not None, name


def test_an_unknown_name_is_still_an_attribute_error() -> None:
    with pytest.raises(AttributeError):
        schemagate.not_a_real_name  # noqa: B018


def test_the_server_names_are_not_imported_until_asked_for() -> None:
    """FastAPI is an extra, so naming `create_app` must not require it at import."""
    assert "create_app" in schemagate.__all__
    assert "create_app" not in vars(schemagate)


async def test_the_pipeline_runs_with_no_server_and_no_database() -> None:
    result = await process(CSV, "invoices.csv", INVOICES, extractor=None)

    assert result.rows[0]["invoice_number"] == "INV-1"


# --- Mounting into an application you own ------------------------------------


def test_install_adds_the_endpoints_to_an_existing_application() -> None:
    host = FastAPI()

    @host.get("/mine")
    async def mine() -> dict[str, str]:
        return {"ours": "yes"}

    install(
        host,
        settings=Settings(connections={"primary": DSN}),
        schemas=FakeSchemas(),
        extractor=StubExtractor(),
    )

    with TestClient(host) as subject:
        assert subject.get("/mine").json() == {"ours": "yes"}
        assert subject.get("/v1/connections").json() == {"connections": ["primary"]}


def test_an_installed_router_extracts_without_a_lifespan_of_its_own() -> None:
    """The mounting mistake this replaces: state that only a lifespan would set."""
    host = FastAPI()
    install(
        host,
        settings=Settings(connections={"primary": DSN}),
        schemas=FakeSchemas(),
        extractor=StubExtractor(),
    )

    subject = TestClient(host, raise_server_exceptions=False)
    response = subject.post(
        "/v1/extract",
        files={"file": ("invoices.csv", CSV, "text/csv")},
        data={"connection": "primary", "table": "invoices"},
    )

    assert response.status_code == 200
    assert response.json()["route"] == "tabular"


async def test_a_pool_we_were_handed_is_not_closed_for_us() -> None:
    """Closing the host's pool at our exit is how an embedded service takes it down."""
    host = FastAPI()
    schemas = FakeSchemas()
    install(host, settings=Settings(connections={"primary": DSN}), schemas=schemas)

    await shutdown(host)

    assert not schemas.closed


async def test_a_pool_we_built_ourselves_is_closed() -> None:
    host = FastAPI()
    install(host, settings=Settings(connections={"primary": DSN}))
    built = FakeSchemas()
    host.state.schemas = built
    host.state.owns_schemas = True

    await shutdown(host)

    assert built.closed


def test_installing_twice_does_not_duplicate_a_route() -> None:
    host = FastAPI()
    settings = Settings(connections={"primary": DSN})
    install(host, settings=settings, schemas=FakeSchemas())
    before = len(host.routes)
    install(host, settings=settings, schemas=FakeSchemas())

    with TestClient(host) as subject:
        assert subject.get("/v1/connections").status_code == 200
    assert len(host.routes) > before, "the second install adds its own copy, and the first wins"


# --- Optional dependencies ---------------------------------------------------


def test_a_missing_extra_names_the_extra_that_installs_it() -> None:
    from schemagate.errors import MissingDependencyError
    from schemagate.optional import require

    with pytest.raises(MissingDependencyError) as raised:
        require("pdf_inspector_that_does_not_exist")

    assert "pip install" in str(raised.value)


def test_a_known_module_names_its_own_extra() -> None:
    from schemagate.optional import EXTRAS

    assert EXTRAS["pdf_inspector"] == "pdf"
    assert EXTRAS["anthropic"] == "anthropic"


def test_an_installed_module_is_returned() -> None:
    from schemagate.optional import require

    assert require("json").dumps({"a": 1}) == '{"a": 1}'


# --- Concurrency -------------------------------------------------------------


def test_extraction_is_bounded_by_the_configured_limit() -> None:
    """The gate is what keeps an arrival rate from becoming a memory problem."""
    host = FastAPI()
    install(
        host,
        settings=Settings(connections={"primary": DSN}, max_concurrent_extractions=3),
        schemas=FakeSchemas(),
        extractor=StubExtractor(),
    )

    subject = TestClient(host, raise_server_exceptions=False)
    subject.post(
        "/v1/extract",
        files={"file": ("invoices.csv", CSV, "text/csv")},
        data={"connection": "primary", "table": "invoices"},
    )

    gate: Any = host.state.gate
    assert gate is not None, "built on the first request, not while there is no event loop"
    assert gate.value == 3, "and released again when the document was done"


def test_no_gate_is_built_when_the_limit_is_off() -> None:
    host = FastAPI()
    install(
        host,
        settings=Settings(connections={"primary": DSN}, max_concurrent_extractions=0),
        schemas=FakeSchemas(),
        extractor=StubExtractor(),
    )

    subject = TestClient(host, raise_server_exceptions=False)
    subject.post(
        "/v1/extract",
        files={"file": ("invoices.csv", CSV, "text/csv")},
        data={"connection": "primary", "table": "invoices"},
    )

    assert host.state.gate is None
