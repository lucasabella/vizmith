"""The Snowflake connector, against a fake connection and — where somebody has one — an
account.

#168 says this source proves the least, because it fits: the namespace, the functions and
the truncation spelling are all what the default `Dialect` already says. So what these
tests are for is the four places it does not fit, and the fifth thing that fitting means —
that a spec compiled for Snowflake is the same SQL the other sources get, bar the parameter
markers.

Nothing here has been run against an account. The connector library is real, so a cursor is
driven the way the connector drives one, but the responses are scripted. What an account
would settle is at the bottom, gated on `VIZMITH_SNOWFLAKE_CONNECTION`.
"""

import datetime as dt
import json
import os
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from vizmith.catalog import SHAPES, TYPES, UNSUPPORTED
from vizmith.profiler import profile_table
from vizmith.query import build, execute
from vizmith.sources.snowflake import TYPES as SNOWFLAKE_TYPES
from vizmith.sources.snowflake import SnowflakeCatalog, _type

DATABASE, SCHEMA = "vizmith", "shop"
ORDERS = f"{DATABASE}.{SCHEMA}.orders"

FIXTURES = Path(__file__).parent / "fixtures" / "specs" / "valid"
ORDERS_PER_MONTH = FIXTURES / "orders_per_month.json"

LIVE_CONNECTION = os.environ.get("VIZMITH_SNOWFLAKE_CONNECTION")
LIVE_DATABASE = os.environ.get("VIZMITH_SNOWFLAKE_DATABASE")
LIVE_SCHEMA = os.environ.get("VIZMITH_SNOWFLAKE_SCHEMA")
LIVE_WAREHOUSE = os.environ.get("VIZMITH_SNOWFLAKE_WAREHOUSE")

needs_account = pytest.mark.skipif(
    not (LIVE_CONNECTION and LIVE_DATABASE and LIVE_SCHEMA and LIVE_WAREHOUSE),
    reason="set VIZMITH_SNOWFLAKE_CONNECTION and its database, schema and warehouse",
)

# How long the metadata is given to publish a write before a candidate that has not moved
# is called one that does not move.
MODIFIED_WAIT = 60


class Cursor:
    """A cursor as the connector's own is driven: executed, described, fetched, closed."""

    def __init__(self, connection):
        self._connection = connection
        self.description = []
        self._rows = []
        self.closed = False

    def execute(self, sql, parameters=None):
        self._connection.asked.append((sql, parameters))
        names, self._rows = self._connection.answer(sql)
        self.description = [(name,) for name in names]

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True
        self._connection.closed.append(self)


class FakeConnection:
    """A Snowflake connection that answers from a script and records what it was asked.

    A cursor per statement and never a shared one, because that is what the connector's own
    documentation asks for and what the profiler's eight threads would otherwise break."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked = []
        self.closed = []
        self.cursors = []

    def cursor(self):
        made = Cursor(self)
        self.cursors.append(made)
        return made

    def answer(self, sql):
        for fragment, answer in self.answers.items():
            if fragment in sql:
                return answer
        return [], []


def catalog_with(answers=None):
    catalog = SnowflakeCatalog(
        connection="work", database=DATABASE, schema=SCHEMA, warehouse="compute_wh"
    )
    connection = FakeConnection(answers)
    catalog._client = connection
    return catalog, connection


# What INFORMATION_SCHEMA.COLUMNS answers for the fixture's orders table, in the shape the
# connector reads: name, type, numeric scale, nullability.
COLUMNS = (
    ["column_name", "data_type", "numeric_scale", "is_nullable"],
    [
        ("id", "NUMBER", 0, "NO"),
        ("customer_id", "NUMBER", 0, "NO"),
        ("order_date", "DATE", None, "YES"),
        ("status", "TEXT", None, "NO"),
        ("total", "NUMBER", 2, "NO"),
    ],
)

IMPORTED_KEYS = (
    ["created_on", "pk_table_name", "pk_column_name", "fk_table_name", "fk_column_name", "key_sequence"],
    [(None, "customers", "id", "orders", "customer_id", 1)],
)


def test_the_listing_is_the_configured_schema_in_qualified_names():
    catalog, connection = catalog_with(
        {"information_schema.tables": (["table_name"], [("orders",), ("customers",)])}
    )

    assert catalog.tables() == [ORDERS, f"{DATABASE}.{SCHEMA}.customers"]
    sql, parameters = connection.asked[0]
    assert parameters == {"database": DATABASE, "schema": SCHEMA}
    assert "%(database)s" in sql, "a value was written into the statement"


def test_a_number_is_an_integer_or_a_decimal_by_its_scale():
    """Snowflake has one numeric type, so a row count and a currency amount are both
    NUMBER and only the scale tells them apart. Reading the name alone would draw a count
    as 1234.0."""
    assert _type("NUMBER", 0) == "integer"
    assert _type("NUMBER", 2) == "decimal"
    assert _type("NUMBER(38,0)", 0) == "integer"
    assert _type("TEXT", None) == "string"
    assert _type("TIMESTAMP_NTZ", None) == "timestamp"
    assert _type("TIMESTAMP_TZ", None) == "timestamp"
    assert _type("VARIANT", None) == UNSUPPORTED
    assert _type("GEOGRAPHY", None) == UNSUPPORTED
    assert set(SNOWFLAKE_TYPES.values()) <= set(SHAPES) <= set(TYPES.values())


def test_a_description_carries_types_nullability_and_declared_keys():
    catalog, _ = catalog_with(
        {"information_schema.columns": COLUMNS, "SHOW IMPORTED KEYS": IMPORTED_KEYS}
    )

    described = catalog.describe("orders")

    assert described.name == ORDERS
    assert {c.name: c.type for c in described.columns} == {
        "id": "integer",
        "customer_id": "integer",
        "order_date": "date",
        "status": "string",
        "total": "decimal",
    }
    assert [c.nullable for c in described.columns] == [False, False, True, False, False]
    assert [(r.left_column, r.right_table, r.right_column) for r in described.relationships] == [
        ("customer_id", f"{DATABASE}.{SCHEMA}.customers", "id")
    ]
    assert described.relationships[0].kind == "declared"


def test_a_composite_key_is_read_in_the_order_the_constraint_declares_it():
    """Several rows carrying a key sequence, which is the order the pairs go together in.
    Sorted rather than trusted to arrive in it, because SHOW does not promise an order."""
    catalog, _ = catalog_with(
        {
            "information_schema.columns": COLUMNS,
            "SHOW IMPORTED KEYS": (
                IMPORTED_KEYS[0],
                [
                    (None, "lines", "line", "orders", "line", 2),
                    (None, "lines", "id", "orders", "line_id", 1),
                ],
            ),
        }
    )

    described = catalog.describe("orders")

    assert [(r.left_column, r.right_column) for r in described.relationships] == [
        ("line_id", "id"),
        ("line", "line"),
    ]


def test_a_show_is_read_by_column_name_rather_than_by_position():
    """SHOW answers with a wide row whose columns have moved between releases, so reading
    by position is the difference between a connector that keeps working and one that
    silently pairs the wrong two columns."""
    catalog, _ = catalog_with(
        {
            "information_schema.columns": COLUMNS,
            "SHOW IMPORTED KEYS": (
                # The same answer with two columns inserted in front and the case Snowflake
                # reports a SHOW in.
                ["CREATED_ON", "SOMETHING_NEW", "PK_TABLE_NAME", "PK_COLUMN_NAME",
                 "FK_TABLE_NAME", "FK_COLUMN_NAME", "KEY_SEQUENCE"],
                [(None, "new", "customers", "id", "orders", "customer_id", 1)],
            ),
        }
    )

    described = catalog.describe("orders")

    assert [(r.left_column, r.right_column) for r in described.relationships] == [("customer_id", "id")]


def test_a_table_the_schema_does_not_hold_is_a_failure_naming_it():
    catalog, _ = catalog_with()

    with pytest.raises(RuntimeError, match=f"{DATABASE}.{SCHEMA}.nowhere"):
        catalog.describe("nowhere")


def test_a_name_outside_the_configured_schema_is_refused():
    catalog, connection = catalog_with()

    with pytest.raises(ValueError, match="outside the configured schema"):
        catalog.describe("hr.people.salaries")
    assert connection.asked == [], "an out of scope name reached the account"


def test_a_row_arrives_in_the_shapes_the_contract_fixes():
    """The connector answers in Python objects — a NUMBER is a Decimal and a TIMESTAMP_TZ
    carries a zone — so `conform` is the whole conversion, as on DuckDB."""
    zoned = dt.datetime(2024, 1, 1, 2, 30, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    naive = dt.datetime.combine(dt.date(2024, 1, 1), dt.time(0, 30))
    catalog, _ = catalog_with({"SELECT": ([], [(Decimal("18.08"), zoned, dt.date(2024, 1, 1), "x", 3)])})

    (row,) = catalog.run("SELECT everything")

    assert row == (18.08, naive, dt.date(2024, 1, 1), "x", 3)
    assert row[1].tzinfo is None
    assert [type(value) for value in row] == [float, dt.datetime, dt.date, str, int]


def test_every_statement_gets_its_own_cursor_and_closes_it():
    """The connector documents a connection as safe to share between threads and a cursor
    as not, which is what the profiler's eight and the metadata pool's sixteen need. A
    cursor left open is a cursor leaked per statement."""
    catalog, connection = catalog_with({"SELECT": ([], [(1,)])})

    for _ in range(3):
        catalog.run("SELECT 1")

    assert len(connection.cursors) == 3
    assert all(cursor.closed for cursor in connection.cursors)


def test_a_failed_statement_still_closes_its_cursor():
    catalog, connection = catalog_with()

    class Angry(FakeConnection):
        def answer(self, sql):
            raise RuntimeError("the warehouse is suspended")

    catalog._client = angry = Angry()
    with pytest.raises(RuntimeError, match="suspended"):
        catalog.run("SELECT 1")

    assert angry.cursors and all(cursor.closed for cursor in angry.cursors)
    assert connection.cursors == []


def test_the_connection_is_opened_once_however_many_threads_arrive():
    """The lock is around opening it, not around using it: two threads arriving first must
    not open two connections, and everything after that runs at the width the pools set."""
    catalog = SnowflakeCatalog(connection="work", database=DATABASE, schema=SCHEMA, warehouse="wh")
    opened = []

    def connect(**kwargs):
        opened.append(kwargs)
        return FakeConnection({"SELECT": ([], [(1,)])})

    import snowflake.connector

    original = snowflake.connector.connect
    snowflake.connector.connect = connect
    try:
        threads = [threading.Thread(target=lambda: catalog.run("SELECT 1")) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        snowflake.connector.connect = original

    assert len(opened) == 1
    assert opened[0]["connection_name"] == "work", "the credential is the vendor file's, not ours"
    assert (opened[0]["database"], opened[0]["schema"]) == (DATABASE, SCHEMA)


def test_there_is_no_freshness_token_until_somebody_measures_one():
    """LAST_ALTERED is free and is the same kind of field Unity Catalog's updated_at is,
    which is the documented trap; SYSTEM$LAST_CHANGE_COMMIT_TIME costs a statement and
    claims to track data. Which one moves on an INSERT is a fact about an account."""
    catalog, connection = catalog_with()

    assert catalog.modified(ORDERS) is None
    assert connection.asked == [], "the freshness answer cost a statement"


def test_a_spec_compiles_to_the_same_sql_the_other_sources_get(catalog):
    """What fitting means. Bar the parameter markers, a spec compiled for Snowflake is the
    statement the fixture catalog gets: the same quoting, the same truncation spelling, the
    same aggregate names. That is the whole of #168's argument that this source proves
    least, asserted rather than assumed."""
    snowflake, _ = catalog_with({"information_schema.columns": COLUMNS})
    spec = json.loads(ORDERS_PER_MONTH.read_text())

    theirs, _ = build(spec, snowflake)
    ours, _ = build(spec, catalog)

    assert theirs.replace("%(p0)s", "$p0") == ours
    assert "date_trunc('month', \"vizmith\".\"shop\".\"orders\".\"order_date\")" in theirs


def test_the_profiler_asks_for_what_it_asks_everywhere_else():
    """Two statements per table, the approximate count in the first and the distinct values
    in the second. A null in that list is dropped by the profiler rather than by the
    source's own aggregate, which is what makes three sources answer the same thing."""
    catalog, connection = catalog_with(
        {
            "information_schema.columns": (COLUMNS[0], [("status", "TEXT", None, "YES")]),
            "SHOW IMPORTED KEYS": ([], []),
            "count(*)": ([], [(4, 3, 2)]),
            "ARRAY_AGG": ([], [(["shipped", None, "held"],)]),
        }
    )

    profile = profile_table(catalog, "orders")

    assert profile.columns[0].samples == ("held", "shipped")
    assert profile.columns[0].null_rate == 0.25
    assert "APPROX_COUNT_DISTINCT(\"status\")" in connection.asked[-2][0]
    assert "ARRAY_AGG(DISTINCT \"status\")" in connection.asked[-1][0]


def live() -> SnowflakeCatalog:
    return SnowflakeCatalog(
        connection=LIVE_CONNECTION,
        database=LIVE_DATABASE,
        schema=LIVE_SCHEMA,
        warehouse=LIVE_WAREHOUSE,
    )


@needs_account
@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
def test_every_spec_that_ships_runs_against_a_real_account(path):
    """The bar DESIGN.md sets for a dialect: the one that ships is the only one a user's
    chart depends on. It needs the fixture dataset loaded into the account, which is what
    the schema setting should name.

    It is also what settles the two things the issue marks as unchecked: that `LIMIT` takes
    a bound value here, since every spec carries a row cap, and that a `%` in the parameter
    style is not otherwise significant in these statements."""
    rows = execute(json.loads(path.read_text()), live())

    assert rows, f"{path.stem} drew nothing"
    assert all(isinstance(row, dict) for row in rows)


@needs_account
def test_whether_a_candidate_moves_when_the_data_changes():
    """The measurement `modified` is waiting on. Two candidates, one insert, and the answer
    is which of them moved: anything that does not move on a plain INSERT is disqualified
    as a cache key however cheap it is to read.

    The table is created and dropped here, so nothing in the schema is written to."""
    import time

    catalog = live()
    name = "vizmith_modified_probe"
    quoted = catalog.dialect.qualified(f"{LIVE_DATABASE}.{LIVE_SCHEMA}.{name}")
    altered = (
        "SELECT last_altered FROM information_schema.tables "
        f"WHERE table_schema = '{LIVE_SCHEMA}' AND table_name = '{name.upper()}'"
    )
    committed = f"SELECT SYSTEM$LAST_CHANGE_COMMIT_TIME('{quoted}')"

    catalog.run(f"CREATE OR REPLACE TABLE {quoted} (n NUMBER)")
    try:
        catalog.run(f"INSERT INTO {quoted} VALUES (1)")
        before = (catalog.run(altered)[0][0], catalog.run(committed)[0][0])
        catalog.run(f"INSERT INTO {quoted} VALUES (2)")
        deadline = time.monotonic() + MODIFIED_WAIT
        after = before
        while after == before and time.monotonic() < deadline:
            time.sleep(2)
            after = (catalog.run(altered)[0][0], catalog.run(committed)[0][0])
    finally:
        catalog.run(f"DROP TABLE IF EXISTS {quoted}")

    print(f"\nLAST_ALTERED {before[0]} -> {after[0]}")
    print(f"SYSTEM$LAST_CHANGE_COMMIT_TIME {before[1]} -> {after[1]}")
    assert after != before, (
        "neither candidate moved after an insert, so Snowflake has no freshness token "
        "Vizmith can key a profile on and None is the answer for good"
    )
