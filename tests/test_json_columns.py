import json

import pytest

from schemagate.schema.factory import build_row_model
from schemagate.schema.spec import ColumnSpec, TableSchema
from schemagate.validate.coerce import coerce_rows


def table(*columns: ColumnSpec) -> TableSchema:
    return TableSchema(schema="public", name="events", columns=columns)


def column(data_type: str = "jsonb", **overrides: object) -> ColumnSpec:
    base: dict[str, object] = {"name": "payload", "nullable": True, "ordinal": 1}
    return ColumnSpec(data_type=data_type, **{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("data_type", ["json", "jsonb"])
def test_a_json_column_compiles_to_a_string_field(data_type: str) -> None:
    model = build_row_model(table(column(data_type)))

    assert model.model_fields["payload"].annotation == (str | None), (
        "strict mode cannot express a free-form object, but it can carry one "
        "inside a string, which is the documented way round it"
    )


@pytest.mark.parametrize("data_type", ["json", "jsonb"])
def test_the_model_is_told_to_put_json_in_the_string(data_type: str) -> None:
    schema = build_row_model(table(column(data_type))).model_json_schema()

    assert "JSON" in schema["properties"]["payload"]["description"]


def test_the_generated_schema_stays_strict() -> None:
    schema = build_row_model(table(column())).model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["payload"]["anyOf"][0]["type"] == "string"


def test_an_object_is_parsed_back_out() -> None:
    rows, failures = coerce_rows(({"payload": '{"kind": "order", "lines": 3}'},), table(column()))

    assert not failures
    assert rows[0]["payload"] == {"kind": "order", "lines": 3}


def test_an_array_is_accepted_too() -> None:
    rows, failures = coerce_rows(({"payload": "[1, 2, 3]"},), table(column()))

    assert not failures
    assert rows[0]["payload"] == [1, 2, 3]


def test_json_that_does_not_parse_is_reported() -> None:
    _, failures = coerce_rows(({"payload": "{not json"},), table(column()))

    assert [f.rule for f in failures] == ["type"]


def test_a_bare_scalar_is_refused() -> None:
    _, failures = coerce_rows(({"payload": '"just a string"'},), table(column()))

    assert [f.rule for f in failures] == ["type"], (
        "a json column holding a bare scalar is almost always a mistake, and "
        "Postgres would accept it silently"
    )


def test_the_response_renders_it_as_real_json_not_a_string() -> None:
    from schemagate.api.serialize import to_json_row

    rendered = to_json_row({"payload": {"kind": "order"}})

    assert rendered["payload"] == {"kind": "order"}
    assert json.dumps(rendered)
