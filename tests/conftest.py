import json
import os
import threading
from pathlib import Path

import duckdb
import pytest
from dotenv import load_dotenv
from generate_data import COLUMNS, DATA_DIR, FOREIGN_KEYS, NULLABLE

from vizmith.catalog import (
    DATE,
    DECIMAL,
    DECLARED,
    INTEGER,
    STRING,
    TIMESTAMP,
    Column,
    DatabricksCatalog,
    Dialect,
    Relationship,
    Table,
    conform,
)

# The same .env the serve command reads, so a workspace that is configured for running the
# application is also configured for testing against it. Without one the live tests skip
# and the suite stays offline, which is what a checkout with no credentials gets.
load_dotenv()

# What Unity Catalog answered about the fixture tables. Live tests take their credentials
# from the environment but their catalog and schema from here, so a .env pointed at other
# data cannot quietly send them somewhere the fixture specs do not describe.
RECORDING = Path(__file__).parent / "fixtures" / "catalog" / "tables.json"
RECORDED = json.loads(RECORDING.read_text())
CATALOG, SCHEMA = RECORDED[0]["full_name"].split(".")[:2]

PROFILE = os.environ.get("VIZMITH_DATABRICKS_PROFILE")
WAREHOUSE = os.environ.get("VIZMITH_DATABRICKS_WAREHOUSE")

# Empty rather than absent, because .env.example ships the names with no values and a
# copy of it that was never filled in should skip rather than fail on authentication.
needs_warehouse = pytest.mark.skipif(
    not PROFILE or not WAREHOUSE,
    reason="set VIZMITH_DATABRICKS_PROFILE and VIZMITH_DATABRICKS_WAREHOUSE to use a warehouse",
)

DUCKDB = Dialect(
    quote='"',
    approx_distinct="approx_count_distinct({column})",
    distinct_values="array_agg(DISTINCT {column})",
    parameter="${name}",
)

TYPES = {
    "INTEGER": INTEGER,
    "VARCHAR": STRING,
    "DATE": DATE,
    "TIMESTAMP": TIMESTAMP,
    "DECIMAL(10,2)": DECIMAL,
}


class FixtureCatalog:
    """The catalog the deterministic tests run against: the committed fixture data in
    DuckDB, described without a workspace. Records every statement it is asked to execute,
    which is how the profiler's cost rules are tested."""

    def __init__(self, connection, dialect=DUCKDB, modified="1"):
        self.dialect = dialect
        self._connection = connection
        # A DuckDB connection is one cursor, so two threads sharing it read each other's
        # rows. Profiling a schema calls run in parallel, so the double serialises rather
        # than being the only catalog that cannot be shared.
        self._lock = threading.Lock()
        self.statements = []
        # What this catalog says about when each table last changed, by short name. A test
        # writes one to make a table look rewritten. `modified=None` is a source with no
        # modified time to give at all, which is the case a profile must not be cached in.
        self._modified = modified
        self.modified_times = {}
        # Every table this was asked the modified time of. On the shipping catalog that
        # question is a DESCRIBE DETAIL the warehouse runs and bills for, so a request that
        # asks it per table has a cost this records and `statements` does not.
        self.freshness_checks = []
        # Every table this was asked to describe. A metadata read rather than a statement,
        # and the thing a relationship graph is made of, so a request that rebuilds the
        # graph per gesture has a cost only this records.
        self.described = []

    def tables(self):
        return [f"vizmith.shop.{name}" for name in sorted(COLUMNS)]

    def describe(self, name):
        """The table's columns and the keys it declares, together, as a source answers
        them: the shipping catalog reads both off one response, and a double that answered
        them separately would let the application ask twice without a test noticing."""
        table = name.rsplit(".", 1)[-1]
        self.described.append(f"vizmith.shop.{table}")
        return Table(
            name=f"vizmith.shop.{table}",
            columns=tuple(
                Column(name=column, type=TYPES[type_], nullable=(table, column) in NULLABLE)
                for column, type_ in COLUMNS[table]
            ),
            relationships=tuple(
                Relationship(
                    f"vizmith.shop.{left}", column, f"vizmith.shop.{right}", key, kind=DECLARED
                )
                for left, column, right, key in FOREIGN_KEYS
                if left == table
            ),
        )

    def relationships(self):
        """What the fixture schema declares. Two of them, so that a test can tell a
        declared relationship from a suggested one rather than finding every pair
        reported the same way. Rolled up from the descriptions, which is where the
        constraints are held on a source and how the shipping catalog answers this."""
        return sorted(
            relationship
            for name in self.tables()
            for relationship in self.describe(name).relationships
        )

    def modified(self, name):
        """Not recorded as a statement, because on a real source this is a metadata read
        rather than a pass over the table, and what `statements` exists to count is what a
        profile costs to build."""
        self.freshness_checks.append(name)
        return self.modified_times.get(name.rsplit(".", 1)[-1], self._modified)

    def run(self, sql, parameters=None):
        with self._lock:
            self.statements.append(sql)
            rows = self._connection.execute(sql, parameters or {}).fetchall()
        # DuckDB answers in Python objects, and a decimal column comes back as a Decimal
        # where the shipping catalog gives a float. The harness conforms to the same
        # contract rather than being the one source whose rows read differently, which is
        # the whole point of writing the shapes down. See ROADMAP.md.
        return [tuple(conform(value) for value in row) for row in rows]


def shapes(rows):
    """What a result set holds, per column, as the set of types its values have. Nulls are
    left out, since a null is None whatever the column is. Two sources answering one spec
    have to agree on this, and comparing the rows themselves would instead be comparing
    two copies of the fixture data."""
    return {
        name: {type(row[name]) for row in rows if row[name] is not None} for name in (rows[0] if rows else {})
    }


def load_fixture_db():
    """The committed Parquet files loaded into a database named vizmith holding one
    schema shop, so that a two or three segment reference in a spec resolves."""
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS vizmith")
    con.execute("CREATE SCHEMA vizmith.shop")
    for name in COLUMNS:
        path = DATA_DIR / f"{name}.parquet"
        con.execute(f"CREATE TABLE vizmith.shop.{name} AS SELECT * FROM read_parquet('{path}')")
    return con


@pytest.fixture(scope="session")
def fixture_db():
    con = load_fixture_db()
    yield con
    con.close()


@pytest.fixture(scope="session")
def other_fixture_db():
    """A second database over the same files, so a determinism test compares two runs
    rather than two queries against one connection."""
    con = load_fixture_db()
    yield con
    con.close()


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """A state directory per test. The server writes two files there, the profile cache
    and the relationship answers, and both default to a home directory. Autouse rather
    than opt in, because a test that forgets it does not fail: it writes to the home
    directory of whoever ran the suite and reads back what the last run left."""
    directory = tmp_path / "state"
    monkeypatch.setenv("VIZMITH_STATE_DIR", str(directory))
    return directory


@pytest.fixture
def catalog(fixture_db):
    return FixtureCatalog(fixture_db)


@pytest.fixture(scope="session")
def live_catalog():
    """The same fixture data, in the workspace, reached the way a user reaches it."""
    return DatabricksCatalog(profile=PROFILE, catalog=CATALOG, schema=SCHEMA, warehouse=WAREHOUSE)
