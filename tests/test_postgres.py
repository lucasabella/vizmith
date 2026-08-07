"""The PostgreSQL connector, and the two contracts nothing had ever answered None to.

#171 makes the case against this source before it makes the case for it, and the case for
it is these tests rather than the connector: `approx_distinct = None` and `modified() ->
None` are two branches the code has always had and no source has ever taken.

The half of that which does not need Postgres is exercised where it can be run — through
the DuckDB connector with its approximate count taken away, which is the shape the issue
itself suggests. That is what makes "an exact figure is written as exact" a test in CI
rather than a claim about a server nobody has.

Nothing here has been run against a server. The deterministic tests drive a fake connection
and the live tests skip without `VIZMITH_POSTGRES_SCHEMA`.
"""

import datetime as dt
import json
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from vizmith.ask import prompt
from vizmith.catalog import SHAPES, TYPES, UNSUPPORTED
from vizmith.critique import findings
from vizmith.profiler import profile_table
from vizmith.query import execute
from vizmith.sources.postgres import TYPES as POSTGRES_TYPES
from vizmith.sources.postgres import PostgresCatalog, _type

SCHEMA = "shop"
ORDERS = f"{SCHEMA}.orders"

FIXTURES = Path(__file__).parent / "fixtures" / "specs" / "valid"

LIVE_SCHEMA = os.environ.get("VIZMITH_POSTGRES_SCHEMA")
LIVE_SERVICE = os.environ.get("VIZMITH_POSTGRES_SERVICE", "")

needs_server = pytest.mark.skipif(
    not LIVE_SCHEMA, reason="set VIZMITH_POSTGRES_SCHEMA to use a server"
)


class Cursor:
    def __init__(self, connection):
        self._connection = connection
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *failure):
        self._connection.closed.append(self)

    def execute(self, sql, parameters=None):
        self._connection.asked.append((sql, parameters))
        self._rows = self._connection.answer(sql)

    def fetchall(self):
        return self._rows


class FakeConnection:
    """A psycopg connection that answers from a script. A cursor is a context manager
    there, which is how the connector uses one, so it is one here."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked = []
        self.closed = []

    def cursor(self):
        return Cursor(self)

    def execute(self, sql):
        self.asked.append((sql, None))

    def answer(self, sql):
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return rows
        return []


def catalog_with(answers=None):
    catalog = PostgresCatalog(service="work", schema=SCHEMA)
    connection = FakeConnection(answers)
    catalog._local.connection = connection
    return catalog, connection


COLUMNS = [
    ("id", "integer", "NO"),
    ("customer_id", "integer", "NO"),
    ("order_date", "date", "YES"),
    ("status", "character varying", "NO"),
    ("total", "numeric", "NO"),
    ("shipped_at", "timestamp with time zone", "YES"),
]


def test_a_name_is_a_schema_and_a_table_because_a_connection_is_one_database():
    """The first source whose scope is not a pair. A Postgres connection is bound to one
    database and cannot query across them, so the database is a property of the connection
    rather than something a spec resolves against."""
    catalog, connection = catalog_with(
        {"information_schema.tables": [("orders",), ("customers",)], "information_schema.columns": COLUMNS}
    )

    assert catalog.scope.levels == ("schema",)
    assert catalog.tables() == [ORDERS, f"{SCHEMA}.customers"]
    assert catalog.describe("orders").name == ORDERS

    with pytest.raises(ValueError, match="at most schema.table"):
        catalog.describe("otherdb.shop.orders")
    with pytest.raises(ValueError, match="outside the configured schema"):
        catalog.describe("hr.salaries")
    assert [sql for sql, _ in connection.asked if "salaries" in str(sql)] == []


def test_a_description_carries_types_nullability_and_declared_keys():
    catalog, _ = catalog_with(
        {
            "information_schema.columns": COLUMNS,
            "pg_constraint": [("customer_id", "customers", "id")],
        }
    )

    described = catalog.describe("orders")

    assert {c.name: c.type for c in described.columns} == {
        "id": "integer",
        "customer_id": "integer",
        "order_date": "date",
        "status": "string",
        "total": "decimal",
        "shipped_at": "timestamp",
    }
    assert [c.nullable for c in described.columns] == [False, False, True, False, False, True]
    assert [(r.left_table, r.left_column, r.right_table, r.right_column) for r in described.relationships] == [
        (ORDERS, "customer_id", f"{SCHEMA}.customers", "id")
    ]
    assert described.relationships[0].kind == "declared"


def test_a_composite_key_keeps_the_order_the_constraint_pairs_it_in():
    """Two arrays unnested together, which is why this reads pg_catalog rather than the
    information schema: there the pairing has to be inferred."""
    catalog, connection = catalog_with(
        {
            "information_schema.columns": COLUMNS,
            "pg_constraint": [("order_id", "lines", "id"), ("line", "lines", "line")],
        }
    )

    described = catalog.describe("orders")

    assert [(r.left_column, r.right_column) for r in described.relationships] == [
        ("order_id", "id"),
        ("line", "line"),
    ]
    keys = next(sql for sql, _ in connection.asked if "pg_constraint" in sql)
    assert "unnest" in keys and "ORDINALITY" in keys
    assert "parent_schema.nspname = %(schema)s" in keys, "a key out of the schema would be offered"


def test_a_type_is_read_by_the_name_the_information_schema_uses():
    assert _type("character varying") == "string"
    assert _type("timestamp without time zone") == "timestamp"
    assert _type("timestamp with time zone") == "timestamp"
    assert _type("double precision") == "decimal"
    assert _type("jsonb") == UNSUPPORTED
    assert _type("USER-DEFINED") == UNSUPPORTED
    assert set(POSTGRES_TYPES.values()) <= set(SHAPES) <= set(TYPES.values())


def test_a_row_arrives_in_the_shapes_the_contract_fixes():
    zoned = dt.datetime(2024, 1, 1, 2, 30, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    naive = dt.datetime.combine(dt.date(2024, 1, 1), dt.time(0, 30))
    catalog, _ = catalog_with({"SELECT everything": [(Decimal("18.08"), zoned, [Decimal("1.50"), None])]})

    (row,) = catalog.run("SELECT everything")

    assert row == (18.08, naive, [1.5, None])
    assert [type(value) for value in row[2] if value is not None] == [float]


def test_the_session_cannot_write():
    """Vizmith reads. A session that cannot write is one fewer thing between a bug here and
    somebody's database, and it is set once on the connection rather than per statement."""
    catalog = PostgresCatalog(service="work", schema=SCHEMA)
    opened = FakeConnection()

    import psycopg

    original = psycopg.connect
    psycopg.connect = lambda conninfo: opened
    try:
        catalog._connection()
    finally:
        psycopg.connect = original

    assert [sql for sql, _ in opened.asked] == ["SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"]


def test_where_the_connection_points_is_libpqs_business():
    """A service in ~/.pg_service.conf, or the standard PG* environment where none is
    named. Vizmith stores which service and never a password, the same as the Databricks
    profile and the Snowflake connection."""
    seen = []

    import psycopg

    original = psycopg.connect
    psycopg.connect = lambda conninfo: seen.append(conninfo) or FakeConnection()
    try:
        PostgresCatalog(service="work", schema=SCHEMA)._connection()
        PostgresCatalog(service="", schema=SCHEMA)._connection()
    finally:
        psycopg.connect = original

    assert seen == ["service=work", ""]


def test_there_is_no_freshness_token_at_all():
    """The counters are resettable, they lag, and they do not move for a TRUNCATE — and a
    cache that misses a truncate serves a profile describing rows that are gone. relfilenode
    moves on a rewrite and not on ordinary DML, which is the trade Unity Catalog's
    updated_at offers and which this project already refused once."""
    catalog, connection = catalog_with()

    assert catalog.modified(ORDERS) is None
    assert connection.asked == [], "the freshness answer cost a statement"


def exact(catalog):
    """The same source with its approximate distinct count taken away.

    #171's own suggestion, and it is what makes the two paths below testable without a
    server: what `approx_distinct = None` changes is not the connector, it is the profiler,
    the prompt and the assistant."""

    class Exact:
        dialect = replace(catalog.dialect, approx_distinct=None)
        scope = catalog.scope

        def tables(self):
            return catalog.tables()

        def describe(self, name):
            return catalog.describe(name)

        def relationships(self):
            return catalog.relationships()

        def modified(self, name):
            return None

        def run(self, sql, parameters=None):
            return catalog.run(sql, parameters)

    return Exact()


def test_a_source_with_no_approximate_count_pays_for_an_exact_one(duckdb_catalog):
    """The fallback the profiler has always had and no source has ever taken. What it costs
    is the scan the "a profile is cheap by requirement" rule exists to prevent, which on
    this source cannot be kept — so the figure is exact and says so."""
    counted = profile_table(exact(duckdb_catalog), "shipment_scans")
    approximate = profile_table(duckdb_catalog, "shipment_scans")

    codes = next(c for c in counted.columns if c.name == "location_code")
    estimated = next(c for c in approximate.columns if c.name == "location_code")
    assert codes.distinct_count_exact is True
    assert all(not c.distinct_count_exact for c in approximate.columns)
    # The fixture holds 500 location codes and the estimator says 483, which is what an
    # approximate count being approximate looks like and what the exact one has to fix.
    assert codes.distinct_count == 500
    assert estimated.distinct_count != codes.distinct_count


def test_the_exact_count_is_the_one_the_source_was_asked_for(duckdb_catalog, monkeypatch):
    """A count(DISTINCT c) rather than the source's estimator, and one statement still."""
    asked = []
    catalog = exact(duckdb_catalog)
    run = catalog.run
    monkeypatch.setattr(catalog, "run", lambda sql, parameters=None: asked.append(sql) or run(sql, parameters))

    profile_table(catalog, "orders")

    statistics = asked[0]
    assert "count(DISTINCT" in statistics
    assert "approx_count_distinct" not in statistics


def test_an_exact_figure_reaches_the_prompt_as_exact(duckdb_catalog):
    """The line a model reads. A reader who cannot tell a count from an estimate treats a
    guess as a fact, which is why the wording differs at all — and this is the first time
    the exact half of it is produced by a source rather than by a test's own dataclass."""
    counted = profile_table(exact(duckdb_catalog), "shipment_scans")
    approximate = profile_table(duckdb_catalog, "shipment_scans")

    written = prompt("how many scans", [counted], constrained=True)
    estimated = prompt("how many scans", [approximate], constrained=True)

    assert "distinct, approximate" not in written
    assert "distinct" in written
    assert "distinct, approximate" in estimated


def test_the_assistant_stops_saying_approximately_where_the_figure_is_a_count(duckdb_catalog):
    """The same flag reaches the second opinion, which quotes a bound in a finding. A
    number quoted as approximate where it was counted is a hedge nobody asked for."""
    # An arc of five hundred slices, which is the shape the rule refuses and the one where
    # the bound it quotes is the figure this test is about.
    spec = json.loads((FIXTURES / "scans_per_location.json").read_text())
    spec["chart"]["mark"] = "arc"
    counted = profile_table(exact(duckdb_catalog), "shipment_scans")
    approximate = profile_table(duckdb_catalog, "shipment_scans")

    said = " ".join(finding.bound for finding in findings(spec, [counted]))
    hedged = " ".join(finding.bound for finding in findings(spec, [approximate]))

    assert said, "the rule found nothing to say about a chart it refuses"
    assert "approximately" not in said
    assert "approximately" in hedged


@needs_server
@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
def test_every_spec_that_ships_runs_against_a_real_server(path):
    """The bar ROADMAP.md sets for a dialect. It needs the fixture dataset loaded into the
    schema `VIZMITH_POSTGRES_SCHEMA` names, and it is also what settles the parameter style:
    every spec carries a bound row cap."""
    rows = execute(json.loads(path.read_text()), PostgresCatalog(service=LIVE_SERVICE, schema=LIVE_SCHEMA))

    assert rows, f"{path.stem} drew nothing"
    assert all(isinstance(row, dict) for row in rows)


@needs_server
def test_the_declared_keys_of_a_real_schema_are_read():
    """The case a lakehouse never produces: a schema whose foreign keys are enforced and
    present, where `relationships()` returns facts and `suggest()` has almost nothing to
    add. What the Data view says then is the other half of this, and it is in the
    frontend's own tests."""
    catalog = PostgresCatalog(service=LIVE_SERVICE, schema=LIVE_SCHEMA)

    declared = catalog.relationships()

    assert declared, "the schema declares no foreign keys, so this proves nothing"
    assert all(r.kind == "declared" for r in declared)
