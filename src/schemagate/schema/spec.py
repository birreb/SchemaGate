import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class TableRef:
    """A relation a caller could extract into, as offered for selection."""

    schema: str
    name: str
    kind: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column of a target table, as read from `pg_catalog`."""

    name: str
    data_type: str
    nullable: bool
    ordinal: int
    description: str | None = None
    enum_labels: tuple[str, ...] = ()
    max_length: int | None = None
    # Decimal places the column accepts. 0 for integers, s for numeric(p,s), and
    # None where the type declares no scale. Used to resolve numbers whose
    # separator could be grouping or decimal.
    numeric_scale: int | None = None
    has_default: bool = False
    default_expr: str | None = None
    is_generated: bool = False
    is_identity: bool = False

    @property
    def is_extractable(self) -> bool:
        """Whether a document should be asked to supply this column.

        Generated and identity columns belong to the database outright: they
        cannot be written even if a document had a value for them.

        A default is subtler, and treating every default the same was wrong. A
        column defaulting to `now()` or `nextval(...)` holds a value the
        database computes, and a model inventing one can only disagree with it.
        A column defaulting to `'draft'` holds a fallback for when nobody says
        otherwise, and a document that does say otherwise should be heard.
        """
        if self.is_generated or self.is_identity:
            return False
        return not (self.has_default and self.default_is_computed)

    @property
    def default_is_computed(self) -> bool:
        """Whether the default is a value the database makes rather than a fallback.

        A literal starts with a quote, a digit, a sign, or one of the SQL words
        that are values in themselves. Anything else is a function call or a
        keyword like CURRENT_TIMESTAMP. An unrecorded default is treated as
        computed, since guessing the cautious way round costs a column and
        guessing the other way corrupts one.
        """
        if not self.has_default:
            return False
        expression = (self.default_expr or "").strip()
        if not expression:
            return True
        return not (
            expression[0] in "'\"0123456789-+."
            or expression.split("::")[0].strip().lower() in {"true", "false", "null"}
        )


@dataclass(frozen=True, slots=True)
class TableSchema:
    """A target table, in the order its columns are declared."""

    schema: str
    name: str
    columns: tuple[ColumnSpec, ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def ordered(self) -> tuple[ColumnSpec, ...]:
        return tuple(sorted(self.columns, key=lambda column: column.ordinal))

    @property
    def extractable(self) -> tuple[ColumnSpec, ...]:
        return tuple(column for column in self.ordered if column.is_extractable)

    @property
    def fingerprint(self) -> str:
        """A stable digest of everything that affects the compiled model.

        Used as the cache key, so it must survive a restart. `hash()` cannot be
        used here: string hashing is salted per process, which would give every
        restart a fresh key and silently disable the cache.
        """
        payload = {
            "schema": self.schema,
            "name": self.name,
            "columns": [asdict(column) for column in self.ordered],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
