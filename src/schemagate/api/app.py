from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from schemagate import __version__
from schemagate.api.logging import record_requests
from schemagate.api.routes import SchemaSource, router
from schemagate.api.security import RateLimiter
from schemagate.config import Settings
from schemagate.extract.base import Extractor
from schemagate.extract.factory import build_extractor, make_extractor, make_model_client


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
        try:
            yield
        finally:
            await shutdown(app)

    app = FastAPI(title="SchemaGate", version=__version__, lifespan=lifespan)
    app.middleware("http")(record_requests)
    install(app, settings=resolved, schemas=schemas, extractor=extractor)
    return app


def install(
    app: FastAPI,
    settings: Settings | None = None,
    schemas: SchemaSource | None = None,
    extractor: Extractor | None = None,
) -> None:
    """Add SchemaGate's endpoints to an application you already have.

    Use this rather than `app.mount`. Starlette does not run a mounted
    application's lifespan, so a mounted copy never builds its connection pool
    and every request fails on a state attribute that is still None. This
    includes the router and sets the state directly, and the pool is built on
    first use, so a host that never wires a lifespan still works.

    Call `shutdown(app)` from your own lifespan to close the pools. Skipping it
    leaks connections at exit and nothing else.
    """
    resolved = settings or Settings()
    app.state.settings = resolved
    app.state.schemas = schemas
    app.state.extractor = extractor
    app.state.header_extractor = None
    app.state.limiter = RateLimiter(resolved.rate_limit_per_minute)
    # Bounds the work in flight rather than the arrival rate. Each document
    # holds its upload, its rendered pages and its answer in memory at once.
    # Built on the first request rather than here: an anyio semaphore and the
    # thread limiter both belong to a running event loop, and this function is
    # called while building the application, before there is one.
    app.state.gate = None
    app.state.owns_schemas = schemas is None

    if extractor is None and resolved.provider is not None:
        app.state.extractor = build_extractor(resolved)
    if resolved.header_model and app.state.extractor is not None:
        app.state.header_extractor = build_extractor(resolved, model=resolved.header_model)

    app.include_router(router)


async def shutdown(app: FastAPI) -> None:
    """Close anything this application built for itself.

    A pool handed in by the caller is left alone, since its lifetime belongs to
    whoever created it.
    """
    schemas = getattr(app.state, "schemas", None)
    if schemas is not None and getattr(app.state, "owns_schemas", False):
        closer = getattr(schemas, "close", None)
        if closer is not None:
            await closer()
        app.state.schemas = None


# Re-exported: these used to live here, and `from schemagate.api.app import
# make_extractor` is what the request path and anyone reading the old module
# still writes. Building a model client is not an HTTP concern, so the code
# moved to `schemagate.extract.factory`, where it can be used without FastAPI.
__all__ = [
    "build_extractor",
    "create_app",
    "install",
    "make_extractor",
    "make_model_client",
    "shutdown",
]
