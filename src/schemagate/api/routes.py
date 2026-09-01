import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Protocol

import anyio
import anyio.to_thread
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse

from schemagate.api.logging import note
from schemagate.api.security import authorise, presented
from schemagate.api.serialize import to_json_row
from schemagate.config import Settings
from schemagate.errors import (
    ConfigurationError,
    DatabaseUnavailableError,
    ExtractionError,
    ExtractorNotConfiguredError,
    MalformedDocumentError,
    MissingDependencyError,
    NotAuthorisedError,
    RateLimitedError,
    TableNotFoundError,
    UnknownConnectionError,
    UnsupportedColumnTypeError,
    UnsupportedFileTypeError,
)
from schemagate.extract.cost import Spend
from schemagate.pipeline import process
from schemagate.schema.spec import TableRef, TableSchema

router = APIRouter()

# Read once at import. The page is a fixed asset, and reading it from disk on
# every request would be work done for no reason.
STATIC = Path(__file__).parent / "static"
PLAYGROUND = (STATIC / "index.html").read_text(encoding="utf-8")

REQUEST_CREDENTIALS_OFF = (
    "Choosing a provider per request is off. It sends a credential over HTTP, "
    "so it has to be enabled deliberately with "
    "SCHEMAGATE_ALLOW_REQUEST_CREDENTIALS=true."
)

ANONYMOUS = "anonymous"


class SchemaSource(Protocol):
    """Where table definitions come from."""

    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema: ...

    async def tables(self, connection: str) -> tuple[TableRef, ...]: ...


async def caller(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> str:
    """Authorise the request and name who made it.

    Both together, since the key that authorises is also what the rate limit is
    counted against. With no keys configured, the client address is used.
    """
    settings: Settings = request.app.state.settings
    key = presented(authorization, x_api_key)

    try:
        authorise(settings.accepts(key))
    except NotAuthorisedError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error

    who = key or (request.client.host if request.client else ANONYMOUS)
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is not None:
        try:
            limiter.check(who)
        except RateLimitedError as error:
            raise HTTPException(
                status_code=429, detail=str(error), headers={"retry-after": "60"}
            ) from error
    return who


def _schemas(request: Request) -> SchemaSource:
    """The table definition source, built on first use if nobody supplied one.

    Built here rather than at startup so that including this router in an
    application that never wires a lifespan still works. That is the ordinary
    case when someone embeds this rather than running it.
    """
    existing: SchemaSource | None = getattr(request.app.state, "schemas", None)
    if existing is not None:
        return existing

    from schemagate.db.pool import PoolSchemas

    built = PoolSchemas(request.app.state.settings)
    request.app.state.schemas = built
    request.app.state.owns_schemas = True
    return built


@asynccontextmanager
async def _in_flight(request: Request) -> AsyncIterator[None]:
    """Hold one of the slots for work that is actually expensive.

    Applied to extraction alone. Listing tables is a catalog query and does not
    need to queue behind a scanned PDF.

    The semaphore is built here, on the first request, because it belongs to a
    running event loop and the application is built before there is one.
    """
    settings: Settings = request.app.state.settings
    if settings.max_concurrent_extractions <= 0:
        yield
        return

    gate = getattr(request.app.state, "gate", None)
    if gate is None:
        gate = anyio.Semaphore(settings.max_concurrent_extractions)
        request.app.state.gate = gate
        # The default thread pool is forty workers, which is a sensible number
        # for waiting on files and a poor one for OCR: those threads are CPU
        # bound, and forty of them on eight cores finish every request more
        # slowly than eight would finish the first eight. Matched to the work
        # actually allowed in, and never lowered below what anyio chose.
        threads = anyio.to_thread.current_default_thread_limiter()
        threads.total_tokens = max(threads.total_tokens, settings.max_concurrent_extractions)

    async with gate:
        yield


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def playground() -> str:
    """A single page for trying the endpoint by hand.

    Not a dashboard and not the product. It exists so that someone evaluating
    the API can see it work in the first minute, and it calls the same public
    endpoint anyone else would. Everything is inline: a page that fetched a font
    or a script from the internet would not load inside a private network.
    """
    return PLAYGROUND


@router.get("/v1/connections")
async def connections(
    request: Request, who: Annotated[str, Depends(caller)]
) -> dict[str, list[str]]:
    """The names a caller may use, and only the names.

    What each one points at stays on the server. Knowing a connection is called
    `primary` tells you nothing about where it goes or how to reach it.
    """
    settings: Settings = request.app.state.settings
    return {"connections": sorted(settings.connections)}


@router.get("/v1/tables")
async def tables(
    request: Request,
    connection: Annotated[str, Query()],
    who: Annotated[str, Depends(caller)],
) -> dict[str, Any]:
    """Every relation the connected role can read.

    Offered so a caller can choose rather than type a name and find out later
    whether it exists.
    """
    settings: Settings = request.app.state.settings

    try:
        settings.dsn(connection)
        found = await _schemas(request).tables(connection)
    except UnknownConnectionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except MissingDependencyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except DatabaseUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return {"tables": [{"schema": ref.schema, "name": ref.name, "kind": ref.kind} for ref in found]}


@router.post("/v1/models")
async def models(
    request: Request,
    provider: Annotated[str, Form()],
    who: Annotated[str, Depends(caller)],
    api_key: Annotated[str | None, Form()] = None,
    base_url: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Which models this provider will accept.

    Asked of the provider rather than kept as a list here, so it cannot go
    stale, it reflects what this key is entitled to, and for a local runtime it
    shows what has actually been pulled.
    """
    settings: Settings = request.app.state.settings

    if api_key and not settings.allow_request_credentials:
        raise HTTPException(status_code=403, detail=REQUEST_CREDENTIALS_OFF)

    from schemagate.api.app import make_model_client
    from schemagate.extract.catalog import list_models

    try:
        client = make_model_client(
            provider=provider,
            api_key=api_key or None,
            base_url=base_url or None,
            ollama_host=settings.ollama_host,
        )
        listing = await list_models(provider, client)
    except MissingDependencyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (ConfigurationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {
        "models": list(listing.models),
        "source": listing.source,
        "detail": listing.detail,
    }


@router.get("/icon.png", include_in_schema=False)
async def icon() -> FileResponse:
    """The tab icon, served from the package rather than fetched anywhere."""
    return FileResponse(STATIC / "icon.png", media_type="image/png")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/v1/extract")
async def extract(
    request: Request,
    file: Annotated[UploadFile, File()],
    connection: Annotated[str, Form()],
    table: Annotated[str, Form()],
    who: Annotated[str, Depends(caller)],
    # `schema` is the right word on the wire but shadows a Pydantic attribute,
    # so the field keeps its name and the parameter takes another.
    namespace: Annotated[str, Form(alias="schema")] = "public",
    # Optional, and only honoured when the operator has allowed it. Present so
    # the playground can try a provider without a configuration change.
    provider: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
    api_key: Annotated[str | None, Form()] = None,
    base_url: Annotated[str | None, Form()] = None,
    # Anything the schema cannot say. Overrides what configuration holds for
    # this table, so the playground can try wording before committing to it.
    instructions: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings

    data = await _read_upload(file, settings.max_upload_bytes)
    extractor = _choose_extractor(request, settings, provider, model, api_key, base_url)

    try:
        # Resolving the connection by name is also what proves the caller is
        # allowed to use it. A connection string never crosses the wire.
        settings.dsn(connection)
        schemas = _schemas(request)
        discovered = time.perf_counter()
        definition = await schemas.fetch(connection, namespace, table)
        discovery_ms = int((time.perf_counter() - discovered) * 1000)
        async with _in_flight(request):
            result = await process(
                data,
                file.filename,
                definition,
                extractor=extractor,
                rules=settings.rules_for(definition.qualified_name),
                instructions=(
                    instructions.strip()
                    if instructions and instructions.strip()
                    else settings.instructions_for(definition.qualified_name)
                ),
                header_extractor=(
                    getattr(request.app.state, "header_extractor", None)
                    if provider is None
                    else None
                ),
                prices=settings.prices,
            )
    except UnknownConnectionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ConfigurationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except TableNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except UnsupportedFileTypeError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except (MalformedDocumentError, UnsupportedColumnTypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (
        DatabaseUnavailableError,
        ExtractorNotConfiguredError,
        MissingDependencyError,
    ) as error:
        # Neither the caller's mistake nor a defect in us: something the
        # deployment depends on is missing or down.
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ExtractionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    note(
        request,
        table=result.table,
        route=result.route.value,
        rows=len(result.rows),
        failures=len(result.failures),
        outcome=result.status,
        # What it cost, on the line an operator already reads.
        calls=result.spend.calls,
        input_tokens=result.spend.input_tokens + result.spend.cached_input_tokens,
        output_tokens=result.spend.output_tokens,
        cost_usd=None if result.spend.cost_usd is None else str(result.spend.cost_usd),
    )

    return {
        "status": result.status,
        "table": result.table,
        "route": result.route.value,
        "rows": [to_json_row(row) for row in result.rows],
        "validation": {
            "failures": [
                {
                    "row": failure.row,
                    "column": failure.column,
                    "rule": failure.rule,
                    "detail": failure.detail,
                    "value": failure.value,
                }
                for failure in result.failures
            ]
        },
        "stages": [
            {
                "name": "schema",
                "detail": (
                    f"Read {definition.qualified_name} from the database: "
                    f"{len(definition.columns)} columns, "
                    f"{len(definition.extractable)} to extract"
                ),
                "ms": discovery_ms,
            },
            *[
                {"name": stage.name, "detail": stage.detail, "ms": stage.ms}
                for stage in result.stages
            ],
        ],
        "usage": _usage_body(result.spend),
        "unmatched_headers": list(result.unmatched_headers),
        "missing_columns": list(result.missing_columns),
        "timings_ms": result.timings_ms,
    }


def _usage_body(spend: Spend) -> dict[str, Any]:
    """What the document cost, per model and in total.

    Money is a string for the same reason every other exact number here is: a
    JSON number is a float in every client parser. Null means at least one
    model that ran has no configured price.
    """
    return {
        "calls": spend.calls,
        "input_tokens": spend.input_tokens,
        "cached_input_tokens": spend.cached_input_tokens,
        "output_tokens": spend.output_tokens,
        "total_tokens": spend.total_tokens,
        "cost_usd": None if spend.cost_usd is None else str(spend.cost_usd),
        "by_model": [
            {
                "model": usage.model,
                "calls": usage.calls,
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
            }
            for usage in spend.by_model
        ],
    }


def _choose_extractor(
    request: Request,
    settings: Settings,
    provider: str | None,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
) -> Any:
    """Use the request's provider when one is given, otherwise the configured one.

    A credential arriving in a request body is refused unless the operator has
    turned it on. That is deliberately the operator's decision rather than the
    caller's, and the key is used to build a client and then dropped: it is
    never stored, logged, or repeated in a response.
    """
    if provider is None:
        return request.app.state.extractor

    if not settings.allow_request_credentials:
        raise HTTPException(status_code=403, detail=REQUEST_CREDENTIALS_OFF)

    from schemagate.api.app import make_extractor

    try:
        return make_extractor(
            provider=provider,
            model=model or None,
            api_key=api_key or None,
            base_url=base_url or None,
            ollama_host=settings.ollama_host,
            effort=settings.effort,
        )
    except MissingDependencyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ConfigurationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    """Read the body, refusing anything past the limit.

    One byte past the limit is enough to know, so the whole of an oversized
    upload is never held in memory.
    """
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"The upload is larger than the configured limit of {limit} bytes.",
        )
    return data
