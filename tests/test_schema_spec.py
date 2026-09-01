import re
import subprocess
import sys
import textwrap

import pytest

from schemagate.schema.spec import ColumnSpec, TableSchema


def column(name: str = "amount", ordinal: int = 1, **overrides: object) -> ColumnSpec:
    defaults: dict[str, object] = {
        "name": name,
        "data_type": "text",
        "nullable": False,
        "ordinal": ordinal,
    }
    return ColumnSpec(**{**defaults, **overrides})  # type: ignore[arg-type]


def table(*columns: ColumnSpec) -> TableSchema:
    return TableSchema(schema="public", name="invoices", columns=tuple(columns))


def test_qualified_name_includes_the_schema() -> None:
    assert table(column()).qualified_name == "public.invoices"


def test_extractable_excludes_generated_columns() -> None:
    schema = table(column("total"), column("total_with_tax", 2, is_generated=True))

    assert [c.name for c in schema.extractable] == ["total"]


def test_extractable_excludes_identity_columns() -> None:
    schema = table(column("id", is_identity=True), column("total", 2))

    assert [c.name for c in schema.extractable] == ["total"]


def test_extractable_excludes_non_nullable_columns_with_a_default() -> None:
    schema = table(column("created_at", has_default=True, nullable=False), column("total", 2))

    assert [c.name for c in schema.extractable] == ["total"]


def test_extractable_keeps_columns_whose_default_is_only_a_fallback() -> None:
    schema = table(column("note", has_default=True, default_expr="''::text", nullable=True))

    assert [c.name for c in schema.extractable] == ["note"], (
        "what decides is where the default comes from, not whether the column is nullable"
    )


def test_extractable_preserves_ordinal_order() -> None:
    schema = table(column("c", 3), column("a", 1), column("b", 2))

    assert [c.name for c in schema.extractable] == ["a", "b", "c"]


def test_fingerprint_is_a_sha256_digest() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", table(column()).fingerprint)


def test_identical_schemas_share_a_fingerprint() -> None:
    assert table(column()).fingerprint == table(column()).fingerprint


def test_fingerprint_changes_when_a_type_changes() -> None:
    before = table(column(data_type="text")).fingerprint
    after = table(column(data_type="numeric")).fingerprint

    assert before != after


def test_fingerprint_changes_when_nullability_changes() -> None:
    before = table(column(nullable=False)).fingerprint
    after = table(column(nullable=True)).fingerprint

    assert before != after


def test_fingerprint_changes_when_enum_labels_change() -> None:
    before = table(column(data_type="status", enum_labels=("draft", "sent"))).fingerprint
    after = table(column(data_type="status", enum_labels=("draft", "sent", "paid"))).fingerprint

    assert before != after


def test_fingerprint_changes_when_a_description_changes() -> None:
    before = table(column(description="Net amount")).fingerprint
    after = table(column(description="Gross amount")).fingerprint

    assert before != after, "descriptions reach the model, so they must invalidate the cache"


def test_fingerprint_changes_when_column_order_changes() -> None:
    before = table(column("a", 1), column("b", 2)).fingerprint
    after = table(column("b", 1), column("a", 2)).fingerprint

    assert before != after


def test_fingerprint_is_stable_across_processes() -> None:
    script = textwrap.dedent("""
        from schemagate.schema.spec import ColumnSpec, TableSchema

        column = ColumnSpec(name="amount", data_type="numeric", nullable=False, ordinal=1)
        print(TableSchema(schema="public", name="invoices", columns=(column,)).fingerprint)
    """)

    runs = {
        subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(2)
    }

    assert len(runs) == 1, "fingerprints must not depend on PYTHONHASHSEED"


# A default is either a value the database computes, or a fallback for when
# nobody supplies one. Only the first belongs exclusively to the database.


def test_a_column_defaulting_to_a_function_belongs_to_the_database() -> None:
    schema = table(column("created_at", has_default=True, default_expr="now()"))

    assert schema.extractable == ()


def test_a_column_defaulting_to_a_literal_may_still_be_supplied() -> None:
    schema = table(column("status", has_default=True, default_expr="'draft'::invoice_status"))

    assert [c.name for c in schema.extractable] == ["status"], (
        "a fallback is not the same as a generated value; the document has a "
        "status and should be allowed to give it"
    )


@pytest.mark.parametrize(
    "expression",
    ["now()", "nextval('invoices_id_seq'::regclass)", "gen_random_uuid()", "CURRENT_TIMESTAMP"],
)
def test_computed_defaults_are_recognised(expression: str) -> None:
    schema = table(column("c", has_default=True, default_expr=expression))

    assert schema.extractable == ()


@pytest.mark.parametrize(
    "expression",
    ["'draft'::invoice_status", "0", "false", "-1", "'ABC'::character varying(32)", "NULL"],
)
def test_literal_defaults_are_recognised(expression: str) -> None:
    schema = table(column("c", has_default=True, default_expr=expression))

    assert [col.name for col in schema.extractable] == ["c"]


def test_a_default_with_no_recorded_expression_is_treated_as_computed() -> None:
    schema = table(column("c", has_default=True, default_expr=None))

    assert schema.extractable == (), "unknown provenance is the cautious case"
