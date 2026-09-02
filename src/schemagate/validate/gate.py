from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from schemagate.schema.spec import TableSchema
from schemagate.validate.coerce import coerce_rows
from schemagate.validate.report import Failure
from schemagate.validate.rules import Rule, check_lengths, check_sums, check_values


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Coerced rows and everything that did not hold."""

    rows: tuple[dict[str, Any], ...]
    failures: tuple[Failure, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def validate(
    rows: Sequence[Mapping[str, Any]],
    schema: TableSchema,
    rules: Sequence[Rule] = (),
) -> ValidationReport:
    """Run the gate: coerce, then check what the schema alone cannot express.

    Ordered cheapest first, and each layer skips cells the previous one already
    rejected. One wrong cell should produce one finding, not three restatements
    of the same problem in different words.
    """
    coerced, failures = coerce_rows(rows, schema)
    rejected = {(failure.row, failure.column) for failure in failures if failure.column}

    failures += check_lengths(coerced, schema, rejected)
    failures += check_sums(coerced, rules, rejected)
    failures += check_values(coerced, rules, rejected)

    return ValidationReport(rows=coerced, failures=tuple(sorted(failures, key=_position)))


def _position(failure: Failure) -> tuple[int, str]:
    return failure.row, failure.column or ""
