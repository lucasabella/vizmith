"""BigQuery: a project and a dataset, read through the Python client.

This is the connector that says whether `Dialect` is the right record, which is why it
comes before the one that fits. Three things it does differently, and only the first
needed the record to change.

**Truncation is spelled the other way round.** `DATE_TRUNC(column, MONTH)` rather than
`date_trunc('month', column)`: the arguments reversed and the unit a bare keyword. That is
what `Dialect.truncate` is for, and it is the same move `parameter` already made for the
same reason.

**And BigQuery picks the truncation function by the column's type** — `DATE_TRUNC`,
`DATETIME_TRUNC` and `TIMESTAMP_TRUNC` are three functions — which the builder cannot
choose between, because it knows a column's name and not its type. It does not have to.
The result set contract already fixes what a truncated value is: both shipping sources
answer a truncated date with a *timestamp*, and `test_result_set.py` holds them to it. So
the function is decided by the shape the value has to come back in rather than by the type
the column has, which is one form for every column: `TIMESTAMP_TRUNC` over a cast. A
DATETIME is read as UTC by that cast, which is the same reading the value itself gets when
it comes back without a zone, so nothing about it is new. The cost is a cast on every
truncated column, which BigQuery folds into the scan.

**Its `ARRAY_AGG` raises on a null rather than skipping it**, so the distinct-value
template carries `IGNORE NULLS`. That the templates are templates rather than function
names is what makes this a string in this file instead of a branch in the profiler.

Nothing here is measured. Vizmith has never been run against a real project: the shapes
below are read from vendor documentation, the deterministic tests drive a fake client, and
the live tests skip unless `VIZMITH_BIGQUERY_PROJECT` names one. The two things a
workspace would settle are named where they are relevant — `modified`, and what a profile
costs on a source that bills by bytes scanned rather than by warehouse time.
"""

import threading

from vizmith.catalog import (
    BOOLEAN,
    DATE,
    DECIMAL,
    DECLARED,
    INTEGER,
    STRING,
    TIMESTAMP,
    UNSUPPORTED,
    Column,
    Dialect,
    Relationship,
    Scope,
    Table,
    conform,
)

# BigQuery's standard SQL type names, as INFORMATION_SCHEMA spells them, into the closed
# set. A parameterised type is looked up without its parameters, so NUMERIC(10, 2) is a
# decimal. DATETIME and TIMESTAMP both land on the closed set's timestamp, which is what
# the truncation form below has to survive rather than what it can distinguish.
TYPES = {
    "STRING": STRING,
    "INT64": INTEGER,
    "INTEGER": INTEGER,
    "NUMERIC": DECIMAL,
    "BIGNUMERIC": DECIMAL,
    "DECIMAL": DECIMAL,
    "FLOAT64": DECIMAL,
    "FLOAT": DECIMAL,
    "BOOL": BOOLEAN,
    "BOOLEAN": BOOLEAN,
    "DATE": DATE,
    "DATETIME": TIMESTAMP,
    "TIMESTAMP": TIMESTAMP,
}

# One form of truncation for every temporal column, rather than the three functions
# BigQuery names. See the module docstring: what a truncated value has to be is fixed by
# the result set contract, and it is a timestamp on every source that ships.
TRUNCATE = "TIMESTAMP_TRUNC(CAST({column} AS TIMESTAMP), {unit})"


class BigQueryCatalog:
    dialect = Dialect(
        quote="`",
        approx_distinct="APPROX_COUNT_DISTINCT({column})",
        # IGNORE NULLS because BigQuery's ARRAY_AGG raises on a null element rather than
        # skipping it, so without this every nullable column below the sample threshold —
        # most of them — fails the profiler's second statement.
        distinct_values="ARRAY_AGG(DISTINCT {column} IGNORE NULLS)",
        parameter="@{name}",
        truncate=TRUNCATE,
    )

    def __init__(self, project: str, dataset: str, location: str | None = None):
        self._project = project
        self._dataset = dataset
        self._location = location or None
        self.scope = Scope(levels=("project", "dataset"), values=(project, dataset))
        # A client per thread rather than one shared behind a lock. The profiler runs eight
        # statements at once and the metadata pool sixteen descriptions, and serialising
        # them here would put a network round trip back in a queue the pools exist to
        # empty. The client is documented as safe to share and that is not something this
        # repository has measured, so a thread gets its own rather than the docstring being
        # trusted with somebody's warehouse bill.
        self._local = threading.local()

    def tables(self) -> list[str]:
        rows = self.run(
            f"SELECT table_name FROM {self._region()}.TABLES "
            "WHERE table_schema = @dataset ORDER BY table_name",
            {"dataset": self._dataset},
        )
        return [".".join([*self.scope.values, name]) for (name,) in rows]

    def describe(self, name: str) -> Table:
        """One table's columns and the keys it declares. Two reads of INFORMATION_SCHEMA,
        which is metadata and is not billed by bytes the way a query over the table is."""
        qualified = self.scope.qualify(name)
        table = qualified.rsplit(".", 1)[-1]
        columns = self.run(
            f"SELECT column_name, data_type, is_nullable FROM {self._region()}.COLUMNS "
            "WHERE table_schema = @dataset AND table_name = @table ORDER BY ordinal_position",
            {"dataset": self._dataset, "table": table},
        )
        if not columns:
            raise RuntimeError(f"no table named {qualified} in {'.'.join(self.scope.values)}")
        return Table(
            name=qualified,
            columns=tuple(
                Column(name=column, type=_type(data_type), nullable=nullable == "YES")
                for column, data_type, nullable in columns
            ),
            relationships=self._declared(table),
        )

    def relationships(self) -> list[Relationship]:
        return sorted(
            relationship
            for name in self.tables()
            for relationship in self.describe(name).relationships
        )

    def modified(self, name: str) -> str | None:
        """None, until somebody measures the candidate against a real project.

        `__TABLES__.last_modified_time` moves on DML and is the obvious answer, and
        `INFORMATION_SCHEMA.TABLE_STORAGE` is the other. What disqualifies a candidate is
        not being expensive, it is not moving when the data moves: that is the whole story
        of `DatabricksCatalog.modified`, where the free field tracks the definition and a
        cache keyed on it would never notice a write. BigQuery's documented caveat is the
        streaming buffer — rows inserted through the streaming API may not move the
        timestamp until they are committed to storage — and "mostly moves" is not something
        a cache key can be.

        So this reports None, the profile cache stays off, and every read profiles. That
        is the expensive answer on a source that bills by bytes scanned, and it is the one
        that cannot serve figures that are quietly out of date.
        `tests/test_bigquery.py::test_whether_the_modified_time_moves_when_the_data_changes`
        is the measurement, and it needs a project."""
        return None

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        """Rows in the shapes the result set contract fixes.

        The client answers in Python objects, so `conform` is the whole of the conversion:
        a decimal becomes a float and a zoned timestamp becomes the same instant without a
        zone. BigQuery returns every TIMESTAMP in UTC, so that second one is doing real
        work here rather than being defensive."""
        from google.cloud.bigquery import QueryJobConfig

        job = self._client().query(
            sql,
            job_config=QueryJobConfig(query_parameters=_parameters(parameters or {})),
            location=self._location,
        )
        return [tuple(conform(value) for value in row.values()) for row in job.result()]

    def _declared(self, table: str) -> tuple[Relationship, ...]:
        """The foreign keys this table declares. BigQuery accepts them and does not enforce
        them, which is the same trust a hand-declared Unity Catalog constraint asks for: a
        statement about what somebody wrote rather than about what is true in the data.

        The three views are one join: TABLE_CONSTRAINTS says which constraints are foreign
        keys, KEY_COLUMN_USAGE gives the columns they are on in the order they were
        declared, and CONSTRAINT_COLUMN_USAGE gives what each one points at."""
        rows = self.run(
            "SELECT k.column_name, c.table_name, c.column_name "
            f"FROM {self._region()}.TABLE_CONSTRAINTS AS t "
            f"JOIN {self._region()}.KEY_COLUMN_USAGE AS k "
            "  ON t.constraint_name = k.constraint_name AND t.table_schema = k.table_schema "
            f"JOIN {self._region()}.CONSTRAINT_COLUMN_USAGE AS c "
            "  ON t.constraint_name = c.constraint_name AND t.table_schema = c.table_schema "
            "  AND k.ordinal_position = c.ordinal_position "
            "WHERE t.constraint_type = 'FOREIGN KEY' AND t.table_schema = @dataset "
            "  AND t.table_name = @table "
            "ORDER BY k.ordinal_position",
            {"dataset": self._dataset, "table": table},
        )
        return tuple(
            Relationship(
                ".".join([*self.scope.values, table]),
                child,
                ".".join([*self.scope.values, parent_table]),
                parent,
                kind=DECLARED,
            )
            for child, parent_table, parent in rows
        )

    def _region(self) -> str:
        """Where INFORMATION_SCHEMA is read from. It is per dataset, and reading it through
        the dataset rather than through a region keeps a project with datasets in several
        regions answerable without knowing which one this is in."""
        return f"{self.dialect.quoted(self._project)}.{self.dialect.quoted(self._dataset)}.INFORMATION_SCHEMA"

    def _client(self):
        """This thread's client, built on first use.

        Lazily, so that importing this module or starting a server configured for another
        source reaches no credentials; per thread, for the reason in `__init__`."""
        client = getattr(self._local, "client", None)
        if client is None:
            from google.cloud.bigquery import Client

            client = Client(project=self._project)
            self._local.client = client
        return client


def _parameters(values: dict) -> list:
    """A bound value as the client's own parameter object, with the type it has to declare.

    A whole number is declared as INT64 rather than as narrowly as it fits, which is what
    the Databricks connector has to do because LIMIT there rejects anything wider than an
    INT. BigQuery's LIMIT takes an INT64, so there is nothing to narrow for."""
    from google.cloud.bigquery import ScalarQueryParameter

    return [ScalarQueryParameter(name, _parameter_type(value), value) for name, value in values.items()]


def _parameter_type(value) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    return "STRING"


def _type(data_type: str) -> str:
    """One of BigQuery's type names in the closed set, or unsupported.

    The parameters come off first — NUMERIC(10, 2) is a decimal — and so does the ARRAY or
    STRUCT wrapper's contents, in the sense that neither is in the set at all: a column of
    them is reported unsupported rather than as whatever it holds, because a column that
    cannot be charted should say so."""
    return TYPES.get(data_type.split("(")[0].split("<")[0].strip().upper(), UNSUPPORTED)
