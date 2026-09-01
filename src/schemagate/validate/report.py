from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Failure:
    """One thing that did not hold, located precisely enough to act on.

    `column` is None for checks that span a row, such as an arithmetic rule.
    """

    row: int
    column: str | None
    rule: str
    detail: str
    value: str | None = None

    def __str__(self) -> str:
        where = f"row {self.row}" + (f", column {self.column!r}" if self.column else "")
        return f"{where}: {self.detail}"
