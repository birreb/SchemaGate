"""Whether the rows that came back are all the rows the document lists.

A row can be checked against itself and against the table. Nothing in a row
says how many rows there should have been, but the document does: a statement
names every invoice on it, and the numbers share a shape. When the text carries
more values of that shape than the rows returned, the document is reported as
incomplete and the values nobody returned are named, so a person can see which
lines were dropped rather than discover it at month end.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from schemagate.schema.spec import TableSchema
from schemagate.validate.report import Failure

# An identifier: a short run of letters, an optional separator, then digits.
# Pure digits are excluded on purpose, since postcodes, account numbers and
# amounts would all match and every document would look incomplete.
IDENTIFIER = re.compile(r"^([A-Za-z]{1,4}[-/]?)(\d{4,})$")

TEXT_TYPES = frozenset({"text", "varchar", "bpchar", "char", "name"})

# The failure names a few of the missing values, not all of them, so a
# document with hundreds of dropped rows produces one readable line.
NAMED = 6

# Whole-document findings carry no row index. -1 says so without inventing one.
DOCUMENT = -1

# With a single row no column proves itself a key by being distinct, so the
# name has to say so. Only the first such column is tried, in schema order, so
# an invoice number is used and a VAT number, which the buyer's would also
# match, is not reached.
KEY_NAME = re.compile(r"(number|_no|nr|_id|ref|code|key)$", re.IGNORECASE)

# Below this much text a document has nothing to be complete against.
LITTLE_TEXT = 80


def find_uncounted(
    text: str, rows: Sequence[Mapping[str, Any]], schema: TableSchema
) -> Failure | None:
    """Report values the document names that no returned row carries.

    The key column is found rather than configured: a text column whose values
    are distinct across the rows and share one identifier shape. With a single
    row the column's name has to say it is a key, and at least two other values
    of the shape must appear, since one other value is as likely a reference to
    another document. Matches are compared as distinct values, so a number
    repeated in a footer is not a missing row. A document with text and no rows
    at all is reported as empty.
    """
    if not rows:
        if len(text.strip()) >= LITTLE_TEXT:
            return Failure(
                row=DOCUMENT,
                column=None,
                rule="empty",
                detail=(
                    f"The document has {len(text.strip())} characters of text and no rows "
                    f"came back."
                ),
            )
        return None

    for column in schema.extractable:
        if column.data_type not in TEXT_TYPES:
            continue
        if len(rows) == 1 and not KEY_NAME.search(column.name):
            continue
        values = [str(row.get(column.name)) for row in rows if row.get(column.name) is not None]
        if len(values) != len(rows) or len(set(values)) != len(values):
            continue

        parsed = [IDENTIFIER.match(value) for value in values]
        if not all(parsed):
            continue
        prefixes = {match.group(1) for match in parsed if match}
        lengths = {len(match.group(2)) for match in parsed if match}
        if len(prefixes) != 1 or len(lengths) != 1:
            continue

        prefix = next(iter(prefixes))
        digits = next(iter(lengths))
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(prefix)}\d{{{digits}}}(?!\d)")
        mentioned = set(pattern.findall(text))
        returned = set(values)
        missing = sorted(mentioned - returned)
        if not missing:
            return None
        if len(rows) == 1 and len(missing) < 2:
            # One other value of the same shape beside a single row is as
            # likely a reference to another document as a dropped row.
            return None

        shown = ", ".join(missing[:NAMED]) + (", ..." if len(missing) > NAMED else "")
        return Failure(
            row=DOCUMENT,
            column=column.name,
            rule="incomplete",
            detail=(
                f"The document names {len(mentioned)} values shaped like {column.name!r} and "
                f"{len(rows)} rows came back. Not returned: {shown}"
            ),
        )
    return None
