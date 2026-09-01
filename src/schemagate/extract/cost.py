from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from schemagate.extract.base import Usage

# Prices are quoted per million tokens, which is how every provider publishes
# them, so a figure copied from a pricing page can be pasted in unchanged.
PER = Decimal(1_000_000)

# Six places, since a single document can cost a fraction of a cent. Fixed
# rather than exact so two costs are comparable as written: unquantised, the
# same two dollars arrives as "2" from one document and "2.0000" from another.
MONEY = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class Price:
    """What one model costs, per million tokens.

    Configuration rather than a table in this repository: a hardcoded price
    goes stale when a provider changes one, and nothing here would detect it.
    An unpriced model reports tokens and leaves `cost_usd` null.
    """

    input: Decimal
    output: Decimal
    # What a cached input token costs, when the provider discounts them. Left
    # unset, cached tokens are billed at the input rate, which overstates
    # rather than understates.
    cached_input: Decimal | None = None

    def of(self, usage: Usage) -> Decimal:
        cached_rate = self.input if self.cached_input is None else self.cached_input
        return (
            Decimal(usage.input_tokens) * self.input
            + Decimal(usage.cached_input_tokens) * cached_rate
            + Decimal(usage.output_tokens) * self.output
        ) / PER


@dataclass(frozen=True, slots=True)
class Spend:
    """Every model call one document needed, added up.

    A document can involve more than one model. A spreadsheet whose headings
    need matching by meaning pays for a small call on the otherwise free path.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: Decimal | None = None
    by_model: tuple[Usage, ...] = ()

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.cached_input_tokens + self.output_tokens


def tally(usages: Sequence[Usage], prices: Mapping[str, Price] | None = None) -> Spend:
    """Add up what was spent, and price it where a price is configured.

    Cost is null unless every model that ran has a price, since a partial total
    would be read as a total.
    """
    if not usages:
        return Spend()

    merged: dict[str, Usage] = {}
    for usage in usages:
        # Seeded at zero calls. A default Usage counts one, which is right for
        # a call that happened and wrong for the empty total it is added to.
        empty = Usage(model=usage.model, calls=0)
        merged[usage.model] = merged.get(usage.model, empty) + usage

    prices = prices or {}
    quoted = [(prices.get(model), usage) for model, usage in merged.items()]
    cost = (
        sum((price.of(usage) for price, usage in quoted if price), Decimal(0)).quantize(
            MONEY, rounding=ROUND_HALF_UP
        )
        if all(price is not None for price, _ in quoted)
        else None
    )

    return Spend(
        calls=sum(usage.calls for usage in merged.values()),
        input_tokens=sum(usage.input_tokens for usage in merged.values()),
        output_tokens=sum(usage.output_tokens for usage in merged.values()),
        cached_input_tokens=sum(usage.cached_input_tokens for usage in merged.values()),
        cost_usd=cost,
        by_model=tuple(merged.values()),
    )
