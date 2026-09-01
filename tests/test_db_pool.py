import pytest

from schemagate.config import Settings
from schemagate.db.pool import PoolSchemas
from schemagate.errors import DatabaseUnavailableError

UNREACHABLE = "postgresql://someone:hunter2@127.0.0.1:1/nothing"


async def test_a_database_that_is_not_there_is_reported_clearly() -> None:
    schemas = PoolSchemas(Settings(connections={"primary": UNREACHABLE}))

    with pytest.raises(DatabaseUnavailableError) as caught:
        await schemas.fetch("primary", "public", "invoices")

    message = str(caught.value)
    assert "primary" in message, "the message should name the connection that failed"
    assert "hunter2" not in message, "and must not print the password while doing so"
    assert UNREACHABLE not in message


async def test_an_unknown_connection_still_reports_as_unknown() -> None:
    from schemagate.errors import UnknownConnectionError

    schemas = PoolSchemas(Settings(connections={"primary": UNREACHABLE}))

    with pytest.raises(UnknownConnectionError):
        await schemas.fetch("missing", "public", "invoices")
