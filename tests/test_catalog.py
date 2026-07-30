import json
import os
from pathlib import Path

import pytest
from databricks.sdk.service.catalog import TableInfo

from vizmith.catalog import TYPES, UNSUPPORTED, Column, DatabricksCatalog, Table, _table

# What Unity Catalog answered about the fixture tables, so the mapping is testable
# without a workspace. Only the live test below can tell whether it is still true, and
# only a person with a workspace can refresh it.
RECORDING = Path(__file__).parent / "fixtures" / "catalog" / "tables.json"
RECORDED = json.loads(RECORDING.read_text())

# Set to run the one test that needs a workspace. Everything else runs offline.
PROFILE = os.environ.get("VIZMITH_DATABRICKS_PROFILE")
CATALOG, SCHEMA = RECORDED[0]["full_name"].split(".")[:2]

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


def test_a_shorter_name_is_filled_in():
    catalog = DatabricksCatalog(profile="unused", catalog=CATALOG, schema=SCHEMA)
    assert catalog.qualify("orders") == f"{CATALOG}.{SCHEMA}.orders"
    assert catalog.qualify(f"{SCHEMA}.orders") == f"{CATALOG}.{SCHEMA}.orders"
    assert catalog.qualify(f"{CATALOG}.{SCHEMA}.orders") == f"{CATALOG}.{SCHEMA}.orders"


@pytest.mark.skipif(PROFILE is None, reason="set VIZMITH_DATABRICKS_PROFILE to use a workspace")
def test_the_recording_still_describes_the_workspace():
    catalog = DatabricksCatalog(profile=PROFILE, catalog=CATALOG, schema=SCHEMA)
    assert catalog.tables() == [entry["full_name"] for entry in RECORDED]
    for name in FIXTURE_TABLES:
        assert catalog.describe(name) == recorded(name)
