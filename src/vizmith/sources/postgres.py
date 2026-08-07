"""PostgreSQL: not the market, and the only source that answers None to two contracts.

#171 makes the case against this connector before it makes the case for it, and the case
against is sound: Postgres is not a lakehouse and not a warehouse, and the argument the
profile-not-rows boundary is built on matters much less when the database is already inside
the building. What earns it a module is that it is the only source that exercises two paths
the code has always had and no source has ever taken.

**There is no approximate distinct count.** Core Postgres has no `APPROX_COUNT_DISTINCT`, so
`approx_distinct` is None and the profiler pays for `count(DISTINCT c)` per column. That
makes `distinct_count_exact` true for the first time against a real source, which reaches
the prompt — a figure is written as "distinct" rather than "distinct, approximate", because
a reader who cannot tell them apart treats a guess as a fact — and reaches the assistant,
which drops ", approximately" from a bound it quotes.

The cost is stated rather than avoided: an exact distinct count per column is the scan the
"a profile is cheap by requirement" rule exists to prevent, and on this source that rule
cannot be kept. `pg_stats.n_distinct` is the free planner statistic somebody will reach for
and this connector does not use it; the reasons are in ROADMAP.md.

**There is no freshness token.** `modified` is None, so the profile cache is off. The
candidates and why each fails are in the method.

Its foreign keys are enforced and usually present, which is the opposite of a lakehouse and
the case the Data view was not written for. What that screen says when the source has
already answered its own question is part of this work rather than a later tidy-up.

Nothing here has been run against a server. The deterministic tests drive a fake connection
and the live tests skip without `VIZMITH_POSTGRES_SCHEMA`.
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

# What `information_schema.columns.data_type` reports, into the closed set. Postgres spells
# a type in words there — "timestamp without time zone" rather than TIMESTAMP_NTZ — so these
# are the spellings that view uses rather than the ones a CREATE TABLE takes.
TYPES = {
    "character varying": STRING,
    "character": STRING,
    "text": STRING,
    "smallint": INTEGER,
    "integer": INTEGER,
    "bigint": INTEGER,
    "numeric": DECIMAL,
    "decimal": DECIMAL,
    "real": DECIMAL,
    "double precision": DECIMAL,
    "boolean": BOOLEAN,
    "date": DATE,
    "timestamp without time zone": TIMESTAMP,
    "timestamp with time zone": TIMESTAMP,
}

# The foreign keys one table declares, with the columns paired in the order the constraint
# declares them. `pg_catalog` rather than `information_schema`, because a composite key is
# two arrays there and unnesting them together is what keeps the pairs together; the
# information schema's own views leave that ordering to be inferred.
#
# A key pointing at a table outside the configured schema is left out. It is a real
# relationship and it is not a usable one: a join through it would name a table this server
# refuses to read, so offering it would be offering a path that cannot be walked.
KEYS = """
SELECT child_column.attname, parent_table.relname, parent_column.attname
FROM pg_constraint AS constraint_
JOIN pg_class AS child_table ON child_table.oid = constraint_.conrelid
JOIN pg_namespace AS child_schema ON child_schema.oid = child_table.relnamespace
JOIN pg_class AS parent_table ON parent_table.oid = constraint_.confrelid
JOIN pg_namespace AS parent_schema ON parent_schema.oid = parent_table.relnamespace
JOIN LATERAL unnest(constraint_.conkey, constraint_.confkey)
     WITH ORDINALITY AS pairs(child_attnum, parent_attnum, ord) ON true
JOIN pg_attribute AS child_column
  ON child_column.attrelid = constraint_.conrelid AND child_column.attnum = pairs.child_attnum
JOIN pg_attribute AS parent_column
  ON parent_column.attrelid = constraint_.confrelid AND parent_column.attnum = pairs.parent_attnum
WHERE constraint_.contype = 'f'
  AND child_schema.nspname = %(schema)s
  AND parent_schema.nspname = %(schema)s
  AND child_table.relname = %(table)s
ORDER BY pairs.ord
"""


class PostgresCatalog:
    dialect = Dialect(
        quote='"',
        # None, and the first source to say so. The profiler falls back to an exact
        # count(DISTINCT c) and marks the figure exact, which is what the prompt then says.
        approx_distinct=None,
        distinct_values="array_agg(DISTINCT {column})",
        parameter="%({name})s",
    )

    def __init__(self, service: str, schema: str):
        self._service = service or None
        # One level, not two. A Postgres connection is bound to one database and cannot
        # query across them, so the database is a property of the connection rather than a
        # setting a spec resolves against, and what a name here may name is a schema and a
        # table. `Scope` already takes a source's own levels; this is the first source
        # whose levels are not a pair.
        self.scope = Scope(levels=("schema",), values=(schema,))
        # A connection per thread. psycopg's own rule is that a connection may not run two
        # statements at once, and the profiler runs eight and the metadata pool sixteen; a
        # lock would put a round trip back into the queue those pools exist to empty. The
        # count is bounded by the pools' widths rather than growing with the schema.
        self._local = threading.local()

    def tables(self) -> list[str]:
        rows = self.run(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %(schema)s AND table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY table_name",
            self._where(),
        )
        return [f"{self.scope.values[0]}.{name}" for (name,) in rows]

    def describe(self, name: str) -> Table:
        qualified = self.scope.qualify(name)
        table = qualified.rsplit(".", 1)[-1]
        columns = self.run(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = %(schema)s AND table_name = %(table)s ORDER BY ordinal_position",
            {**self._where(), "table": table},
        )
        if not columns:
            raise RuntimeError(f"no table named {qualified} in the {self.scope.values[0]} schema")
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
        """None. Postgres has nothing that says when a table's data last changed.

        Two candidates, and both fail the rule the protocol states — a token has to move
        when the data moves, and nothing else about it matters.

        `pg_stat_user_tables`' insert, update and delete counters are a token in exactly the
        sense meant here, since they are only ever compared with the last ones seen. But
        they are reset by `pg_stat_reset()`, they lag the write, and they do not move for a
        `TRUNCATE` — and a cache key that misses a truncate serves a profile describing rows
        that are gone, which is the worst failure this key exists to prevent, because the
        figures still look like figures.

        `pg_class.relfilenode` moves on a rewrite and not on ordinary DML, which is the same
        trade Unity Catalog's `updated_at` offers and which `DatabricksCatalog.modified`
        already refuses.

        So the cache is off and every read profiles. `Profiles` handles that by never
        storing a profile it cannot key, and its own reasoning applies unchanged: keeping
        one forever to save two statements trades a bill for a stale profile, and the stale
        profile is the worse of the two."""
        return None

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        """Rows in the shapes the result set contract fixes.

        psycopg answers in Python objects — a NUMERIC is a Decimal, a `timestamp with time
        zone` carries one, and an `array_agg` is a list — so `conform` is the whole of the
        conversion, and it already recurses into an array because the profiler's samples
        arrive inside one."""
        with self._connection().cursor() as cursor:
            cursor.execute(sql, parameters or None)
            return [tuple(conform(value) for value in row) for row in cursor.fetchall()]

    def _declared(self, table: str) -> tuple[Relationship, ...]:
        rows = self.run(KEYS, {**self._where(), "table": table})
        schema = self.scope.values[0]
        return tuple(
            Relationship(
                f"{schema}.{table}",
                child,
                f"{schema}.{parent_table}",
                parent,
                kind=DECLARED,
            )
            for child, parent_table, parent in rows
        )

    def _where(self) -> dict:
        return {"schema": self.scope.values[0]}

    def _connection(self):
        """This thread's connection, opened on first use.

        Where it points is libpq's own business: a service named in `~/.pg_service.conf`,
        or the standard `PGHOST` and `PGDATABASE` environment where no service is named.
        Vizmith stores which service and never a password, the same way it stores a
        Databricks profile and a Snowflake connection rather than a credential."""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            import psycopg

            connection = psycopg.connect(f"service={self._service}" if self._service else "")
            # Read only, because Vizmith reads: every statement it builds is a SELECT, and a
            # session that cannot write is one fewer thing between a bug here and somebody's
            # database. Set on the session rather than per transaction so it covers
            # everything this connection will ever run.
            connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            self._local.connection = connection
        return connection


def _type(data_type: str) -> str:
    """One of the information schema's type names in the closed set, or unsupported.

    The names are matched whole rather than by prefix: `timestamp without time zone` and
    `timestamp with time zone` are two entries because they are two types, and both are the
    closed set's timestamp — a zone is not part of what a value carries, and `conform`
    strips it."""
    return TYPES.get(data_type.strip().lower(), UNSUPPORTED)
