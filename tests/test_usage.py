"""What a document cost to read.

The pipeline reports rows and timings and, until this existed, said nothing at
all about the one part of the work that comes with an invoice attached. These
tests hold the counting to the same standard as the rows: added up correctly,
attributed to the model that ran, and never guessed at.
"""

from decimal import Decimal

import pytest

from schemagate.extract.base import Usage
from schemagate.extract.cost import Price, Spend, tally

OPUS = Price(input=Decimal("5"), output=Decimal("25"))
HAIKU = Price(input=Decimal("1"), output=Decimal("5"))


def test_two_calls_to_one_model_add_up() -> None:
    total = Usage(model="m", input_tokens=100, output_tokens=10) + Usage(
        model="m", input_tokens=50, output_tokens=5
    )

    assert total.input_tokens == 150
    assert total.output_tokens == 15
    assert total.calls == 2


def test_two_models_cannot_be_added_by_mistake() -> None:
    """Silently merging them would attribute one model's tokens to another's price."""
    with pytest.raises(ValueError, match="different models"):
        Usage(model="opus") + Usage(model="haiku")


def test_nothing_spent_is_an_empty_spend() -> None:
    assert tally(()) == Spend()


def test_a_million_input_tokens_costs_the_input_price() -> None:
    spend = tally([Usage(model="opus", input_tokens=1_000_000)], {"opus": OPUS})

    assert spend.cost_usd == Decimal("5.000000")


def test_input_and_output_are_priced_separately() -> None:
    spend = tally(
        [Usage(model="opus", input_tokens=200_000, output_tokens=40_000)],
        {"opus": OPUS},
    )

    assert spend.cost_usd == Decimal("2.000000")


def test_cached_tokens_are_cheaper_when_the_price_says_so() -> None:
    priced = {"opus": Price(input=Decimal("5"), output=Decimal("25"), cached_input=Decimal("0.5"))}

    spend = tally([Usage(model="opus", cached_input_tokens=1_000_000)], priced)

    assert spend.cost_usd == Decimal("0.500000")


def test_cached_tokens_cost_full_price_when_the_price_does_not_say() -> None:
    """Overstating a bill is recoverable. Understating one is discovered later."""
    spend = tally([Usage(model="opus", cached_input_tokens=1_000_000)], {"opus": OPUS})

    assert spend.cost_usd == Decimal("5.000000")


def test_two_models_are_priced_at_their_own_rates() -> None:
    spend = tally(
        [
            Usage(model="opus", input_tokens=1_000_000),
            Usage(model="haiku", input_tokens=1_000_000),
        ],
        {"opus": OPUS, "haiku": HAIKU},
    )

    assert spend.cost_usd == Decimal("6.000000")
    assert spend.calls == 2


def test_an_unpriced_model_reports_tokens_and_no_cost() -> None:
    spend = tally([Usage(model="qwen3", input_tokens=900, output_tokens=100)])

    assert spend.input_tokens == 900
    assert spend.cost_usd is None, "a price nobody configured cannot be invented"


def test_one_unpriced_model_withholds_the_whole_total() -> None:
    """A partial total is read as a total, which is worse than no total."""
    spend = tally(
        [Usage(model="opus", input_tokens=1_000_000), Usage(model="mystery", input_tokens=500)],
        {"opus": OPUS},
    )

    assert spend.cost_usd is None
    assert spend.input_tokens == 1_000_500


def test_the_breakdown_names_every_model_that_ran() -> None:
    spend = tally(
        [
            Usage(model="opus", input_tokens=10),
            Usage(model="haiku", input_tokens=20),
            Usage(model="opus", input_tokens=30),
        ]
    )

    assert {usage.model: usage.input_tokens for usage in spend.by_model} == {
        "opus": 40,
        "haiku": 20,
    }


def test_total_tokens_counts_the_cached_ones_too() -> None:
    spend = tally([Usage(model="m", input_tokens=10, cached_input_tokens=5, output_tokens=2)])

    assert spend.total_tokens == 17
