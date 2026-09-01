import asyncpg

from schemagate.config import Settings
from schemagate.db.introspect import introspect, list_tables
from schemagate.errors import DatabaseUnavailableError
from schemagate.schema.spec import TableRef, TableSchema


class PoolSchemas:
    """Reads table definitions over pooled connections, one pool per name.

    Introspection is deliberately not cached. It is a single indexed catalog
    query, and running it every time is what makes an altered column take effect
    on the very next upload. The expensive part, compiling the model, is cached
    on what the query returned, so an unchanged table still costs nothing.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pools: dict[str, asyncpg.Pool[asyncpg.Record]] = {}

    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema:
        pool = await self._pool(connection)
        try:
            async with pool.acquire() as held:
                return await introspect(held, schema, table)
        except (OSError, asyncpg.PostgresError) as error:
            raise self._unreachable(connection, error) from error

    async def tables(self, connection: str) -> tuple[TableRef, ...]:
        pool = await self._pool(connection)
        try:
            async with pool.acquire() as held:
                return await list_tables(held)
        except (OSError, asyncpg.PostgresError) as error:
            raise self._unreachable(connection, error) from error

    async def _pool(self, connection: str) -> "asyncpg.Pool[asyncpg.Record]":
        if connection not in self._pools:
            # dsn() raises for a name that is not configured, which is what
            # keeps a caller from reaching a database nobody registered.
            dsn = self._settings.dsn(connection)
            try:
                self._pools[connection] = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
            except (OSError, asyncpg.PostgresError) as error:
                raise self._unreachable(connection, error) from error
        return self._pools[connection]

    def _unreachable(self, connection: str, error: Exception) -> DatabaseUnavailableError:
        """Name the connection, never the string behind it.

        The reason a database is unreachable is worth reporting. The credentials
        used to try are not, and an error body is one of the easiest places for
        them to escape.
        """
        return DatabaseUnavailableError(
            f"Cannot reach the database registered as {connection!r}: "
            f"{type(error).__name__}: {error}"
        )

    async def close(self) -> None:
        for pool in self._pools.values():
            await pool.close()
        self._pools.clear()
