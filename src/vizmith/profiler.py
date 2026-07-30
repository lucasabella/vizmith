"""Column profiles: what a model gets to see of the data, and all it ever gets to see.

Two statements per table and no more. The first computes every column's statistics in
one pass over the table. The second collects distinct values, and only for the columns
the first one showed to be below the sample threshold, because collecting the distinct
values of a free text column means collecting the whole table.

The threshold is the security boundary. A column above it contributes no sample values
at all, since sample values from a high cardinality column are raw data and raw data
never reaches a prompt.

A sample list is exact while the distinct count next to it usually is not, so the two
can disagree by a value or two on the same column. Whatever writes a profile into a
prompt has to say which of the two it is quoting.

Caching is not built here. When it is, the key is the source's own last modified time
for the table plus the threshold, because lowering the threshold has to silence samples
that a cached profile still holds. The catalog does not report a modified time yet.
"""

import dataclasses
from dataclasses import dataclass
from typing import NamedTuple

from vizmith.catalog import DATE, DECIMAL, INTEGER, TIMESTAMP, UNSUPPORTED, Catalog, Column

# A column with more distinct values than this gets no samples. Low enough that a list
# of values still reads as a vocabulary rather than as data.
SAMPLE_THRESHOLD = 25

# Only these carry a meaningful minimum and maximum. Free text has one and it says
# nothing, booleans have one and it is already in the type.
ORDERED = {INTEGER, DECIMAL, DATE, TIMESTAMP}


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    type: str
    null_rate: float
    distinct_count: int
    distinct_count_exact: bool
    minimum: str | None
    maximum: str | None
    samples: tuple[str, ...]


@dataclass(frozen=True)
class TableProfile:
    table: str
    row_count: int
    columns: tuple[ColumnProfile, ...]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "TableProfile":
        return TableProfile(
            table=data["table"],
            row_count=data["row_count"],
            columns=tuple(
                ColumnProfile(**{**column, "samples": tuple(column["samples"])}) for column in data["columns"]
            ),
        )


def profile_table(catalog: Catalog, name: str, threshold: int = SAMPLE_THRESHOLD) -> TableProfile:
    """A profile of one table. Columns whose type the catalog reports as unsupported are
    left out: a profile describes what can be charted, and `describe` is where the full
    column list lives."""
    table = catalog.describe(name)
    columns = [column for column in table.columns if column.type != UNSUPPORTED]

    row_count, statistics = _statistics(catalog, table.name, columns)
    low_cardinality = [column for column in columns if statistics[column.name].distinct_count <= threshold]
    samples = _samples(catalog, table.name, low_cardinality, threshold)

    profiles = []
    for column in columns:
        figures = statistics[column.name]
        profiles.append(
            ColumnProfile(
                name=column.name,
                type=column.type,
                null_rate=_null_rate(row_count, figures.non_null),
                distinct_count=figures.distinct_count,
                distinct_count_exact=catalog.dialect.approx_distinct is None,
                minimum=figures.minimum,
                maximum=figures.maximum,
                samples=samples.get(column.name, ()),
            )
        )
    return TableProfile(table=table.name, row_count=row_count, columns=tuple(profiles))


class _Statistics(NamedTuple):
    non_null: int
    distinct_count: int
    minimum: str | None
    maximum: str | None


def _statistics(catalog: Catalog, table: str, columns: list[Column]):
    """One query over the table: non null count, distinct count and range per column."""
    dialect = catalog.dialect
    distinct = dialect.approx_distinct or "count(DISTINCT {column})"

    select = ["count(*)"]
    for column in columns:
        quoted = dialect.quoted(column.name)
        select.append(f"count({quoted})")
        select.append(distinct.format(column=quoted))
        if column.type in ORDERED:
            select.append(f"min({quoted})")
            select.append(f"max({quoted})")

    values = iter(catalog.run(f"SELECT {', '.join(select)} FROM {dialect.qualified(table)}")[0])
    row_count = int(next(values))
    statistics = {}
    for column in columns:
        non_null = int(next(values))
        distinct_count = int(next(values))
        minimum, maximum = None, None
        if column.type in ORDERED:
            minimum, maximum = _text(next(values)), _text(next(values))
        statistics[column.name] = _Statistics(non_null, distinct_count, minimum, maximum)
    return row_count, statistics


def _samples(catalog: Catalog, table: str, columns: list[Column], threshold: int):
    """One query for every low cardinality column together, or none if there are none."""
    if not columns:
        return {}

    dialect = catalog.dialect
    select = [dialect.distinct_values.format(column=dialect.quoted(column.name)) for column in columns]
    collected = catalog.run(f"SELECT {', '.join(select)} FROM {dialect.qualified(table)}")[0]

    samples = {}
    for column, values in zip(columns, collected):
        # A distinct count from an approximate function can sit below the threshold while
        # the column sits above it. What came back is then raw data, and it gets dropped
        # rather than trimmed to size, because a trimmed list still leaks values.
        if len(values or []) <= threshold:
            samples[column.name] = tuple(sorted(_text(value) for value in values or []))
    return samples


def _null_rate(row_count: int, non_null: int) -> float:
    """Unrounded, because rounding turns one null in a million rows into a column that
    reports no nulls, and a model reads that rate to decide whether to filter. Rounding
    for display belongs where a profile is written into a prompt."""
    if not row_count:
        return 0.0
    return (row_count - non_null) / row_count


def _text(value) -> str | None:
    """Every figure in a profile is text, because a date, a decimal and a string have to
    survive the same JSON round trip and end up in the same prompt."""
    return None if value is None else str(value)
