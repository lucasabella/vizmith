import duckdb
import pytest
from generate_data import COLUMNS, DATA_DIR


@pytest.fixture(scope="session")
def fixture_db():
    """The committed Parquet files loaded into a database named vizmith holding one
    schema shop, so that a two or three segment reference in a spec resolves."""
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS vizmith")
    con.execute("CREATE SCHEMA vizmith.shop")
    for name in COLUMNS:
        path = DATA_DIR / f"{name}.parquet"
        con.execute(f"CREATE TABLE vizmith.shop.{name} AS SELECT * FROM read_parquet('{path}')")
    yield con
    con.close()
