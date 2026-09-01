from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from schemagate.api.serialize import to_json_row
from schemagate.config import Settings
from schemagate.errors import (
    DatabaseUnavailableError,
    ExtractionError,
    MalformedDocumentError,
    TableNotFoundError,
    UnknownConnectionError,
    UnsupportedColumnTypeError,
    UnsupportedFileTypeError,
)
from schemagate.pipeline import process
from schemagate.schema.spec import TableSchema

router = APIRouter()

# Read once at import. The page is a fixed asset, and reading it from disk on
# every request would be work done for no reason.
PLAYGROUND = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


class SchemaSource(Protocol):
    """Where a table definition comes from."""

    async def fetch(self, connection: str, schema: str, table: str) -> TableSchema: ...


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def playground() -> str:
    """A single page for trying the endpoint by hand.

    Not a dashboard and not the product. It exists so that someone evaluating
    the API can see it work in the first minute, and it calls the same public
    endpoint anyone else would. Everything is inline: a page that fetched a font
    or a script from the internet would not load inside a private network.
    """
    return PLAYGROUND


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
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    schemas: SchemaSource = request.app.state.schemas

    data = await _read_upload(file, settings.max_upload_bytes)

    try:
        # Resolving the connection by name is also what proves the caller is
        # allowed to use it. A connection string never crosses the wire.
        settings.dsn(connection)
        definition = await schemas.fetch(connection, namespace, table)
        result = await process(
            data,
            file.filename,
            definition,
            extractor=request.app.state.extractor,
            rules=settings.rules_for(definition.qualified_name),
        )
    except UnknownConnectionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TableNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except UnsupportedFileTypeError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except (MalformedDocumentError, UnsupportedColumnTypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DatabaseUnavailableError as error:
        # Neither the caller's mistake nor a defect in us: the database is down.
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
        "unmatched_headers": list(result.unmatched_headers),
        "missing_columns": list(result.missing_columns),
        "timings_ms": result.timings_ms,
    }


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
