"""Measure accuracy, latency and cost together, on documents with known answers.

The unit tests exercise the pipeline against a stub extractor. They do not say
whether a given model reads a given invoice correctly.

All three numbers come from one pass, since the question is which model is
cheap enough and still correct, and accuracy and cost do not answer that
separately.

A case carries its own table definition, so this runs against a provider with
no database involved.
"""

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from schemagate.api.serialize import to_json_value
from schemagate.errors import ConfigurationError, SchemaGateError
from schemagate.extract.base import Extractor
from schemagate.extract.cost import Price, Spend
from schemagate.pipeline import process
from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.rules import Rule, parse_rule


@dataclass(frozen=True, slots=True)
class Case:
    """One document, and what a correct reading of it produces."""

    name: str
    document: Path
    table: TableSchema
    expected: tuple[dict[str, Any], ...]
    why: str = ""
    instructions: str | None = None
    rules: tuple[Rule, ...] = ()
    # Rows the gate is supposed to flag. A fixture with a deliberate arithmetic
    # error is testing the gate, so catching it is the case passing.
    expected_flags: int = 0


@dataclass(frozen=True, slots=True)
class Result:
    """How one case went."""

    case: Case
    route: str = ""
    rows_found: int = 0
    cells_expected: int = 0
    cells_correct: int = 0
    flags: int = 0
    ms: int = 0
    spend: Spend = field(default_factory=Spend)
    wrong: tuple[str, ...] = ()
    error: str = ""

    @property
    def accuracy(self) -> float:
        """Cells read correctly, as a fraction.

        Per cell rather than per row. A row scored whole reports a model that
        misreads one date out of eight columns identically to one that invents
        the entire row.
        """
        if not self.cells_expected:
            return 0.0
        return self.cells_correct / self.cells_expected

    @property
    def ok(self) -> bool:
        return not self.error and self.accuracy == 1.0 and self.flags == self.case.expected_flags


def load_cases(directory: Path) -> tuple[Case, ...]:
    """Read every case in a directory, in name order."""
    found = [_case(path) for path in sorted(directory.glob("*.json"))]
    if not found:
        raise ConfigurationError(f"No evaluation cases found in {directory}.")
    return tuple(found)


def _case(path: Path) -> Case:
    raw = json.loads(path.read_text(encoding="utf-8"))
    table = raw["table"]
    document = Path(raw["document"])
    if not document.is_absolute():
        # Relative to the repository rather than to the case file, so a path in
        # a case reads the same as one typed at a shell in the project root.
        document = Path.cwd() / document

    return Case(
        name=raw.get("name", path.stem),
        document=document,
        why=raw.get("why", ""),
        instructions=raw.get("instructions"),
        table=TableSchema(
            schema=table["schema"],
            name=table["name"],
            columns=tuple(_column(column) for column in table["columns"]),
        ),
        expected=tuple(raw["expected"]),
        rules=tuple(parse_rule(rule) for rule in raw.get("rules", ())),
        expected_flags=raw.get("expected_flags", 0),
    )


def _column(raw: Mapping[str, Any]) -> ColumnSpec:
    """One column, with JSON's lists turned back into tuples.

    A ColumnSpec is hashed: the compiled model is cached on the table it came
    from, and a list of enum labels makes the whole table unhashable. JSON has
    no tuples, so this is where they come back.
    """
    fields = dict(raw)
    if "enum_labels" in fields:
        fields["enum_labels"] = tuple(fields["enum_labels"])
    return ColumnSpec(**fields)


async def run_case(
    case: Case,
    extractor: Extractor | None,
    prices: Mapping[str, Price] | None = None,
) -> Result:
    """Read one document and score it against its known answer.

    A failure is recorded rather than raised, so one refused document does not
    discard the results for the rest of the run.
    """
    if not case.document.exists():
        return Result(case=case, error=f"Missing document: {case.document}")

    started = time.perf_counter()
    try:
        extraction = await process(
            case.document.read_bytes(),
            case.document.name,
            case.table,
            extractor=extractor,
            rules=case.rules,
            instructions=case.instructions,
            prices=prices,
        )
    except SchemaGateError as error:
        return Result(
            case=case,
            ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(error).__name__}: {error}",
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    correct, expected, wrong = _score(case.expected, extraction.rows)
    return Result(
        case=case,
        route=extraction.route.value,
        rows_found=len(extraction.rows),
        cells_expected=expected,
        cells_correct=correct,
        flags=len(extraction.failures),
        ms=elapsed,
        spend=extraction.spend,
        wrong=wrong,
    )


def _score(
    expected: Sequence[Mapping[str, Any]], actual: Sequence[Mapping[str, Any]]
) -> tuple[int, int, tuple[str, ...]]:
    """Compare cell by cell, in order, and name what did not match.

    Rows are matched positionally. A document lists its rows in an order, and
    pairing by best fit would hide a model that returned the right values
    against the wrong rows.
    """
    correct = 0
    total = 0
    wrong: list[str] = []

    for index, wanted in enumerate(expected):
        found = actual[index] if index < len(actual) else {}
        for column, value in wanted.items():
            total += 1
            got = _rendered(found.get(column))
            if got == _rendered(value):
                correct += 1
            elif len(wrong) < 8:
                wrong.append(f"row {index + 1} {column}: wanted {value!r}, got {got!r}")

    return correct, total, tuple(wrong)


def _rendered(value: Any) -> str | None:
    """One spelling for comparison, so `10.00` and `Decimal('10.00')` agree."""
    if value is None:
        return None
    rendered = to_json_value(value)
    if isinstance(rendered, Decimal):
        return str(rendered)
    return rendered if isinstance(rendered, str) else json.dumps(rendered, sort_keys=True)


async def evaluate(
    cases: Sequence[Case],
    extractor: Extractor | None,
    prices: Mapping[str, Price] | None = None,
) -> tuple[Result, ...]:
    """Run every case in order.

    Sequentially, so that a provider's rate limiter does not turn the latency
    column into a report of queueing.
    """
    return tuple([await run_case(case, extractor, prices) for case in cases])


def report(results: Sequence[Result]) -> str:
    """One row per case, then the totals, then what did not match."""
    lines = [
        f"{'case':<26} {'route':<11} {'rows':>5} {'cells':>7} {'ms':>7} {'tokens':>8} {'cost':>10}",
        "-" * 80,
    ]
    for result in results:
        cells = f"{result.cells_correct}/{result.cells_expected}"
        cost = "-" if result.spend.cost_usd is None else f"${result.spend.cost_usd}"
        lines.append(
            f"{result.case.name:<26} {result.route or '-':<11} {result.rows_found:>5} "
            f"{cells:>7} {result.ms:>7} {result.spend.total_tokens:>8} {cost:>10}"
        )

    lines.append("-" * 80)
    lines.append(_totals(results))

    for result in results:
        if result.error:
            lines.append(f"\n{result.case.name}: {result.error}")
        elif result.wrong:
            lines.append(f"\n{result.case.name}:")
            lines.extend(f"  {detail}" for detail in result.wrong)
        if result.flags != result.case.expected_flags:
            lines.append(
                f"\n{result.case.name}: the gate flagged {result.flags} value(s), "
                f"and this case expects {result.case.expected_flags}"
            )

    return "\n".join(lines)


def _totals(results: Sequence[Result]) -> str:
    correct = sum(result.cells_correct for result in results)
    expected = sum(result.cells_expected for result in results)
    tokens = sum(result.spend.total_tokens for result in results)
    ms = sum(result.ms for result in results)
    passed = sum(1 for result in results if result.ok)

    costs = [result.spend.cost_usd for result in results]
    cost = (
        f"${sum((value for value in costs if value is not None), Decimal(0))}"
        if all(value is not None for value in costs)
        else "unpriced"
    )
    share = f"{100 * correct / expected:.1f}%" if expected else "n/a"

    return (
        f"{passed}/{len(results)} cases clean, {correct}/{expected} cells ({share}), "
        f"{ms} ms, {tokens} tokens, {cost}"
    )
