class SchemaGateError(Exception):
    """Base class for every error raised by SchemaGate."""


class ConfigurationError(SchemaGateError):
    """The service is misconfigured and cannot start."""


class UnknownConnectionError(SchemaGateError):
    """A caller asked for a database connection that is not configured."""


class TableNotFoundError(SchemaGateError):
    """The requested table does not exist or is not visible to the role."""


class UnsupportedColumnTypeError(SchemaGateError):
    """A table contains a column SchemaGate cannot compile into a model."""


class MalformedDocumentError(SchemaGateError):
    """An uploaded file could not be parsed as the type it claims to be."""


class UnsupportedFileTypeError(SchemaGateError):
    """An upload is not a file type SchemaGate knows how to read."""


class ExtractionError(SchemaGateError):
    """A model failed to return usable output."""


class DatabaseUnavailableError(SchemaGateError):
    """A configured database could not be reached."""


class ExtractorNotConfiguredError(SchemaGateError):
    """A document needs a model and no model server is configured."""
