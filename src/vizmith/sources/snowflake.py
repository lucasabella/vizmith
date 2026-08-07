"""Snowflake: the closest fit to the catalog interface, which is also why it proves least.

A database, a schema and a table; every function the profiler and the builder need, spelled
the way the default `Dialect` already spells them; a `date_trunc` that takes its unit
quoted and first. This connector could have been the Databricks one with the strings
changed, and #168 is right that writing it first would have left the interface exactly as
unvalidated as it went in. It is written after BigQuery for that reason, and what it
inherits from that order is a `Dialect` that already has a truncation field it does not
need to use.

Four things are its own.

**Credentials are not Vizmith's to hold.** The connector reads a named connection from
`~/.snowflake/connections.toml`, which is the same shape the Databricks source takes from
`~/.databrickscfg`: Vizmith stores which connection, and the account, the user and whatever
authenticates them stay in the file the vendor's own tools already write. The one secret
Vizmith's config file holds is still the model key.

**A NUMBER is an integer or a decimal depending on its scale.** Snowflake has one numeric
type with a precision and a scale, so `NUMBER(38,0)` is a count and `NUMBER(10,2)` is
money, and reading the name alone would report every one of them the same way.

**Its declared keys are unenforced.** A `FOREIGN KEY` there is a statement about what
somebody wrote rather than about what is true in the data, which is the same trust a
hand-declared Unity Catalog constraint asks for. `DECLARED` means nobody has to approve it,
and that stays true here, but it is worth knowing it is a claim.

**`modified` is None until somebody measures it.** Two candidates that do not mean the same
thing, and the wrong one is silent: see the method.

Nothing here has been run against an account. The deterministic tests drive a fake client
and the live tests skip without `VIZMITH_SNOWFLAKE_CONNECTION`.
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

# Snowflake's type names as INFORMATION_SCHEMA reports them. NUMBER is absent on purpose:
# it is one type covering both of the closed set's numerics and the scale is what tells
# them apart, which `_type` does rather than this.
TYPES = {
    "TEXT": STRING,
    "VARCHAR": STRING,
    "CHAR": STRING,
    "STRING": STRING,
    "FLOAT": DECIMAL,
    "FLOAT4": DECIMAL,
    "FLOAT8": DECIMAL,
    "DOUBLE": DECIMAL,
    "REAL": DECIMAL,
    "BOOLEAN": BOOLEAN,
    "DATE": DATE,
    "DATETIME": TIMESTAMP,
    "TIMESTAMP_NTZ": TIMESTAMP,
    "TIMESTAMP_LTZ": TIMESTAMP,
    "TIMESTAMP_TZ": TIMESTAMP,
}

NUMBER = "NUMBER"


class SnowflakeCatalog:
    dialect = Dialect(
        quote='"',
        approx_distinct="APPROX_COUNT_DISTINCT({column})",
        distinct_values="ARRAY_AGG(DISTINCT {column})",
        # The Python connector's default paramstyle. Named rather than positional, which is
        # what the field is for, and the reason a `%` in a statement would need escaping is
        # the reason nothing above this writes one: the builder emits no LIKE and no
        # literal percent.
        parameter="%({name})s",
    )

    def __init__(self, connection: str, database: str, schema: str, warehouse: str):
        self._connection_name = connection
        self._warehouse = warehouse
        self.scope = Scope(levels=("database", "schema"), values=(database, schema))
        self._client = None
        # One connection, a cursor per statement. The connector documents the connection as
        # safe to share between threads and a cursor as not, which is exactly the shape the
        # profiler's eight and the metadata pool's sixteen need; the lock is around opening
        # it, so two threads arriving first do not open two.
        self._lock = threading.Lock()

    def tables(self) -> list[str]:
        rows = self.run(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog = %(database)s AND table_schema = %(schema)s "
            "AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name",
            self._where(),
        )
        return [".".join([*self.scope.values, name]) for (name,) in rows]

    def describe(self, name: str) -> Table:
        qualified = self.scope.qualify(name)
        table = qualified.rsplit(".", 1)[-1]
        columns = self.run(
            "SELECT column_name, data_type, numeric_scale, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_catalog = %(database)s AND table_schema = %(schema)s "
            "AND table_name = %(table)s ORDER BY ordinal_position",
            {**self._where(), "table": table},
        )
        if not columns:
            raise RuntimeError(f"no table named {qualified} in {'.'.join(self.scope.values)}")
        return Table(
            name=qualified,
            columns=tuple(
                Column(name=column, type=_type(data_type, scale), nullable=nullable == "YES")
                for column, data_type, scale, nullable in columns
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
        """None, until somebody measures a candidate against a real account.

        There are two and they do not mean the same thing.
        `INFORMATION_SCHEMA.TABLES.LAST_ALTERED` is free to read and is the same kind of
        field Unity Catalog's `updated_at` is — which `DatabricksCatalog.modified` records
        as the trap, because it tracks the definition and a cache keyed on it would never
        notice a write. `SYSTEM$LAST_CHANGE_COMMIT_TIME` is the one that claims to track
        data, and it costs a statement.

        Which of them moves on a plain INSERT is a fact about an account, and the test that
        settles it is one insert and two reads:
        `tests/test_snowflake.py::test_whether_a_candidate_moves_when_the_data_changes`.
        Until it has been run, None is the answer, the profile cache is off, and every read
        profiles. That is more statements than it needs to be and it cannot serve figures
        that are quietly out of date, which is the failure this key exists to prevent."""
        return None

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        """Rows in the shapes the result set contract fixes.

        The connector answers in Python objects — a NUMBER comes back as a Decimal and a
        TIMESTAMP_TZ carries a zone — so `conform` is the whole of the conversion, the same
        as on DuckDB and unlike the statement API's text."""
        cursor = self._snowflake().cursor()
        try:
            cursor.execute(sql, parameters or None)
            return [tuple(conform(value) for value in row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _declared(self, table: str) -> tuple[Relationship, ...]:
        """The foreign keys this table declares, from `SHOW IMPORTED KEYS`.

        `SHOW` rather than `INFORMATION_SCHEMA`, because Snowflake's constraint views name
        a constraint and leave its columns to be joined out of two more, while this answers
        with both sides in one row. It takes no bound values, so the name is quoted into it
        — it is a table name the source itself listed or one the scope has already refused,
        never a value out of a spec.

        A composite key arrives as several rows carrying a key sequence, and they are read
        in that order because that is the order the constraint pairs them in."""
        rows = self._named(f"SHOW IMPORTED KEYS IN TABLE {self.dialect.qualified(self._qualified(table))}")
        found = [
            (
                int(row.get("key_sequence") or 0),
                Relationship(
                    ".".join([*self.scope.values, row["fk_table_name"]]),
                    row["fk_column_name"],
                    ".".join([*self.scope.values, row["pk_table_name"]]),
                    row["pk_column_name"],
                    kind=DECLARED,
                ),
            )
            for row in rows
        ]
        return tuple(relationship for _, relationship in sorted(found, key=lambda pair: pair[0]))

    def _named(self, sql: str) -> list[dict]:
        """One statement whose rows are read by column name rather than by position.

        `SHOW` commands answer with a wide row whose columns have moved between releases,
        so reading `fk_column_name` by name is the difference between a connector that
        keeps working and one that silently pairs the wrong two columns. The names are
        lowercased, because Snowflake reports them uppercased for a `SHOW` and lowercased
        for a query and this reads only the first."""
        cursor = self._snowflake().cursor()
        try:
            cursor.execute(sql)
            names = [column[0].lower() for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _qualified(self, table: str) -> str:
        return ".".join([*self.scope.values, table])

    def _where(self) -> dict:
        database, schema = self.scope.values
        return {"database": database, "schema": schema}

    def _snowflake(self):
        """The connection, opened on first use, from a named connection in the vendor's own
        file. Vizmith stores which connection and never the credential."""
        with self._lock:
            if self._client is None:
                import snowflake.connector

                self._client = snowflake.connector.connect(
                    connection_name=self._connection_name,
                    database=self.scope.values[0],
                    schema=self.scope.values[1],
                    warehouse=self._warehouse,
                )
            return self._client


def _type(data_type: str, scale) -> str:
    """One of Snowflake's type names in the closed set, or unsupported.

    NUMBER is the one that needs its scale. Snowflake has a single numeric type with a
    precision and a scale, so a count and a currency amount are both NUMBER and only the
    scale tells them apart: a scale of zero is the closed set's integer and anything else
    is its decimal. Reading the name alone would report a row count as a decimal, which is
    a chart drawn in `1234.0`."""
    name = data_type.split("(")[0].strip().upper()
    if name == NUMBER:
        return INTEGER if not scale else DECIMAL
    return TYPES.get(name, UNSUPPORTED)
