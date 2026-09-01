import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.report import Failure

TRUE_WORDS = frozenset({"true", "t", "yes", "y", "1"})
FALSE_WORDS = frozenset({"false", "f", "no", "n", "0"})

# Symbols only. A separator must never appear here: stripping a dot would turn
# 1.234 into 1234 silently.
CURRENCY_SYMBOLS = frozenset("€$£¥₹₽₩")

INTEGER_TYPES = frozenset({"int2", "int4", "int8"})
FLOAT_TYPES = frozenset({"float4", "float8"})
EXACT_TYPES = frozenset({"numeric", "decimal"})

# A separator followed by exactly three digits is either a thousands separator
# or a decimal point, and nothing in the string says which.
GROUP_SIZE = 3


class _AmbiguousNumberError(ValueError):
    """A number whose separator could be grouping or decimal, with no way to tell."""


def coerce_rows(
    rows: Sequence[Mapping[str, str | None]], schema: TableSchema
) -> tuple[tuple[dict[str, Any], ...], tuple[Failure, ...]]:
    """Turn the strings that came out of a file or a model into database types.

    A row that fails is reported, not dropped, and the rows around it still
    come back. The caller decides whether a single bad line is worth rejecting
    the whole document.
    """
    coerced: list[dict[str, Any]] = []
    failures: list[Failure] = []

    for index, row in enumerate(rows):
        values: dict[str, Any] = {}
        for column in schema.extractable:
            text = row.get(column.name)
            value, failure = _coerce_cell(text, column, index)
            values[column.name] = value
            if failure is not None:
                failures.append(failure)
        coerced.append(values)

    return tuple(coerced), tuple(failures)


def _coerce_cell(text: str | None, column: ColumnSpec, row: int) -> tuple[Any, Failure | None]:
    if text is None:
        if column.nullable:
            return None, None
        return None, Failure(
            row=row,
            column=column.name,
            rule="not_null",
            detail=f"Column {column.name!r} is NOT NULL but the document gave no value.",
        )

    try:
        return _convert(text, column), None
    except _AmbiguousNumberError:
        return None, Failure(
            row=row,
            column=column.name,
            rule="ambiguous_number",
            detail=(
                f"{text!r} could be a thousands separator or a decimal point. "
                f"A wrong reading is out by a factor of a thousand, so it is refused."
            ),
            value=text,
        )
    except _NotALabelError as error:
        return None, Failure(
            row=row, column=column.name, rule="enum", detail=str(error), value=text
        )
    except (ValueError, ArithmeticError) as error:
        return None, Failure(
            row=row,
            column=column.name,
            rule="type",
            detail=f"{text!r} is not a valid {column.data_type}: {error}",
            value=text,
        )


class _NotALabelError(ValueError):
    """A value that is not one of the enum's labels."""


def _convert(text: str, column: ColumnSpec) -> Any:
    if column.enum_labels:
        if text not in column.enum_labels:
            allowed = ", ".join(repr(label) for label in column.enum_labels)
            raise _NotALabelError(f"{text!r} is not one of: {allowed}.")
        return text

    if column.data_type.startswith("_"):
        element = ColumnSpec(
            name=column.name,
            data_type=column.data_type[1:],
            nullable=False,
            ordinal=column.ordinal,
            enum_labels=column.enum_labels,
        )
        return [_convert(item.strip(), element) for item in _split_array(text)]

    return _convert_scalar(text, column.data_type)


def _convert_scalar(text: str, data_type: str) -> Any:
    if data_type in EXACT_TYPES:
        return _to_decimal(text)
    if data_type in INTEGER_TYPES:
        return _to_int(text)
    if data_type in FLOAT_TYPES:
        return float(_normalise(_strip(text)[0]))
    if data_type == "bool":
        return _to_bool(text)
    if data_type == "date":
        return dt.date.fromisoformat(text.strip())
    if data_type in {"timestamp", "timestamptz"}:
        return dt.datetime.fromisoformat(text.strip())
    if data_type in {"time", "timetz"}:
        return dt.time.fromisoformat(text.strip())
    if data_type == "uuid":
        return uuid.UUID(text.strip())
    return text


def _split_array(text: str) -> list[str]:
    inner = text.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    return [item for item in inner.split(",") if item.strip()]


def _to_decimal(text: str) -> Decimal:
    body, negative = _strip(text)
    try:
        value = Decimal(_normalise(body))
    except InvalidOperation as error:
        raise ValueError("not a number") from error
    return -value if negative else value


def _to_int(text: str) -> int:
    body, negative = _strip(text)
    digits = _normalise(body)
    if not digits.isdigit():
        raise ValueError(
            "an integer column takes digits only, so a fraction or an exponent "
            "means the source already lost the exact value"
        )
    return -int(digits) if negative else int(digits)


def _to_bool(text: str) -> bool:
    word = text.strip().casefold()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise ValueError(f"expected one of {sorted(TRUE_WORDS | FALSE_WORDS)}")


def _strip(text: str) -> tuple[str, bool]:
    """Remove presentation and report the sign.

    Accounting exports write a negative in parentheses, and currency symbols and
    thin spaces travel with the number rather than meaning anything.
    """
    body = text.strip()
    negative = False

    if body.startswith("(") and body.endswith(")"):
        negative = True
        body = body[1:-1]

    body = "".join(
        character
        for character in body
        if not character.isspace() and character not in CURRENCY_SYMBOLS
    )

    if body.startswith("-"):
        negative = not negative
        body = body[1:]
    elif body.startswith("+"):
        body = body[1:]

    return body, negative


def _normalise(body: str) -> str:
    """Resolve grouping and decimal separators into a plain decimal string.

    With both separators present the last one is the decimal point, which
    settles the common European and Anglo formats without guessing. A separator
    that repeats can only be grouping. What is left is a single separator, and
    if exactly three digits follow it the string is genuinely ambiguous.
    """
    dots = body.count(".")
    commas = body.count(",")

    if dots and commas:
        decimal_point = "." if body.rfind(".") > body.rfind(",") else ","
        grouping = "," if decimal_point == "." else "."
        return body.replace(grouping, "").replace(decimal_point, ".")

    separator = "." if dots else ("," if commas else "")
    if not separator:
        return body

    if (dots or commas) > 1:
        return body.replace(separator, "")

    head, _, tail = body.partition(separator)
    if len(tail) == GROUP_SIZE and tail.isdigit():
        raise _AmbiguousNumberError(body)
    return f"{head}.{tail}"
