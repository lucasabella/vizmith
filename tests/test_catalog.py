import pytest
from conftest import CATALOG, PROFILE, RECORDED, SCHEMA, WAREHOUSE, needs_warehouse
from databricks.sdk.service.catalog import TableInfo

from vizmith.catalog import (
    TYPES,
    UNSUPPORTED,
    Column,
    DatabricksCatalog,
    Table,
    _parameter,
    _parameter_type,
    _table,
    _value,
)

# The recording is what makes the mapping testable without a workspace. Only the live
# test below can tell whether it is still true, and only a person with a workspace can
# refresh it. It lives in conftest because the live tests elsewhere read it too.
FIXTURE_TABLES = [
    "carriers",
    "customers",
    "order_items",
    "orders",
    "products",
    "returns",
    "shipment_scans",
    "shipments",
]


def recorded(name):
    return _table(TableInfo.from_dict(next(t for t in RECORDED if t["full_name"].endswith(f".{name}"))))


def test_the_recording_holds_exactly_the_fixture_tables():
    assert [entry["full_name"] for entry in RECORDED] == [
        f"{CATALOG}.{SCHEMA}.{name}" for name in FIXTURE_TABLES
    ]


@pytest.mark.parametrize("name", FIXTURE_TABLES)
def test_every_column_carries_a_normalised_type(name):
    table = recorded(name)
    assert isinstance(table, Table)
    assert table.columns
    for column in table.columns:
        assert isinstance(column, Column)
        assert column.type in set(TYPES.values()), f"{name}.{column.name} is {column.type}"


def test_nullability_comes_from_the_source():
    """A table created from a query reports every column as nullable, so this asserts
    both cases rather than only that the field is present."""
    orders = {column.name: column.nullable for column in recorded("orders").columns}
    assert orders["order_date"] is True
    assert orders["id"] is False
    assert orders["status"] is False


def test_types_normalise_to_the_closed_set():
    orders = {column.name: column.type for column in recorded("orders").columns}
    scans = {column.name: column.type for column in recorded("shipment_scans").columns}
    assert orders == {
        "id": "integer",
        "customer_id": "integer",
        "order_date": "date",
        "status": "string",
        "total": "decimal",
        "item_count": "integer",
    }
    assert scans["scanned_at"] == "timestamp"


@pytest.mark.parametrize("source_type", ["BINARY", "ARRAY", "STRUCT", "MAP", "INTERVAL", "VARIANT"])
def test_a_type_that_does_not_map_is_reported_as_unsupported(source_type):
    table = _table(
        TableInfo.from_dict(
            {
                "full_name": f"{CATALOG}.{SCHEMA}.probe",
                "columns": [{"name": "value", "type_name": source_type, "nullable": True}],
            }
        )
    )
    assert table.columns[0].type == UNSUPPORTED


def test_a_bound_number_is_declared_as_narrowly_as_it_fits():
    """A row limit is a bound value like any other, and a warehouse rejects a BIGINT
    marker in a LIMIT, so the width is part of what makes the statement run at all."""
    assert _parameter_type(10) == "INT"
    assert _parameter_type(2**40) == "BIGINT"
    assert _parameter_type(True) == "BOOLEAN"
    assert _parameter_type(1.5) == "DOUBLE"
    assert _parameter_type("shipped") == "STRING"
    assert (_parameter(True), _parameter(None), _parameter(10)) == ("true", None, "10")


def test_a_value_comes_back_as_the_type_the_manifest_reported():
    assert _value("123", "LONG") == 123
    assert _value("18.08", "DECIMAL") == 18.08
    assert _value("true", "BOOLEAN") is True
    assert _value("2024-01-01", "DATE") == "2024-01-01"
    assert _value(None, "STRING") is None


def test_a_shorter_name_is_filled_in():
    catalog = DatabricksCatalog(profile="unused", catalog=CATALOG, schema=SCHEMA, warehouse="unused")
    assert catalog.qualify("orders") == f"{CATALOG}.{SCHEMA}.orders"
    assert catalog.qualify(f"{SCHEMA}.orders") == f"{CATALOG}.{SCHEMA}.orders"
    assert catalog.qualify(f"{CATALOG}.{SCHEMA}.orders") == f"{CATALOG}.{SCHEMA}.orders"


@pytest.mark.skipif(not PROFILE, reason="set VIZMITH_DATABRICKS_PROFILE to use a workspace")
def test_the_recording_still_describes_the_workspace():
    catalog = DatabricksCatalog(profile=PROFILE, catalog=CATALOG, schema=SCHEMA, warehouse=WAREHOUSE)
    assert catalog.tables() == [entry["full_name"] for entry in RECORDED]
    for name in FIXTURE_TABLES:
        assert catalog.describe(name) == recorded(name)


@needs_warehouse
def test_a_bound_value_survives_the_warehouse_round_trip():
    """The statement API takes values as text plus a type and answers in text, so this is
    the only test that can say the binding and the types on both sides line up."""
    catalog = DatabricksCatalog(profile=PROFILE, catalog=CATALOG, schema=SCHEMA, warehouse=WAREHOUSE)
    orders = catalog.dialect.qualified(catalog.qualify("orders"))
    matching = catalog.run(f"SELECT count(*) FROM {orders} WHERE `status` = :p0", {"p0": "shipped"})
    quoted = catalog.run(f"SELECT count(*) FROM {orders} WHERE `status` = :p0", {"p0": "' OR 1=1 --"})
    assert isinstance(matching[0][0], int)
    assert matching[0][0] > 0
    assert quoted[0][0] == 0
