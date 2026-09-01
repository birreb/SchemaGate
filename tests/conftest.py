import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

TEST_DSN_VARIABLE = "SCHEMAGATE_TEST_DSN"


@pytest.fixture
async def connection() -> AsyncIterator[asyncpg.Connection[asyncpg.Record]]:
    """A live connection, or a skip when no test database is configured.

    CI provides one through a service container. Locally the postgres-marked
    tests skip rather than fail, so the suite stays runnable without Docker.
    """
    dsn = os.environ.get(TEST_DSN_VARIABLE)
    if not dsn:
        pytest.skip(f"{TEST_DSN_VARIABLE} is not set")

    connection = await asyncpg.connect(dsn)
    try:
        yield connection
    finally:
        await connection.close()
