from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from schemagate.errors import ExtractionError
from schemagate.extract.base import Extractor, Usage
from schemagate.schema.spec import TableSchema

# Keyed on the headers and the table, because the same headers against the same
# table always mean the same thing. Supplier files repeat, so the second one
# costs nothing.
#
# Bounded, and oldest-first: an unbounded dict keyed on file headings grows for
# as long as the process lives, and a service that reads whatever people upload
# has no upper bound on distinct headings. The cap is large enough that a
# recurring set of suppliers never falls out of it.
MAX_REMEMBERED = 512

_answers: OrderedDict[tuple[tuple[str, ...], str], dict[str, str]] = OrderedDict()

NO_MATCH = "none"


@dataclass(frozen=True, slots=True)
class HeaderMatch:
    """Which heading meant which column, and what asking cost.

    A CSV takes the free path and its rows never leave the machine, but working
    out that `Fakturanr` is an invoice number is a billed model call, so it is
    reported like any other.
    """

    aliases: dict[str, str] = field(default_factory=dict)
    usage: tuple[Usage, ...] = ()


def forget_mappings() -> None:
    """Drop what has been learned so far.

    The cache invalidates itself when either the headings or the table
    change, since both are part of the key. This is for the case where a
    mapping was simply wrong and an operator wants it asked again.
    """
    _answers.clear()


async def map_headers(
    headers: tuple[str, ...], schema: TableSchema, extractor: Extractor | None
) -> HeaderMatch:
    """Work out which column each header means, when the words do not match.

    Only the names are sent. A header called `Fakturanr` means nothing to string
    matching and everything to a model, but deciding that needs the column names
    and nothing else. The rows stay on this machine, which is what keeps the
    tabular path free of the document ever reaching a provider.
    """
    if extractor is None or not headers:
        return HeaderMatch()

    columns = [column.name for column in schema.extractable]
    if not columns:
        return HeaderMatch()

    key = (headers, schema.fingerprint)
    if key in _answers:
        _answers.move_to_end(key)
        # No usage: a remembered answer is the saving, and reporting the
        # tokens of the call that filled the cache would bill it twice.
        return HeaderMatch(aliases=_answers[key])

    model = _mapping_model(columns)
    try:
        answer = await extractor.extract(_question(headers, schema), model)
    except ExtractionError:
        # Falling back to no mapping loses nothing: the unmatched headers are
        # reported either way, and a wrong mapping is worse than none.
        return HeaderMatch()

    mapping = _clean(answer.value, headers, columns)
    _answers[key] = mapping
    while len(_answers) > MAX_REMEMBERED:
        _answers.popitem(last=False)
    return HeaderMatch(aliases=mapping, usage=(answer.usage,))


def _question(headers: tuple[str, ...], schema: TableSchema) -> str:
    """The prompt. Column comments come along, since they say what a column means."""
    described = []
    for column in schema.extractable:
        line = f"- {column.name} ({column.data_type})"
        if column.description:
            line += f": {column.description}"
        described.append(line)

    # Joined outside the f-string: a backslash inside one is a syntax error
    # before Python 3.12, and this project supports 3.11.
    available = "\n".join(described)
    listed = "\n".join(f"- {header}" for header in headers)
    return (
        "Match each column heading from a spreadsheet to the database column it "
        "means. Headings may be in another language or abbreviated. Answer "
        f"{NO_MATCH!r} for a heading with no clear match rather than forcing one.\n\n"
        f"Database columns:\n{available}\n\n"
        f"Headings in the file:\n{listed}"
    )


def _mapping_model(columns: list[str]) -> type[BaseModel]:
    """A model that can only answer with a column that exists.

    The same constraint used for extraction: a name that is not in the table is
    not expressible, so it cannot be returned.
    """
    pair = create_model(
        "HeaderPair",
        __config__=ConfigDict(extra="forbid"),
        header=(str, Field(description="The heading exactly as it appears in the file.")),
        column=(
            Literal[(*columns, NO_MATCH)],
            Field(description="The database column it means."),
        ),
    )
    return create_model(
        "HeaderMapping",
        __config__=ConfigDict(extra="forbid"),
        pairs=(list[pair], Field(description="One entry per heading.")),  # type: ignore[valid-type]
    )


def _clean(answer: Any, headers: tuple[str, ...], columns: list[str]) -> dict[str, str]:
    """Keep only what is unambiguous.

    A heading the file does not have, a column the table does not have, and a
    second claim on a column already taken are all dropped. A wrong mapping
    writes the right-looking value into the wrong field, which is worse than
    reporting the heading as unmatched.
    """
    known_headers = set(headers)
    known_columns = set(columns)

    mapping: dict[str, str] = {}
    for pair in answer.pairs:
        if pair.column == NO_MATCH or pair.column not in known_columns:
            continue
        if pair.header not in known_headers:
            continue
        if pair.column in mapping.values():
            continue
        mapping[pair.header] = pair.column
    return mapping
