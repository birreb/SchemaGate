from typing import Any

import pytest

from schemagate.errors import UnsupportedColumnTypeError
from schemagate.schema.factory import build_container_model, build_row_model
from schemagate.schema.spec import ColumnSpec, TableSchema


def column(name: str = "value", data_type: str = "text", **overrides: Any) -> ColumnSpec:
    defaults: dict[str, Any] = {"nullable": False, "ordinal": 1}
    return ColumnSpec(name=name, data_type=data_type, **{**defaults, **overrides})


def table(*columns: ColumnSpec, name: str = "invoices") -> TableSchema:
    return TableSchema(schema="public", name=name, columns=tuple(columns))


def annotation_of(data_type: str, **overrides: Any) -> Any:
    model = build_row_model(table(column(data_type=data_type, **overrides)))
    return model.model_fields["value"].annotation


@pytest.mark.parametrize(
    ("data_type", "expected"),
    [
        ("text", str),
        ("varchar", str),
        ("bpchar", str),
        ("uuid", str),
        ("int2", int),
        ("int4", int),
        ("int8", int),
        ("float4", float),
        ("float8", float),
        ("bool", bool),
        ("date", str),
        ("timestamp", str),
        ("timestamptz", str),
    ],
)
def test_maps_postgres_types_to_python(data_type: str, expected: type) -> None:
    assert annotation_of(data_type) is expected


@pytest.mark.parametrize("data_type", ["numeric", "decimal"])
def test_exact_numerics_cross_the_boundary_as_strings(data_type: str) -> None:
    assert annotation_of(data_type) is str, "a float total is a corrupted total"


def test_enum_columns_become_a_literal_of_their_labels() -> None:
    model = build_row_model(
        table(column(data_type="invoice_status", enum_labels=("draft", "sent", "paid")))
    )

    allowed = model.model_json_schema()["properties"]["value"]["enum"]

    assert sorted(allowed) == ["draft", "paid", "sent"]


def test_array_columns_become_lists_of_the_element_type() -> None:
    model = build_row_model(table(column(data_type="_text")))

    assert model.model_fields["value"].annotation == list[str]


@pytest.mark.parametrize("data_type", ["json", "jsonb", "tsvector"])
def test_unsupported_types_are_rejected_by_name(data_type: str) -> None:
    with pytest.raises(UnsupportedColumnTypeError) as caught:
        build_row_model(table(column("payload", data_type)))

    message = str(caught.value)
    assert "payload" in message
    assert data_type in message


def test_nullable_columns_accept_null() -> None:
    model = build_row_model(table(column(nullable=True)))

    assert model(value=None).value is None  # type: ignore[attr-defined]


def test_nullable_columns_are_still_required() -> None:
    schema = build_row_model(table(column(nullable=True))).model_json_schema()

    assert schema["required"] == ["value"], (
        "strict structured output requires every property in `required`; "
        "a nullable column is null, never absent"
    )


def test_every_property_is_required() -> None:
    schema = build_row_model(
        table(column("a", nullable=True), column("b", "int4", nullable=False, ordinal=2))
    ).model_json_schema()

    assert sorted(schema["required"]) == ["a", "b"]


def test_additional_properties_are_forbidden() -> None:
    schema = build_row_model(table(column())).model_json_schema()

    assert schema["additionalProperties"] is False


def test_column_comments_become_field_descriptions() -> None:
    model = build_row_model(table(column(description="Seller VAT number, not the buyer")))

    schema = model.model_json_schema()

    assert schema["properties"]["value"]["description"] == "Seller VAT number, not the buyer"


def test_length_limits_stay_out_of_the_generated_schema() -> None:
    schema = build_row_model(table(column(max_length=50))).model_json_schema()

    assert "maxLength" not in schema["properties"]["value"], (
        "strict mode support for validation keywords is inconsistent across providers; "
        "length is enforced by the validation gate instead"
    )


def test_non_extractable_columns_are_left_out() -> None:
    model = build_row_model(
        table(
            column("id", "int8", is_identity=True),
            column("total", "numeric", ordinal=2),
            column("created_at", "timestamptz", ordinal=3, has_default=True),
        )
    )

    assert list(model.model_fields) == ["total"]


def test_a_table_with_no_extractable_columns_is_rejected() -> None:
    with pytest.raises(UnsupportedColumnTypeError):
        build_row_model(table(column("id", "int8", is_identity=True)))


def test_fields_follow_column_order() -> None:
    model = build_row_model(
        table(column("c", ordinal=3), column("a", ordinal=1), column("b", ordinal=2))
    )

    assert list(model.model_fields) == ["a", "b", "c"]


def test_the_row_model_is_named_after_the_table() -> None:
    assert build_row_model(table(column(), name="invoice_lines")).__name__ == "InvoiceLinesRow"


def test_the_container_holds_a_list_of_rows() -> None:
    schema = table(column())

    container = build_container_model(schema)
    rows = container.model_fields["rows"].annotation

    assert rows == list[build_row_model(schema)]  # type: ignore[misc]


def test_the_container_is_an_object_because_a_bare_array_is_not_valid() -> None:
    schema = build_container_model(table(column())).model_json_schema()

    assert schema["type"] == "object"
    assert schema["required"] == ["rows"]
    assert schema["additionalProperties"] is False


def test_the_container_validates_many_rows() -> None:
    container = build_container_model(table(column("total", "numeric")))

    parsed = container.model_validate({"rows": [{"total": "10.00"}, {"total": "20.00"}]})

    assert [row.total for row in parsed.rows] == ["10.00", "20.00"]  # type: ignore[attr-defined]


def test_a_date_column_tells_the_model_what_shape_it_wants() -> None:
    schema = build_row_model(table(column("issued_on", "date"))).model_json_schema()

    described = schema["properties"]["issued_on"].get("description", "")

    assert "YYYY-MM-DD" in described, (
        "the instructions say copy values verbatim, which protects numbers but "
        "leaves dates in whatever the document used; the column can say better"
    )


def test_the_hint_is_added_to_an_existing_comment_rather_than_replacing_it() -> None:
    spec = column("issued_on", "date", description="Date the supplier issued it")
    schema = build_row_model(table(spec)).model_json_schema()

    described = schema["properties"]["issued_on"]["description"]

    assert described.startswith("Date the supplier issued it")
    assert "YYYY-MM-DD" in described


def test_columns_that_are_not_dates_are_left_alone() -> None:
    schema = build_row_model(table(column("supplier", "text"))).model_json_schema()

    assert "description" not in schema["properties"]["supplier"]
