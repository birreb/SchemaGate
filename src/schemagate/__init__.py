"""Extract documents into rows that match your PostgreSQL schema.

Two ways in. `process` is the library: give it a file, a table definition and
an extractor, and it hands back validated rows with what they cost. `create_app`
and `install` are the service: the first runs it, the second adds its endpoints
to an application you already have.

Everything a caller should need is named here. Reaching into `schemagate.pipeline`
or `schemagate.db.pool` still works and is not the supported surface, which
matters because those are the modules free to move.
"""

from typing import TYPE_CHECKING

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
    SchemaGateError,
    TableNotFoundError,
    UnknownConnectionError,
    UnsupportedColumnTypeError,
    UnsupportedFileTypeError,
)
from schemagate.extract.base import Extracted, Extractor, Usage
from schemagate.extract.cost import Price, Spend
from schemagate.extract.factory import build_extractor, make_extractor
from schemagate.pipeline import Extraction, Route, Stage, process
from schemagate.schema.spec import ColumnSpec, TableRef, TableSchema
from schemagate.validate.report import Failure
from schemagate.validate.rules import SumRule

if TYPE_CHECKING:
    # Typed here and imported nowhere at runtime. A type checker gets the real
    # signatures; an install without the `server` or `postgres` extra still
    # imports this module.
    from schemagate.api.app import create_app, install, shutdown
    from schemagate.db.pool import PoolSchemas

__version__ = "0.1.0"

# Imported on first use rather than here. Each one needs a dependency the
# library itself does not: FastAPI for the endpoints, asyncpg for the pool.
# Naming them in `__all__` without importing them is what lets
# `pip install schemagate` stay small and still make `from schemagate import
# create_app` work the moment the extra is present.
_LAZY = {
    "create_app": "schemagate.api.app",
    "install": "schemagate.api.app",
    "shutdown": "schemagate.api.app",
    "PoolSchemas": "schemagate.db.pool",
}

__all__ = [
    "ColumnSpec",
    "ConfigurationError",
    "DatabaseUnavailableError",
    "Extracted",
    "Extraction",
    "ExtractionError",
    "Extractor",
    "ExtractorNotConfiguredError",
    "Failure",
    "MalformedDocumentError",
    "MissingDependencyError",
    "NotAuthorisedError",
    "PoolSchemas",
    "Price",
    "RateLimitedError",
    "Route",
    "SchemaGateError",
    "Settings",
    "Spend",
    "Stage",
    "SumRule",
    "TableNotFoundError",
    "TableRef",
    "TableSchema",
    "UnknownConnectionError",
    "UnsupportedColumnTypeError",
    "UnsupportedFileTypeError",
    "Usage",
    "__version__",
    "build_extractor",
    "create_app",
    "install",
    "make_extractor",
    "process",
    "shutdown",
]


def __getattr__(name: str) -> object:
    """Resolve the names that cost a dependency to import."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)
