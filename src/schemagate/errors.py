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
