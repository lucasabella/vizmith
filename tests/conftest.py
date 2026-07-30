import duckdb
import pytest
from generate_data import COLUMNS, DATA_DIR


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
