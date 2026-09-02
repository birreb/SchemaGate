import re
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

    @property
    def columns(self) -> tuple[str, ...]:
        return (*self.terms, self.equals)


@dataclass(frozen=True, slots=True)
class ProductRule:
    """Assert that some columns multiply to another one.

    `quantity * unit_price = line_total` on a line item. A model that glues two
    printed columns into one number, `122940.00` for a line total of `2940.00`
    beside a quantity of `12`, produces a row this rule refuses.
    """

    factors: tuple[str, ...]
    equals: str
    tolerance: Decimal = field(default=DEFAULT_TOLERANCE)

    def describe(self) -> str:
        return f"{' * '.join(self.factors)} = {self.equals}"

    @property
    def columns(self) -> tuple[str, ...]:
        return (*self.factors, self.equals)


@dataclass(frozen=True, slots=True)
class RejectRule:
    """Values that can never be right for a column.

    Your own VAT number can never be a supplier's, and your own company name
    can never be the supplier. A document prints both parties side by side,
    and a model that takes the wrong one produces a value that looks
    plausible and is not. Compared with case and spacing ignored.
    """

    column: str
    reject: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.column} is not one of {', '.join(repr(value) for value in self.reject)}"

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.column,)


@dataclass(frozen=True, slots=True)
class PatternRule:
    """A regular expression the whole value must match.

    For identifiers with a known shape that the column type cannot express: a
    VAT number begins with a country code, an IBAN with two letters and two
    digits. A value the document prints in the wrong place, an EIN where a VAT
    number was wanted, fails the shape even though it is a real identifier.
    """

    column: str
    pattern: str

    def describe(self) -> str:
        return f"{self.column} matches {self.pattern!r}"

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.column,)


@dataclass(frozen=True, slots=True)
class RequireRule:
    """A column the operator expects to be filled, though the table allows null.

    A seller VAT number is nullable because some suppliers have none, and a
    model that could not find one returns null, which the table accepts. Where
    the operator knows the value is nearly always printed, a null is worth a
    look, and this says so.
    """

    column: str

    def describe(self) -> str:
        return f"{self.column} is present"

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.column,)


@dataclass(frozen=True, slots=True)
class RangeRule:
    """Bounds on a numeric column that the type does not carry.

    A tax that is never zero, a shipping charge that is never half the invoice,
    a quantity that is never negative. A model that splits one printed amount
    into two that still add up passes every arithmetic rule; a bound on what
    each part can be is what refuses it.
    """

    column: str
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def describe(self) -> str:
        bounds = []
        if self.minimum is not None:
            bounds.append(f"at least {self.minimum}")
        if self.maximum is not None:
            bounds.append(f"at most {self.maximum}")
        return f"{self.column} is {' and '.join(bounds)}"

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.column,)


Rule = SumRule | ProductRule | RejectRule | PatternRule | RequireRule | RangeRule


def parse_rule(raw: Mapping[str, Any]) -> Rule:
    """Build a rule from its configured form.

    The keys say which kind it is: `terms` for a sum, `factors` for a product,
    `reject` for forbidden values, `pattern` for a shape. Anything else is a
    configuration error and is reported as one rather than ignored.
    """
    fields = dict(raw)
    if "terms" in fields:
        return SumRule(
            terms=tuple(fields["terms"]),
            equals=fields["equals"],
            tolerance=Decimal(str(fields.get("tolerance", DEFAULT_TOLERANCE))),
        )
    if "factors" in fields:
        return ProductRule(
            factors=tuple(fields["factors"]),
            equals=fields["equals"],
            tolerance=Decimal(str(fields.get("tolerance", DEFAULT_TOLERANCE))),
        )
    if "reject" in fields:
        return RejectRule(column=fields["column"], reject=tuple(fields["reject"]))
    if "pattern" in fields:
        re.compile(fields["pattern"])
        return PatternRule(column=fields["column"], pattern=fields["pattern"])
    if fields.get("require") is True:
        return RequireRule(column=fields["column"])
    if "min" in fields or "max" in fields:
        return RangeRule(
            column=fields["column"],
            minimum=Decimal(str(fields["min"])) if "min" in fields else None,
            maximum=Decimal(str(fields["max"])) if "max" in fields else None,
        )
    raise ValueError(
        "A rule needs `terms` and `equals` for a sum, `factors` and `equals` for a "
        "product, `column` and `reject` for forbidden values, `column` and `pattern` "
        "for a shape, `column` and `require: true` for a value that must be present, or "
        "`column` with `min` or `max` for bounds. "
        f"Got keys: {', '.join(sorted(fields)) or 'none'}."
    )


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
    rows: Sequence[Mapping[str, Any]], rules: Sequence[Rule], skip: set[tuple[int, str]]
) -> tuple[Failure, ...]:
    """Check that the numbers in a row agree with each other.

    A rule is skipped rather than failed when any column it names is absent or
    null. An arithmetic complaint about a value that is already reported missing
    adds noise instead of information.
    """
    failures: list[Failure] = []

    for index, row in enumerate(rows):
        for rule in rules:
            if not isinstance(rule, SumRule | ProductRule):
                continue
            names = rule.columns
            if any((index, name) in skip for name in names):
                continue

            values = [row.get(name) for name in names]
            if not all(isinstance(value, Decimal) for value in values):
                continue

            *operands, target = (value for value in values if isinstance(value, Decimal))
            if isinstance(rule, SumRule):
                computed = sum(operands, Decimal(0))
            else:
                computed = Decimal(1)
                for operand in operands:
                    computed *= operand
            if abs(computed - target) > rule.tolerance:
                failures.append(
                    Failure(
                        row=index,
                        column=rule.equals,
                        rule="arithmetic",
                        detail=(
                            f"{rule.describe()} does not hold: the terms come to "
                            f"{computed.quantize(rule.tolerance)}, the document says {target}."
                        ),
                        value=str(target),
                    )
                )
    return tuple(failures)


def _folded(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)).casefold()


def check_values(
    rows: Sequence[Mapping[str, Any]], rules: Sequence[Rule], skip: set[tuple[int, str]]
) -> tuple[Failure, ...]:
    """Check single values against what an operator knows about them."""
    failures: list[Failure] = []

    for index, row in enumerate(rows):
        for rule in rules:
            if not isinstance(rule, RejectRule | PatternRule | RequireRule | RangeRule):
                continue
            if (index, rule.column) in skip:
                continue
            value = row.get(rule.column)
            if isinstance(rule, RequireRule):
                if value is None:
                    failures.append(
                        Failure(
                            row=index,
                            column=rule.column,
                            rule="required",
                            detail=(
                                f"Column {rule.column!r} is expected to be present and the "
                                f"document gave no value."
                            ),
                        )
                    )
                continue
            if value is None:
                continue

            if isinstance(rule, RangeRule):
                if not isinstance(value, Decimal | int | float):
                    continue
                number = Decimal(str(value))
                below = rule.minimum is not None and number < rule.minimum
                above = rule.maximum is not None and number > rule.maximum
                if below or above:
                    failures.append(
                        Failure(
                            row=index,
                            column=rule.column,
                            rule="range",
                            detail=(
                                f"{value} is outside what {rule.column!r} can be: "
                                f"{rule.describe()}."
                            ),
                            value=str(value),
                        )
                    )
                continue

            if isinstance(rule, RejectRule):
                if _folded(value) in {_folded(bad) for bad in rule.reject}:
                    failures.append(
                        Failure(
                            row=index,
                            column=rule.column,
                            rule="rejected_value",
                            detail=(
                                f"{value!r} can never be right for {rule.column!r}: it is one "
                                f"of the values this table rejects."
                            ),
                            value=str(value),
                        )
                    )
            elif re.fullmatch(rule.pattern, str(value)) is None:
                failures.append(
                    Failure(
                        row=index,
                        column=rule.column,
                        rule="pattern",
                        detail=f"{value!r} does not match the shape {rule.pattern!r}.",
                        value=str(value),
                    )
                )
    return tuple(failures)
