"""What a data source says about itself, in words the rest of Vizmith understands.

Nothing above this module knows that the source is Databricks. A caller gets
qualified names, a closed set of types and nullability, and nothing else.
"""

import dataclasses
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

STRING = "string"
INTEGER = "integer"
DECLARED = "declared"
SUGGESTED = "suggested"
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


# How long a statement is waited on before it is given up on and cancelled, in seconds. A
# warehouse that has to start answers pending for minutes, so this is generous rather than
# protective; what it rules out is a wait with no end. See ROADMAP.md for the trade.
WAIT_LIMIT = 300


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]


@dataclass(frozen=True, order=True)
class Relationship:
    """One join a source either states or is thought to hold. Declared means the source's
    own foreign key, which nobody has to approve. Suggested is inferred from names and
    types and is not usable until a person confirms it, because a wrong join produces a
    plausible number rather than an error.

    The left side is the table carrying the key and the right side is the one it points
    at. Ordering is by field, so a list of these sorts the same way twice."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    kind: str = SUGGESTED

    @property
    def key(self) -> str:
        """What identifies this relationship wherever an answer about it is stored. The
        kind is left out: a suggestion that the source later declares is the same join,
        and a confirmation of it should not be lost to that."""
        return f"{self.left_table}.{self.left_column}>{self.right_table}.{self.right_column}"

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Dialect:
    """The parts of a source's SQL that differ between sources, so that everything above
    writes one query and the source names the pieces. Templates rather than function
    names, because the shape differs too: a set of distinct values is `collect_set(c)`
    here and `array_agg(DISTINCT c)` there. `approx_distinct` is None for a source that
    offers no approximate count, and a caller then pays for an exact one. `parameter` is
    how a bound value is referred to in a statement, named rather than positional because
    Unity Catalog's statement execution takes named markers only."""

    quote: str
    approx_distinct: str | None
    distinct_values: str
    parameter: str

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

    def relationships(self) -> list[Relationship]:
        """The relationships the source declares for itself, and only those. What is
        inferred from names and types is not the source's word and is not reported here."""

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        """Rows for a statement built above this layer, with every value bound by name
        rather than written into the statement.

        Callable from several threads at once, because profiling a schema runs several
        tables in parallel and a warehouse round trip is nearly all waiting. A source whose
        client is not safe to share serialises here rather than making the caller ask."""


class DatabricksCatalog:
    dialect = Dialect(
        quote="`",
        approx_distinct="approx_count_distinct({column})",
        distinct_values="collect_set({column})",
        parameter=":{name}",
    )

    def __init__(self, profile: str, catalog: str, schema: str, warehouse: str):
        self._profile = profile
        self._catalog = catalog
        self._schema = schema
        self._warehouse = warehouse
        self._client = None

    def tables(self) -> list[str]:
        listed = self._workspace().tables.list(catalog_name=self._catalog, schema_name=self._schema)
        return sorted(table.full_name for table in listed)

    def describe(self, name: str) -> Table:
        return _table(self._workspace().tables.get(self.qualify(name)))

    def relationships(self) -> list[Relationship]:
        """The foreign keys Unity Catalog holds for the configured schema. A lakehouse
        table carries one only where somebody declared it by hand, so this is often
        empty, and what fills the gap is a suggestion a person confirms."""
        workspace = self._workspace()
        found = []
        for name in self.tables():
            for constraint in getattr(workspace.tables.get(name), "table_constraints", None) or []:
                found.extend(_foreign_key(name, constraint))
        return sorted(found)

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        from databricks.sdk.service.sql import StatementParameterListItem, StatementState

        execution = self._workspace().statement_execution
        started = time.monotonic()
        response = execution.execute_statement(
            statement=sql,
            warehouse_id=self._warehouse,
            wait_timeout="50s",
            parameters=[
                StatementParameterListItem(name=name, value=_parameter(value), type=_parameter_type(value))
                for name, value in (parameters or {}).items()
            ],
        )
        # A statement that outlives the wait comes back pending rather than finished, and a
        # warehouse that has to start does that every time, so the cap is minutes rather
        # than seconds. Past it the statement is cancelled and this gives up, because a
        # query whose caller has gone is billed for as long as it keeps running. The clock
        # starts before the call rather than at the loop: the call blocks for wait_timeout
        # first, and that is part of what the person waited.
        while response.status.state in (StatementState.PENDING, StatementState.RUNNING):
            waited = time.monotonic() - started
            if waited >= WAIT_LIMIT:
                _cancel(execution, response.statement_id)
                raise RuntimeError(f"statement not finished after {waited:.0f} seconds, cancelled")
            time.sleep(1)
            response = execution.get_statement(response.statement_id)

        # An unsuccessful statement still carries a result of None, and returning no rows
        # for it would read as an empty answer rather than as a failure.
        if response.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"statement {response.status.state}: {response.status.error}")
        # Rows past the first chunk are fetched separately, so returning that chunk would
        # answer with a page and call it the result. A spec's row cap keeps a chart query
        # well inside one chunk, and a profile is a single row.
        if response.manifest.truncated or response.manifest.total_chunk_count > 1:
            raise RuntimeError("statement returned more rows than one chunk holds")

        types = [_type(column) for column in response.manifest.schema.columns]
        return [tuple(_value(v, t) for v, t in zip(row, types)) for row in response.result.data_array or []]

    def qualify(self, name: str) -> str:
        segments = name.split(".")
        return ".".join([self._catalog, self._schema][: 3 - len(segments)] + segments)

    def _workspace(self):
        if self._client is None:
            from databricks.sdk import WorkspaceClient

            self._client = WorkspaceClient(profile=self._profile)
        return self._client


def _cancel(execution, statement_id: str) -> None:
    """Stop a statement nobody is left to read the answer of.

    Best effort, because the caller is already being told that the statement did not
    finish and a source that will not take the cancellation cannot change that. Raising
    here would replace a message about waiting with one about cancelling, which sends the
    person to the wrong thing."""
    with suppress(Exception):
        execution.cancel_execution(statement_id)


def _parameter(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parameter_type(value) -> str:
    """A statement takes its values as text plus a declared type, so a number compared
    against a numeric column has to say so or it arrives as a string. A whole number is
    declared as small as it fits, because a row limit is one of these and LIMIT rejects
    anything wider than an INT. A comparison widens the other way by itself."""
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT" if -(2**31) <= value < 2**31 else "BIGINT"
    if isinstance(value, float):
        return "DOUBLE"
    return "STRING"


def _type(column) -> str:
    """The manifest names a column's type twice and not always in both places: for a
    TIMESTAMP_NTZ the SDK's enum is absent while the text says what it is. The text is
    the fallback rather than the primary, because it carries a decimal's precision and
    scale and the enum does not."""
    return column.type_name.value if column.type_name else column.type_text


def _value(text: str | None, type_name: str):
    """Rows come back as text with the types in the manifest, so the source's own type is
    what turns a total back into a number before it reaches a chart.

    An array arrives as the JSON text of one. It is handled here rather than in `TYPES`
    because that set says which column types can be charted, and an array still cannot be.
    Only the profiler's sample query returns one, and it needs the values rather than a
    string that happens to contain them: counting the characters of that string is how a
    four value column ends up looking like a thirty character one."""
    if text is None:
        return None
    if type_name == "ARRAY":
        return json.loads(text)
    kind = TYPES.get(type_name, UNSUPPORTED)
    if kind == INTEGER:
        return int(text)
    if kind == DECIMAL:
        return float(text)
    if kind == BOOLEAN:
        return text == "true"
    return text


def _foreign_key(table: str, constraint) -> list[Relationship]:
    """One constraint as one relationship per column pair. A composite key joins on
    several columns at once, and the pairs are read positionally because that is the
    order the constraint declares them in."""
    foreign_key = getattr(constraint, "foreign_key_constraint", None)
    if foreign_key is None:
        return []
    return [
        Relationship(table, child, foreign_key.parent_table, parent, kind=DECLARED)
        for child, parent in zip(foreign_key.child_columns, foreign_key.parent_columns)
    ]


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
