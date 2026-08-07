"""The DuckDB connector, against a file rather than against a double.

The suite has been running through a second implementation of the catalog protocol since
before there was a second source — `FixtureCatalog`, which is a double: it answers from
Python, records what it was asked, and hands back a freshness token so the profile cache
has something to be keyed on. What it cannot say is whether the connector a person
configures reads a real database correctly, because it is not one.

So this file uses the shipping `DuckDBCatalog` over a DuckDB file holding the same
committed fixture rows, with the keys the fixture schema declares actually declared. What
it is for is the two answers this source gives differently from a warehouse — no freshness
token, and foreign keys that are enforced rather than asserted — and the ordinary half:
that every spec the repository ships compiles and runs against it.
"""

import json
from pathlib import Path

import pytest

from vizmith.catalog import DECLARED, SHAPES, TYPES, UNSUPPORTED
from vizmith.profiler import Profiles, profile_table
from vizmith.query import execute
from vizmith.relationships import graph, suggest
from vizmith.sources.duckdb import TYPES as DUCKDB_TYPES
from vizmith.sources.duckdb import DuckDBCatalog, _type

VALID = sorted((Path(__file__).parent / "fixtures" / "specs" / "valid").glob("*.json"))

ORDERS = "vizmith.shop.orders"


def test_the_listing_is_the_configured_schema_in_qualified_names(duckdb_catalog):
    """Three segments, because that is what the rest of Vizmith addresses a table by and
    what a spec's own reference is resolved against."""
    assert duckdb_catalog.tables() == [
        f"vizmith.shop.{name}"
        for name in [
            "carriers",
            "customers",
            "order_items",
            "orders",
            "products",
            "returns",
            "shipment_scans",
            "shipments",
        ]
    ]


def test_a_description_carries_types_from_the_closed_set(duckdb_catalog):
    """The same closed set the warehouse's types map into, which is what lets one renderer
    draw a chart without knowing which source produced the rows."""
    described = duckdb_catalog.describe(ORDERS)

    assert described.name == ORDERS
    assert {column.name: column.type for column in described.columns} == {
        "id": "integer",
        "customer_id": "integer",
        "order_date": "date",
        "status": "string",
        "total": "decimal",
        "item_count": "integer",
    }
    assert all(column.type in set(TYPES.values()) for column in described.columns)


def test_nullability_comes_from_the_source(duckdb_catalog):
    """The fixture file declares NOT NULL where the generator never leaves a hole, so this
    asserts both cases rather than that the field is present. A table loaded from a query
    reports every column nullable, which is what the in-memory harness does and why it
    cannot answer this."""
    orders = {column.name: column.nullable for column in duckdb_catalog.describe(ORDERS).columns}

    assert orders["order_date"] is True
    assert orders["id"] is False
    assert orders["status"] is False


def test_a_width_or_a_precision_does_not_make_a_type_unsupported():
    """`information_schema` spells a decimal with its precision and a string with its
    width, so a set keyed on the spelled-out names would report a column unsupported for
    the number in its brackets."""
    assert _type("DECIMAL(10,2)") == "decimal"
    assert _type("VARCHAR(64)") == "string"
    assert _type("varchar") == "string"
    assert _type("HUGEINT") == UNSUPPORTED
    assert set(DUCKDB_TYPES.values()) <= set(SHAPES)


def test_a_declared_key_is_the_sources_own_word(duckdb_catalog):
    """DuckDB enforces its foreign keys, so a key here is a fact about the data rather
    than a note somebody wrote. `DECLARED` already means nobody has to approve it."""
    declared = duckdb_catalog.relationships()

    assert [(r.left_table, r.left_column, r.right_table, r.right_column) for r in declared] == [
        ("vizmith.shop.order_items", "order_id", ORDERS, "id"),
        (ORDERS, "customer_id", "vizmith.shop.customers", "id"),
    ]
    assert all(r.kind == DECLARED for r in declared)
    assert declared == sorted(declared)


def test_a_description_carries_the_keys_its_own_table_declares(duckdb_catalog):
    """Per table, from the same read that listed the columns, which is what keeps a
    relationship graph one round trip per table rather than two."""
    assert duckdb_catalog.describe(ORDERS).relationships == tuple(
        r for r in duckdb_catalog.relationships() if r.left_table == ORDERS
    )
    assert duckdb_catalog.describe("vizmith.shop.carriers").relationships == ()


def test_the_graph_still_offers_what_the_source_does_not_declare(duckdb_catalog):
    """A source with enforced keys does not make the suggest and confirm path redundant:
    the fixture declares two of its relationships and leaves the rest to inference, which
    is the lakehouse case and is why the Data view exists."""
    described = [duckdb_catalog.describe(name) for name in duckdb_catalog.tables()]
    columns = {t.name: {column.name: column.type for column in t.columns} for t in described}

    known = graph(duckdb_catalog.relationships(), suggest(columns))
    kinds = {f"{r.left_table}.{r.left_column}": r.kind for r in known}

    assert kinds[f"{ORDERS}.customer_id"] == DECLARED
    assert kinds["vizmith.shop.shipments.carrier_id"] == "suggested"


def test_a_short_name_resolves_and_a_name_outside_the_schema_does_not(duckdb_catalog):
    """The scope, through this connector. What it may read is configuration, and a file
    with several databases attached is exactly where that matters."""
    assert duckdb_catalog.describe("orders").name == ORDERS
    assert duckdb_catalog.describe("shop.orders").name == ORDERS

    with pytest.raises(ValueError, match="outside the configured schema"):
        duckdb_catalog.describe("hr.people.salaries")


def test_a_table_the_schema_does_not_hold_is_a_failure_naming_it(duckdb_catalog):
    """RuntimeError rather than an empty description: a table with no columns would reach
    the profiler as a table with nothing to profile, and the person asked for a name that
    is not there."""
    with pytest.raises(RuntimeError, match="vizmith.shop.nowhere"):
        duckdb_catalog.describe("nowhere")


def test_there_is_no_freshness_token_and_the_cache_is_therefore_off(duckdb_catalog, tmp_path):
    """The honest answer, and the one the protocol already documents. `Profiles` never
    stores a profile it cannot key, so a second read profiles again — two statements
    against a local file, which is the case where paying them is nothing. A token invented
    out of the file's own modified time would be a cache that does not notice a row
    changing inside it, which is the failure the key exists to prevent."""
    assert duckdb_catalog.modified(ORDERS) is None

    kept = Profiles(tmp_path / "profiles.json")
    first = kept.read(duckdb_catalog, ORDERS)
    again = kept.read(duckdb_catalog, ORDERS)

    assert first == again
    assert not (tmp_path / "profiles.json").exists(), "a profile was stored against no token"


def test_a_profile_is_the_figures_and_never_a_row(duckdb_catalog):
    """The boundary, through the connector a person configures rather than through the
    double. A column above the sample threshold contributes no values at all."""
    profile = profile_table(duckdb_catalog, "vizmith.shop.shipment_scans")
    columns = {column.name: column for column in profile.columns}

    assert profile.row_count > 0
    assert columns["status"].samples, "a low cardinality column carries its values"
    assert columns["location_code"].samples == (), "a column above the threshold sent values"
    assert columns["scanned_at"].minimum and columns["scanned_at"].maximum


@pytest.mark.parametrize("path", VALID, ids=lambda path: path.stem)
def test_every_spec_that_ships_runs_against_this_source(path, duckdb_catalog):
    """The bar ROADMAP.md sets for a dialect: the one that ships is the only one a user's
    chart depends on. Every valid fixture spec is compiled and run, so a dialect that is
    wrong about a function name fails here rather than in front of somebody."""
    rows = execute(json.loads(path.read_text()), duckdb_catalog)

    assert rows, f"{path.stem} drew nothing"
    assert all(isinstance(row, dict) for row in rows)


def test_the_connection_cannot_write(duckdb_catalog):
    """Vizmith reads. Every statement it builds is a SELECT, so a connection that cannot
    write is one fewer thing between a bug here and somebody's file."""
    with pytest.raises(Exception, match="read.only|read_only"):
        duckdb_catalog.run("CREATE TABLE shop.mistake (id INTEGER)")


def test_the_file_is_opened_on_first_use_and_not_before(tmp_path):
    """Constructing a catalog reaches nothing, which is what lets `/api/health` answer on a
    server whose source is configured wrongly and what keeps `vizmith configure` from
    touching a database."""
    catalog = DuckDBCatalog(path=str(tmp_path / "nothing.duckdb"), database="vizmith", schema="shop")

    assert not (tmp_path / "nothing.duckdb").exists()
    with pytest.raises(Exception, match="nothing.duckdb"):
        catalog.tables()
