from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from schemagate.schema.spec import TableSchema
from schemagate.validate.report import Failure

DEFAULT_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class SumRule:
    """Assert that some columns add up to another one.

    Declared as data rather than parsed from an expression. A rule that arrives
    from configuration is input, and an expression evaluator taking input is a
    way to run arbitrary code inside the service.
    """

    terms: tuple[str, ...]
    equals: str
    tolerance: Decimal = field(default=DEFAULT_TOLERANCE)

    def describe(self) -> str:
        return f"{' + '.join(self.terms)} = {self.equals}"


def check_lengths(
    rows: Sequence[Mapping[str, Any]], schema: TableSchema, skip: set[tuple[int, str]]
) -> tuple[Failure, ...]:
    """Enforce `varchar(n)`, which the generated JSON schema deliberately omits.

    Strict structured output does not carry validation keywords reliably, so a
    length that the database enforces has to be enforced here instead.
    """
    failures: list[Failure] = []
    limited = [column for column in schema.extractable if column.max_length]

    for index, row in enumerate(rows):
        for column in limited:
            if (index, column.name) in skip:
                continue
            value = row.get(column.name)
            limit = column.max_length
            if isinstance(value, str) and limit is not None and len(value) > limit:
                failures.append(
                    Failure(
                        row=index,
                        column=column.name,
                        rule="length",
                        detail=(
                            f"{len(value)} characters, but {column.name!r} is "
                            f"{column.data_type}({limit})."
                        ),
                        value=value,
                    )
                )
    return tuple(failures)


def check_sums(
    rows: Sequence[Mapping[str, Any]], rules: Sequence[SumRule], skip: set[tuple[int, str]]
) -> tuple[Failure, ...]:
    """Check that the numbers in a row agree with each other.

    A rule is skipped rather than failed when any column it names is absent or
    null. An arithmetic complaint about a value that is already reported missing
    adds noise instead of information.
    """
    failures: list[Failure] = []

    for index, row in enumerate(rows):
        for rule in rules:
            names = (*rule.terms, rule.equals)
            if any((index, name) in skip for name in names):
                continue

            values = [row.get(name) for name in names]
            if not all(isinstance(value, Decimal) for value in values):
                continue

            *terms, target = (value for value in values if isinstance(value, Decimal))
            total = sum(terms, Decimal(0))
            if abs(total - target) > rule.tolerance:
                failures.append(
                    Failure(
                        row=index,
                        column=rule.equals,
                        rule="arithmetic",
                        detail=(
                            f"{rule.describe()} does not hold: the terms come to "
                            f"{total}, the document says {target}."
                        ),
                        value=str(target),
                    )
                )
    return tuple(failures)
