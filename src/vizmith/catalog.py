"""What a data source says about itself, in words the rest of Vizmith understands.

Nothing above this module knows that the source is Databricks. A caller gets
qualified names, a closed set of types and nullability, and nothing else.
"""

from dataclasses import dataclass
from typing import Protocol

STRING = "string"
INTEGER = "integer"
DECIMAL = "decimal"
BOOLEAN = "boolean"
DATE = "date"
TIMESTAMP = "timestamp"
UNSUPPORTED = "unsupported"

# Deliberately narrower than the source's type system. A type that is not here is
# reported as unsupported, so a caller can see why a column cannot be charted rather
# than being handed a guess.
TYPES = {
    "STRING": STRING,
    "CHAR": STRING,
    "BYTE": INTEGER,
    "SHORT": INTEGER,
    "INT": INTEGER,
    "LONG": INTEGER,
    "DECIMAL": DECIMAL,
    "FLOAT": DECIMAL,
    "DOUBLE": DECIMAL,
    "BOOLEAN": BOOLEAN,
    "DATE": DATE,
    "TIMESTAMP": TIMESTAMP,
    "TIMESTAMP_NTZ": TIMESTAMP,
}


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]


@dataclass(frozen=True)
class Dialect:
    """The parts of a source's SQL that differ between sources, so that everything above
    writes one query and the source names the pieces. Templates rather than function
    names, because the shape differs too: a set of distinct values is `collect_set(c)`
    here and `array_agg(DISTINCT c)` there. `approx_distinct` is None for a source that
    offers no approximate count, and a caller then pays for an exact one."""

    quote: str
    approx_distinct: str | None
    distinct_values: str

    def quoted(self, identifier: str) -> str:
        return f"{self.quote}{identifier.replace(self.quote, self.quote * 2)}{self.quote}"

    def qualified(self, name: str) -> str:
        return ".".join(self.quoted(segment) for segment in name.split("."))


class Catalog(Protocol):
    dialect: Dialect

    def tables(self) -> list[str]:
        """Qualified names, one per table."""

    def describe(self, name: str) -> Table:
        """A table by qualified name. Fewer segments than the source uses are filled in."""

    def run(self, sql: str) -> list[tuple]:
        """Rows for a statement built above this layer. The Databricks warehouse round
        trip belongs to the query builder, so the fixture catalog in the tests is the
        only implementation today."""


class DatabricksCatalog:
    dialect = Dialect(
        quote="`",
        approx_distinct="approx_count_distinct({column})",
        distinct_values="collect_set({column})",
    )

    def __init__(self, profile: str, catalog: str, schema: str):
        self._profile = profile
        self._catalog = catalog
        self._schema = schema
        self._client = None

    def tables(self) -> list[str]:
        listed = self._workspace().tables.list(catalog_name=self._catalog, schema_name=self._schema)
        return sorted(table.full_name for table in listed)

    def describe(self, name: str) -> Table:
        return _table(self._workspace().tables.get(self.qualify(name)))

    def qualify(self, name: str) -> str:
        segments = name.split(".")
        return ".".join([self._catalog, self._schema][: 3 - len(segments)] + segments)

    def _workspace(self):
        if self._client is None:
            from databricks.sdk import WorkspaceClient

            self._client = WorkspaceClient(profile=self._profile)
        return self._client


def _table(info) -> Table:
    return Table(
        name=info.full_name,
        columns=tuple(
            Column(
                name=column.name,
                type=TYPES.get(column.type_name.value, UNSUPPORTED),
                nullable=bool(column.nullable),
            )
            for column in info.columns
        ),
    )
