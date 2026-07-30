import duckdb
import pytest
from generate_data import COLUMNS, DATA_DIR, NULLABLE

from vizmith.catalog import DATE, DECIMAL, INTEGER, STRING, TIMESTAMP, Column, Dialect, Table

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

    def __init__(self, connection, dialect=DUCKDB):
        self.dialect = dialect
        self._connection = connection
        self.statements = []

    def tables(self):
        return [f"vizmith.shop.{name}" for name in sorted(COLUMNS)]

    def describe(self, name):
        table = name.rsplit(".", 1)[-1]
        return Table(
            name=f"vizmith.shop.{table}",
            columns=tuple(
                Column(name=column, type=TYPES[type_], nullable=(table, column) in NULLABLE)
                for column, type_ in COLUMNS[table]
            ),
        )

    def run(self, sql, parameters=None):
        self.statements.append(sql)
        return self._connection.execute(sql, parameters or {}).fetchall()


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


@pytest.fixture
def catalog(fixture_db):
    return FixtureCatalog(fixture_db)
