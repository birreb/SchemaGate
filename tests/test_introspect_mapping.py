from typing import Any

from schemagate.db.introspect import to_column_spec


def record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "total",
        "data_type": "numeric",
        "nullable": False,
        "ordinal": 1,
        "description": None,
        "enum_labels": None,
        "max_length": None,
        "has_default": False,
        "is_generated": False,
        "is_identity": False,
    }
    return {**base, **overrides}


def test_maps_the_plain_fields() -> None:
    column = to_column_spec(record(name="vat_id", data_type="text", nullable=True, ordinal=4))

    assert (column.name, column.data_type, column.nullable, column.ordinal) == (
        "vat_id",
        "text",
        True,
        4,
    )


def test_absent_enum_labels_become_an_empty_tuple() -> None:
    assert to_column_spec(record()).enum_labels == ()


def test_enum_labels_become_a_tuple_so_the_spec_stays_hashable() -> None:
    column = to_column_spec(record(enum_labels=["draft", "sent"]))

    assert column.enum_labels == ("draft", "sent")
    hash(column)


def test_enum_label_order_is_preserved() -> None:
    assert to_column_spec(record(enum_labels=["z", "a", "m"])).enum_labels == ("z", "a", "m")


def test_carries_the_column_comment_as_a_description() -> None:
    column = to_column_spec(record(description="Seller VAT number, not the buyer"))

    assert column.description == "Seller VAT number, not the buyer"


def test_carries_the_database_flags() -> None:
    column = to_column_spec(
        record(has_default=True, is_generated=True, is_identity=True, max_length=50)
    )

    assert (column.has_default, column.is_generated, column.is_identity, column.max_length) == (
        True,
        True,
        True,
        50,
    )
