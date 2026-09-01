from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from schemagate import __version__
from schemagate.api.routes import SchemaSource, router
from schemagate.config import Settings
from schemagate.extract.base import Extractor


def create_app(
    settings: Settings | None = None,
    schemas: SchemaSource | None = None,
    extractor: Extractor | None = None,
) -> FastAPI:
    """Build the application.

    Dependencies are constructor arguments rather than module globals, so a test
    can supply a table definition without a database and a model without a
    server. Left out, the real ones are built at startup.
    """
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = None
        if app.state.schemas is None:
            from schemagate.db.pool import PoolSchemas

            owned = PoolSchemas(resolved)
            app.state.schemas = owned
        try:
            yield
        finally:
            if owned is not None:
                await owned.close()

    app = FastAPI(title="SchemaGate", version=__version__, lifespan=lifespan)
    app.state.settings = resolved
    app.state.schemas = schemas
    app.state.extractor = extractor
    app.include_router(router)
    return app
