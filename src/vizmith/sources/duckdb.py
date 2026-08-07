"""DuckDB: a file on this machine, described the same way a warehouse is.

This is the source somebody without a workspace can actually run. It is also the one the
deterministic half of the suite has been exercising all along, in the shape of a test
double, which is the argument #105 makes: the layering stopped being a hypothesis the day
every offline test started running through a second implementation of it.

Two things it answers differently from a warehouse, and both are the interface working
rather than the interface bending.

It has no freshness token. `modified` is None, which the protocol already documents as the
honest answer where a source has none to give, and which `Profiles` already handles by
never storing a profile it could not key. The cost is two statements per table per read,
paid against a local file rather than a billed warehouse, which is the case where paying it
is nothing. Inventing a token out of a file's mtime would key a cache on something that
does not move when a row changes inside the file, which is the failure the whole key exists
to prevent.

Its declared foreign keys are real. DuckDB enforces them, so a key here is a fact about the
data rather than a note somebody wrote, and `relationships.py` needs no telling: a declared
relationship is already a relationship nobody has to approve.
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

# DuckDB's own type names as `information_schema` spells them, into the closed set. A width
# or a precision is dropped before the lookup — DECIMAL(10,2) is a decimal — and a type that
# is not here is reported as unsupported rather than guessed at, exactly as on a warehouse.
# HUGEINT is absent on purpose: it holds values no Python int-to-JSON round trip should be
# asked to carry silently, and a column of them is better refused than truncated.
TYPES = {
    "VARCHAR": STRING,
    "CHAR": STRING,
    "BPCHAR": STRING,
    "TEXT": STRING,
    "TINYINT": INTEGER,
    "SMALLINT": INTEGER,
    "INTEGER": INTEGER,
    "BIGINT": INTEGER,
    "UTINYINT": INTEGER,
    "USMALLINT": INTEGER,
    "UINTEGER": INTEGER,
    "UBIGINT": INTEGER,
    "DECIMAL": DECIMAL,
    "NUMERIC": DECIMAL,
    "REAL": DECIMAL,
    "FLOAT": DECIMAL,
    "DOUBLE": DECIMAL,
    "BOOLEAN": BOOLEAN,
    "DATE": DATE,
    "TIMESTAMP": TIMESTAMP,
    "TIMESTAMP WITH TIME ZONE": TIMESTAMP,
    "TIMESTAMP_NS": TIMESTAMP,
    "TIMESTAMP_MS": TIMESTAMP,
    "TIMESTAMP_S": TIMESTAMP,
}


class DuckDBCatalog:
    dialect = Dialect(
        quote='"',
        approx_distinct="approx_count_distinct({column})",
        distinct_values="array_agg(DISTINCT {column})",
        parameter="${name}",
    )

    def __init__(self, path: str, database: str, schema: str):
        self._path = path
        self.scope = Scope(levels=("database", "schema"), values=(database, schema))
        self._connection = None
        # One DuckDB connection is one cursor, so two threads sharing it read each other's
        # rows. The profiler runs eight statements at once and the metadata pool sixteen
        # descriptions, and the protocol says a source whose client cannot be shared
        # serialises here rather than making the caller ask. Serialising costs nothing that
        # matters: the waiting a pool exists to overlap is a network round trip, and there
        # is no network.
        self._lock = threading.Lock()

    def tables(self) -> list[str]:
        rows = self.run(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog = $database AND table_schema = $schema "
            "AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name",
            self._where(),
        )
        return [".".join([*self.scope.values, name]) for (name,) in rows]

    def describe(self, name: str) -> Table:
        """One table's columns and the keys it declares, from the two catalog views that
        hold them. A name with fewer segments than the source uses is filled in, and one
        outside the configured pair is refused, both by the scope."""
        qualified = self.scope.qualify(name)
        table = qualified.rsplit(".", 1)[-1]
        columns = self.run(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_catalog = $database AND table_schema = $schema AND table_name = $table "
            "ORDER BY ordinal_position",
            {**self._where(), "table": table},
        )
        if not columns:
            raise RuntimeError(f"no table named {qualified} in {'.'.join(self.scope.values)}")
        return Table(
            name=qualified,
            columns=tuple(
                Column(name=column, type=_type(data_type), nullable=nullable in ("YES", True))
                for column, data_type, nullable in columns
            ),
            relationships=self._declared(table),
        )

    def relationships(self) -> list[Relationship]:
        """Every key the schema declares, rolled up from the descriptions, because that is
        where a constraint is held and asking twice is a second read of the same rows."""
        return sorted(
            relationship
            for name in self.tables()
            for relationship in self.describe(name).relationships
        )

    def modified(self, name: str) -> str | None:
        """None, which is what a source with no token to give reports.

        DuckDB has no per-table version that moves when a row changes: a file's own modified
        time moves when anything in the file does and not when a table inside an attached
        read-only database is replaced underneath it, so it is both too coarse and not
        reliable enough to key a profile on. A caller that cannot key a cache must not keep
        one, which `Profiles` already does, so a profile here is built per read. That is two
        statements against a local file rather than two against a billed warehouse."""
        return None

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        """Rows in the shapes the result set contract fixes.

        DuckDB answers in Python objects rather than in text, so `conform` is the whole of
        the conversion: a decimal becomes a float and a zoned timestamp becomes the same
        instant without a zone. The lock is what makes this callable from the pools above."""
        with self._lock:
            rows = self._duckdb().execute(sql, parameters or {}).fetchall()
        return [tuple(conform(value) for value in row) for row in rows]

    def _declared(self, table: str) -> tuple[Relationship, ...]:
        """The foreign keys this table declares, from `duckdb_constraints()`.

        The view reports the constraint as the columns it is on plus the table and columns
        it references, and a composite key is several columns in one constraint, read
        positionally because that is the order it declares them in. A referenced table is
        named without its schema, which is the same schema by construction: DuckDB has no
        cross-schema foreign key."""
        rows = self.run(
            "SELECT constraint_column_names, referenced_table, referenced_column_names "
            "FROM duckdb_constraints() WHERE database_name = $database AND schema_name = $schema "
            "AND table_name = $table AND constraint_type = 'FOREIGN KEY'",
            {**self._where(), "table": table},
        )
        return tuple(
            Relationship(
                ".".join([*self.scope.values, table]),
                child,
                ".".join([*self.scope.values, parent_table]),
                parent,
                kind=DECLARED,
            )
            for children, parent_table, parents in rows
            for child, parent in zip(children, parents)
        )

    def _where(self) -> dict:
        database, schema = self.scope.values
        return {"database": database, "schema": schema}

    def _duckdb(self):
        """The connection, opened on first use. Called under the lock.

        Read only, because Vizmith reads: every statement it builds is a SELECT, and a
        connection that cannot write is one fewer thing standing between a bug here and
        somebody's file. Opened lazily so that importing this module, or starting a server
        configured for a warehouse, does not touch a file at all."""
        if self._connection is None:
            import duckdb

            self._connection = duckdb.connect(self._path, read_only=True)
        return self._connection


def _type(data_type: str) -> str:
    """One of DuckDB's type names in the closed set, or unsupported.

    The width comes off first: `DECIMAL(10,2)` is a decimal and `VARCHAR(64)` is a string,
    and a set keyed on the spelled-out widths would report a column unsupported for the
    number in its brackets."""
    return TYPES.get(data_type.split("(")[0].strip().upper(), UNSUPPORTED)
