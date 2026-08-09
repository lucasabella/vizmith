import json
import os
import threading
from pathlib import Path

import duckdb
import pytest
from dotenv import load_dotenv
from generate_data import COLUMNS, DATA_DIR, FOREIGN_KEYS, NULLABLE

from vizmith.api import rations
from vizmith.catalog import (
    DATE,
    DECIMAL,
    DECLARED,
    INTEGER,
    STRING,
    TIMESTAMP,
    Column,
    Dialect,
    Relationship,
    Scope,
    Table,
    conform,
)
from vizmith.sources.databricks import DatabricksCatalog
from vizmith.sources.duckdb import DuckDBCatalog

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


# What serialises a statement against the fixture database.
#
# One lock for every catalog rather than one per catalog, which is the correction #166 came
# out of. A DuckDB connection is a single cursor, and the whole suite shares one session
# scoped connection; a lock held on the instance therefore serialises nothing the moment two
# catalogs exist over it, which is what the interface fixture used to build — one per
# request. Two tiles of a dashboard fetch at once, two threads interleaved `execute` and
# `fetchall` on that one cursor, and one of them got the other's rows or none: "No rows to
# draw" for a spec that returns thirty, reproducible about once in four runs.
#
# The shipping catalogs do not have this shape. `source()` in `api.py` builds one catalog
# for the process, and each connector's `run` says it is callable from several threads and
# is what makes that true. So the fault was the double's, and the fix keeps the double
# honest about the promise the interface it stands in for makes.
_STATEMENTS = threading.Lock()


class FixtureCatalog:
    """The catalog the deterministic tests run against: the committed fixture data in
    DuckDB, described without a workspace. Records every statement it is asked to execute,
    which is how the profiler's cost rules are tested."""

    def __init__(self, connection, dialect=DUCKDB, modified="1"):
        self.dialect = dialect
        # The same two levels the workspace has, over the fixture database. Carried rather
        # than assumed: this double used to take the last segment of whatever it was given
        # and rebuild the name, so a spec naming another catalog was answered with this one.
        self.scope = Scope(levels=("catalog", "schema"), values=("vizmith", "shop"))
        self._connection = connection
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
        table = self.scope.qualify(name).rsplit(".", 1)[-1]
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
        return self.modified_times.get(self.scope.qualify(name).rsplit(".", 1)[-1], self._modified)

    def run(self, sql, parameters=None):
        with _STATEMENTS:
            self.statements.append(sql)
            rows = self._connection.execute(sql, parameters or {}).fetchall()
        # DuckDB answers in Python objects, and a decimal column comes back as a Decimal
        # where the shipping catalog gives a float. The harness conforms to the same
        # contract rather than being the one source whose rows read differently, which is
        # the whole point of writing the shapes down. See DESIGN.md.
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


def write_fixture_file(path):
    """The same fixture data in a DuckDB file, with the keys the fixture schema declares
    actually declared.

    The in-memory harness above loads Parquet straight into tables, which carries no
    constraint and reports every column nullable, so it cannot say anything about a
    connector that reads either. This one writes the columns out with their types, their
    nullability and their keys, and then fills them from the same committed Parquet — so
    what `DuckDBCatalog` describes is what the fixture data actually is.

    The file is named for the database inside it, because that is what DuckDB calls an
    attached file, and it is what the two segment names in the fixture specs resolve
    against."""
    parents = {left: (right, key) for left, _, right, key in FOREIGN_KEYS}
    keys = {left: column for left, column, _, _ in FOREIGN_KEYS}
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA IF NOT EXISTS shop")
    # Parents first: DuckDB refuses a foreign key to a table that is not there yet, and
    # the fixture's two keys both point at a table that sorts after the one declaring them.
    for name in sorted(COLUMNS, key=lambda name: name in parents):
        columns = [
            f'"{column}" {type_}' + ("" if (name, column) in NULLABLE else " NOT NULL")
            for column, type_ in COLUMNS[name]
        ]
        if name in parents:
            parent, key = parents[name]
            columns.append(f'FOREIGN KEY ("{keys[name]}") REFERENCES shop."{parent}" ("{key}")')
        # A foreign key needs a unique column to point at, so every table declares its own
        # primary key. That is a fact about the fixture rather than about the connector.
        con.execute(f'CREATE TABLE shop."{name}" (PRIMARY KEY ("id"), {", ".join(columns)})')
        con.execute(f"""INSERT INTO shop."{name}" SELECT * FROM read_parquet('{DATA_DIR / f"{name}.parquet"}')""")
    con.close()
    return path


@pytest.fixture(scope="session")
def duckdb_file(tmp_path_factory):
    """The fixture data as a file a person could point `VIZMITH_DUCKDB_PATH` at."""
    return write_fixture_file(tmp_path_factory.mktemp("duckdb") / "vizmith.duckdb")


@pytest.fixture
def duckdb_catalog(duckdb_file):
    """The shipping DuckDB connector over that file, scoped the way the fixture specs are
    written: a database called vizmith and a schema called shop."""
    return DuckDBCatalog(path=str(duckdb_file), database="vizmith", schema="shop")


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


@pytest.fixture(autouse=True)
def full_rations():
    """Every test starts with its rations untouched.

    The limiter holds one bucket per client for the life of the process, which is what it
    is for and what makes it wrong in a suite: two hundred requests spread over a hundred
    tests are one client emptying a bucket, and the test that happens to be running when it
    empties is the one that fails. Cleared before and after, so a test about the limiter
    leaves nothing behind either."""
    rations.cache_clear()
    yield
    rations.cache_clear()


@pytest.fixture
def catalog(fixture_db):
    return FixtureCatalog(fixture_db)


@pytest.fixture(scope="session")
def live_catalog():
    """The same fixture data, in the workspace, reached the way a user reaches it."""
    return DatabricksCatalog(profile=PROFILE, catalog=CATALOG, schema=SCHEMA, warehouse=WAREHOUSE)
