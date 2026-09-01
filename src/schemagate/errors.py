class SchemaGateError(Exception):
    """Base class for every error raised by SchemaGate."""


class ConfigurationError(SchemaGateError):
    """The service is misconfigured and cannot start."""


class UnknownConnectionError(SchemaGateError):
    """A caller asked for a database connection that is not configured."""
