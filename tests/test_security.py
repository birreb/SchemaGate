"""Who may spend the model budget, and how fast.

This service reads a database and pays a provider on every document. Before
this, anything that could reach the port could do both. Open by default is
still the shipped behaviour, because inventing a key an operator never sees is
worse, but an operator who sets one gets it enforced everywhere it matters.
"""

from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from schemagate.api.app import create_app
from schemagate.api.security import RateLimiter
from schemagate.config import Settings
from schemagate.errors import RateLimitedError
from schemagate.extract.base import Extracted, ModelT, Usage
from schemagate.ingest.images import NormalisedImage
from schemagate.schema.spec import ColumnSpec, TableRef, TableSchema

DSN = "postgresql://user:password@localhost:5432/billing"
KEY = "sk-test-first"
OTHER = "sk-test-second"

CSV = b"invoice_number\nINV-1\n"

INVOICES = TableSchema(
    schema="public",
    name="invoices",
    columns=(ColumnSpec(name="invoice_number", data_type="text", nullable=False, ordinal=1),),
)


class FakeSchemas:
    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema:
        return INVOICES

    async def tables(self, connection: str) -> tuple[TableRef, ...]:
        return (TableRef(schema="public", name="invoices", kind="table"),)


class StubExtractor:
    async def extract(
        self, document: str, model: type[ModelT], images: Sequence[NormalisedImage] = ()
    ) -> Extracted[ModelT]:
        return Extracted(value=model.model_validate({"rows": []}), usage=Usage(model="stub"))


def client(**overrides: Any) -> TestClient:
    settings = Settings(connections={"primary": DSN}, **overrides)
    app = create_app(settings=settings, schemas=FakeSchemas(), extractor=StubExtractor())
    return TestClient(app, raise_server_exceptions=False)


def upload() -> dict[str, Any]:
    return {
        "files": {"file": ("invoices.csv", CSV, "text/csv")},
        "data": {"connection": "primary", "table": "invoices"},
    }


# --- Keys --------------------------------------------------------------------


def test_no_keys_configured_leaves_the_endpoints_open() -> None:
    assert client().get("/v1/connections").status_code == 200


def test_a_configured_key_is_required() -> None:
    assert client(api_keys=[KEY]).get("/v1/connections").status_code == 401


def test_a_bearer_token_is_accepted() -> None:
    subject = client(api_keys=[KEY])

    response = subject.get("/v1/connections", headers={"Authorization": f"Bearer {KEY}"})

    assert response.status_code == 200


def test_an_api_key_header_is_accepted_too() -> None:
    """Bearer is what a generated client sends; this is what a person writes."""
    subject = client(api_keys=[KEY])

    response = subject.get("/v1/connections", headers={"X-API-Key": KEY})

    assert response.status_code == 200


def test_a_wrong_key_is_refused() -> None:
    subject = client(api_keys=[KEY])

    response = subject.get("/v1/connections", headers={"X-API-Key": "sk-not-this"})

    assert response.status_code == 401


def test_any_of_several_keys_works() -> None:
    """Rotation needs two valid keys at once, or every rotation is an outage."""
    subject = client(api_keys=[KEY, OTHER])

    assert subject.get("/v1/connections", headers={"X-API-Key": OTHER}).status_code == 200


def test_keys_may_be_given_as_a_comma_separated_string() -> None:
    settings = Settings(connections={"primary": DSN}, api_keys=f"{KEY}, {OTHER}")

    assert settings.accepts(OTHER)


def test_extraction_needs_the_key_too() -> None:
    """The endpoint that spends money is the one that most needs a key."""
    subject = client(api_keys=[KEY])

    assert subject.post("/v1/extract", **upload()).status_code == 401
    assert subject.post("/v1/extract", headers={"X-API-Key": KEY}, **upload()).status_code == 200


def test_listing_tables_needs_the_key() -> None:
    subject = client(api_keys=[KEY])

    assert subject.get("/v1/tables", params={"connection": "primary"}).status_code == 401


def test_health_stays_open() -> None:
    """A load balancer has no key, and a probe that needs one is a probe that fails."""
    assert client(api_keys=[KEY]).get("/health").status_code == 200


def test_the_refusal_says_how_to_authenticate_and_nothing_else() -> None:
    detail = client(api_keys=[KEY]).get("/v1/connections").json()["detail"]

    assert "Authorization: Bearer" in detail
    assert KEY not in detail, "an error body is read by whoever failed to authenticate"


# --- Rate limiting -----------------------------------------------------------


def test_no_limit_is_configured_by_default() -> None:
    assert not RateLimiter(0).enabled


def test_the_allowance_is_spent_then_refused() -> None:
    limiter = RateLimiter(2)

    limiter.check("someone", now=0.0)
    limiter.check("someone", now=0.0)

    with pytest.raises(RateLimitedError):
        limiter.check("someone", now=0.0)


def test_the_next_window_starts_again() -> None:
    limiter = RateLimiter(1)

    limiter.check("someone", now=0.0)
    limiter.check("someone", now=61.0)


def test_one_caller_running_out_does_not_stop_another() -> None:
    limiter = RateLimiter(1)

    limiter.check("first", now=0.0)
    limiter.check("second", now=0.0)


def test_the_table_of_callers_is_bounded() -> None:
    """A flood of one-request callers must not grow the table without bound."""
    from schemagate.api.security import MAX_TRACKED

    limiter = RateLimiter(100)
    for index in range(MAX_TRACKED + 50):
        limiter.check(f"caller-{index}", now=0.0)

    assert len(limiter._seen) <= MAX_TRACKED


def test_a_limited_request_is_refused_with_429_and_a_retry_after() -> None:
    subject = client(rate_limit_per_minute=1)

    subject.get("/v1/connections")
    response = subject.get("/v1/connections")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_the_limit_is_counted_per_key() -> None:
    subject = client(api_keys=[KEY, OTHER], rate_limit_per_minute=1)

    subject.get("/v1/connections", headers={"X-API-Key": KEY})
    spent = subject.get("/v1/connections", headers={"X-API-Key": KEY})
    fresh = subject.get("/v1/connections", headers={"X-API-Key": OTHER})

    assert spent.status_code == 429
    assert fresh.status_code == 200, "one caller's mistake is their own"


def test_health_is_not_rate_limited() -> None:
    subject = client(rate_limit_per_minute=1)

    subject.get("/health")
    assert subject.get("/health").status_code == 200
