import datetime as dt
import uuid
from decimal import Decimal
from typing import Any


def to_json_value(value: Any) -> Any:
    """Render a coerced value for the response body.

    Exact numbers leave as strings. JSON has one number type and every client
    parser reads it as a float, so emitting 1234.56 as a JSON number would undo
    the exactness the rest of the pipeline exists to preserve. A caller that
    wants arithmetic can parse the string with a decimal type of its own.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict):
        # A json column arrives parsed, and goes out as structure rather
        # than as a string containing structure.
        return {key: to_json_value(item) for key, item in value.items()}
    return value


def to_json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {name: to_json_value(value) for name, value in row.items()}
