"""Unity Catalog, through the Databricks SDK: the source that ships.

Nothing above `catalog.py` knows this module exists. What it owes is the protocol there —
a listing, a description carrying the keys a table declares, a freshness token, and rows in
the one shape the result set contract fixes — and everything below is how a workspace
answers those questions.
"""

import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

from vizmith.catalog import (
    BOOLEAN,
    DATE,
    DECIMAL,
    DECLARED,
    INTEGER,
    METADATA_WORKERS,
    TIMESTAMP,
    TYPES,
    UNSUPPORTED,
    Column,
    Dialect,
    Relationship,
    Scope,
    Table,
    conform,
)

# How long a statement is waited on before it is given up on and cancelled, in seconds. A
# warehouse that has to start answers pending for minutes, so this is generous rather than
# protective; what it rules out is a wait with no end. See DESIGN.md for the trade.
WAIT_LIMIT = 300


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
        self.scope = Scope(levels=("catalog", "schema"), values=(catalog, schema))

    def tables(self) -> list[str]:
        listed = self._workspace().tables.list(catalog_name=self._catalog, schema_name=self._schema)
        return sorted(table.full_name for table in listed)

    def describe(self, name: str) -> Table:
        return _table(self._workspace().tables.get(self.qualify(name)))

    def relationships(self) -> list[Relationship]:
        """The foreign keys Unity Catalog holds for the configured schema. A lakehouse
        table carries one only where somebody declared it by hand, so this is often
        empty, and what fills the gap is a suggestion a person confirms.

        Every table is read, because a constraint is held on the table that declares it and
        there is nothing to ask for the set of them. That was a loop, so a schema's worth of
        round trips happened one after another for an answer that is nearly all waiting: on
        a hundred and fifty tables it was the larger half of every join path resolved and
        every question asked. They overlap now.

        It is the same read `describe` makes, rather than a second one beside it: a
        description already carries the constraints the response held, so this is those
        descriptions rolled up. That is what makes the read once rather than twice for
        anything holding descriptions in front of this — `Held` above — and it is why a
        graph costs one round trip per table rather than two.

        `tables` runs first and on this thread, which is what builds the client before the
        pool shares it."""
        names = self.tables()
        with ThreadPoolExecutor(max_workers=METADATA_WORKERS) as pool:
            described = pool.map(self.describe, names)
            return sorted(relationship for table in described for relationship in table.relationships)

    def modified(self, name: str) -> str | None:
        """The last time this table's data or definition changed, from `DESCRIBE DETAIL`.

        Unity Catalog's own `updated_at` on a table, which is the same field the
        information schema calls `last_altered`, is not this: Databricks documents that it
        tracks the table's structure and does not move for an insert, an update or a
        delete. It is free to read where this costs a statement, and a cache keyed on it
        would never notice a write, which is the failure this whole key exists to prevent.
        So the more expensive answer is the only correct one.

        A source object `DESCRIBE DETAIL` does not answer for, a view among them, has no
        modified time here rather than a failure: what it means is that the table cannot be
        cached, and a caller is told that by None."""
        detail = f"DESCRIBE DETAIL {self.dialect.qualified(self.qualify(name))}"
        try:
            columns, rows = self._statement(detail)
        except RuntimeError:
            return None
        found = dict(zip(columns, rows[0])) if rows else {}
        modified = found.get("lastModified")
        return None if modified is None else str(modified)

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        return self._statement(sql, parameters)[1]

    def _statement(self, sql: str, parameters: dict | None = None) -> tuple[list[str], list[tuple]]:
        """`run` plus the names the manifest gave the columns, which `run` drops because
        the result set contract is positional. Only this class reads the names, and only to
        find one field of a `DESCRIBE DETAIL` whose column order has changed between
        runtime versions."""
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

        described = response.manifest.schema.columns
        types = [_type(column) for column in described]
        return [column.name for column in described], [
            tuple(_value(v, t) for v, t in zip(row, types)) for row in response.result.data_array or []
        ]

    def qualify(self, name: str) -> str:
        """A name as this source spells it, through the scope every catalog carries.

        The rule used to live here, which made it this connector's to keep and the next
        one's to forget. It is `Scope` now, and this is where the workspace's own calls
        reach it: a `DESCRIBE DETAIL` needs the full name whether the caller wrote one or
        not."""
        return self.scope.qualify(name)

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
    what turns a total back into a number and a month back into a date before either
    reaches a chart. What it turns them into is `SHAPES`, which is the same answer the
    harness gives for the same column.

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
    if kind in (DATE, TIMESTAMP):
        return _temporal(text, kind, type_name)
    return text


def _temporal(text: str, kind: str, type_name: str):
    """A date or a timestamp as an object rather than as the text the API sent.

    A TIMESTAMP arrives with a `Z` on the end where a TIMESTAMP_NTZ arrives without one,
    so this is also where the zone goes: the instant is carried to UTC and stripped of it,
    because the contract has one shape for a timestamp and a zone is not part of it.

    Text the manifest called temporal and Python cannot read is the source failing rather
    than the spec, and it raises the error the API attributes to a source. Returning the
    text would be worse than raising: it puts the shape this exists to remove into a
    result set, and only whatever reads the value much later would notice."""
    try:
        moment = dt.date.fromisoformat(text) if kind == DATE else dt.datetime.fromisoformat(text)
    except ValueError as failure:
        raise RuntimeError(f"the source answered {text!r} for a {type_name} column") from failure
    return conform(moment)


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
    """One table's response as a `Table`, columns and declared keys from the same object.

    A table with no constraints answers with the field absent rather than empty, and a
    workspace that has never had one declared on anything answers that way for every
    table, so the field is read defensively."""
    return Table(
        name=info.full_name,
        columns=tuple(
            Column(
                name=column.name,
                type=TYPES.get(column.type_name.value, UNSUPPORTED),
                nullable=bool(column.nullable),
            )
            for column in info.columns or []
        ),
        relationships=tuple(
            relationship
            for constraint in getattr(info, "table_constraints", None) or []
            for relationship in _foreign_key(info.full_name, constraint)
        ),
    )
