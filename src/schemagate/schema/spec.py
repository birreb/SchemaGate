import hashlib
import json
from dataclasses import asdict, dataclass


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
    has_default: bool = False
    is_generated: bool = False
    is_identity: bool = False

    @property
    def is_extractable(self) -> bool:
        """Whether a model should be asked to supply this column.

        Generated and identity columns belong to the database. So does a column
        that is `NOT NULL` with a default, since the database already has an
        answer and inventing one can only disagree with it.
        """
        if self.is_generated or self.is_identity:
            return False
        return not (self.has_default and not self.nullable)


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
