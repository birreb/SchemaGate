import time
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from schemagate.api.serialize import to_json_row
from schemagate.config import Settings
from schemagate.errors import (
    ConfigurationError,
    DatabaseUnavailableError,
    ExtractionError,
    ExtractorNotConfiguredError,
    MalformedDocumentError,
    TableNotFoundError,
    UnknownConnectionError,
    UnsupportedColumnTypeError,
    UnsupportedFileTypeError,
)
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


class SchemaSource(Protocol):
    """Where table definitions come from."""

    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema: ...

    async def tables(self, connection: str) -> tuple[TableRef, ...]: ...


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
async def connections(request: Request) -> dict[str, list[str]]:
    """The names a caller may use, and only the names.

    What each one points at stays on the server. Knowing a connection is called
    `primary` tells you nothing about where it goes or how to reach it.
    """
    settings: Settings = request.app.state.settings
    return {"connections": sorted(settings.connections)}


@router.get("/v1/tables")
async def tables(request: Request, connection: Annotated[str, Query()]) -> dict[str, Any]:
    """Every relation the connected role can read.

    Offered so a caller can choose rather than type a name and find out later
    whether it exists.
    """
    settings: Settings = request.app.state.settings
    schemas: SchemaSource = request.app.state.schemas

    try:
        settings.dsn(connection)
        found = await schemas.tables(connection)
    except UnknownConnectionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except DatabaseUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return {"tables": [{"schema": ref.schema, "name": ref.name, "kind": ref.kind} for ref in found]}


@router.post("/v1/models")
async def models(
    request: Request,
    provider: Annotated[str, Form()],
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
    schemas: SchemaSource = request.app.state.schemas

    data = await _read_upload(file, settings.max_upload_bytes)
    extractor = _choose_extractor(request, settings, provider, model, api_key, base_url)

    try:
        # Resolving the connection by name is also what proves the caller is
        # allowed to use it. A connection string never crosses the wire.
        settings.dsn(connection)
        discovered = time.perf_counter()
        definition = await schemas.fetch(connection, namespace, table)
        discovery_ms = int((time.perf_counter() - discovered) * 1000)
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
    except (DatabaseUnavailableError, ExtractorNotConfiguredError) as error:
        # Neither the caller's mistake nor a defect in us: something the
        # deployment depends on is missing or down.
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ExtractionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

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
        "unmatched_headers": list(result.unmatched_headers),
        "missing_columns": list(result.missing_columns),
        "timings_ms": result.timings_ms,
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
        )
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
