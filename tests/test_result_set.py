"""One shape per type, whatever source produced the rows.

The result set contract in DESIGN.md says what a column is called, in what order, and
what a value is. This file is the last of those three: for every type in the catalog's
closed set, the shape a value arrives in, asserted against each catalog that ships.

Every test here runs against each source that ships, because a contract only one source is
checked against is a contract the others are free to break: the fixture harness, the DuckDB
connector over the same rows in a file, and the workspace. The last skips without
credentials, the way the rest of the live suite does.
"""

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from conftest import needs_warehouse

from vizmith.catalog import (
    BOOLEAN,
    DATE,
    DECIMAL,
    INTEGER,
    SHAPES,
    STRING,
    TIMESTAMP,
    TYPES,
)
from vizmith.query import execute

ORDERS_PER_MONTH = Path(__file__).parent / "fixtures" / "specs" / "valid" / "orders_per_month.json"

# One value of every type in the closed set, as an expression over the fixture tables. A
# name in braces is quoted the way the source quotes an identifier, which is what lets one
# statement run against both. Boolean is a comparison rather than a column because the
# fixture data holds no boolean column, and what is being asserted is the shape of a value
# rather than the type of a column.
VALUES = [
    (STRING, "orders", "{status}"),
    (INTEGER, "orders", "{item_count}"),
    (DECIMAL, "orders", "{total}"),
    (BOOLEAN, "orders", "{item_count} > 0"),
    (DATE, "orders", "{order_date}"),
    (TIMESTAMP, "shipments", "{shipped_at}"),
]

# The columns the generator leaves holes in, which is the only place a null can be read
# from without writing one.
NULLABLE = [("orders", "{order_date}"), ("shipments", "{shipped_at}")]


def statement(catalog, table, expression, null=False):
    """One value of an expression, out of the source itself.

    Written against the catalog's dialect rather than as literal SQL, so the same test is
    the one that runs against the warehouse."""
    name = catalog.dialect.qualified(catalog.describe(table).name)
    column = re.sub(r"\{(\w+)\}", lambda found: catalog.dialect.quoted(found[1]), expression)
    held = "" if null else "NOT "
    rows = catalog.run(f"SELECT ({column}) FROM {name} WHERE ({column}) IS {held}NULL LIMIT 1")
    assert rows, f"the fixture data holds no row where {expression} is {'null' if null else 'a value'}"
    return rows[0][0]


def test_every_type_that_can_be_charted_has_a_shape():
    """The closed set and the shapes are one statement in two halves, and a type in the
    first with nothing in the second is a column that can be charted out of a value nobody
    decided the shape of."""
    assert set(SHAPES) == set(TYPES.values())
    assert {kind for kind, _, _ in VALUES} == set(SHAPES), "a type in the closed set is not asserted here"


@pytest.mark.parametrize(("kind", "table", "expression"), VALUES, ids=lambda case: str(case))
def test_the_fixture_catalog_answers_in_the_contracts_shape(kind, table, expression, catalog):
    assert type(statement(catalog, table, expression)) is SHAPES[kind]


@needs_warehouse
@pytest.mark.parametrize(("kind", "table", "expression"), VALUES, ids=lambda case: str(case))
def test_the_duckdb_connector_answers_in_the_contracts_shape(kind, table, expression, duckdb_catalog):
    """The source a person without a workspace configures, over a file rather than over the
    in-memory harness. It answers in Python objects like the harness does, so what this
    holds it to is `conform` rather than a converter."""
    assert type(statement(duckdb_catalog, table, expression)) is SHAPES[kind]


@needs_warehouse
@pytest.mark.parametrize(("kind", "table", "expression"), VALUES, ids=lambda case: str(case))
def test_the_workspace_answers_in_the_contracts_shape(kind, table, expression, live_catalog):
    """The statement API answers in text with the types in its manifest, so this is the
    half of the contract that a converter rather than a client has to keep."""
    assert type(statement(live_catalog, table, expression)) is SHAPES[kind]


@pytest.mark.parametrize(("table", "expression"), NULLABLE, ids=lambda case: str(case))
def test_a_null_is_none_whatever_type_it_is(table, expression, catalog):
    assert statement(catalog, table, expression, null=True) is None


@pytest.mark.parametrize(("table", "expression"), NULLABLE, ids=lambda case: str(case))
def test_a_null_is_none_from_the_duckdb_connector_too(table, expression, duckdb_catalog):
    assert statement(duckdb_catalog, table, expression, null=True) is None


@needs_warehouse
@pytest.mark.parametrize(("table", "expression"), NULLABLE, ids=lambda case: str(case))
def test_a_null_is_none_from_the_workspace_too(table, expression, live_catalog):
    assert statement(live_catalog, table, expression, null=True) is None


def test_a_timestamp_carries_no_zone(catalog):
    """A value that carried one would compare as a different instant against a value that
    did not, and both would look like a timestamp in a result set."""
    value = statement(catalog, "shipments", "{shipped_at}")

    assert value.tzinfo is None


@needs_warehouse
def test_a_timestamp_from_the_workspace_carries_no_zone(live_catalog):
    value = statement(live_catalog, "shipments", "{shipped_at}")

    assert value.tzinfo is None


def test_a_truncated_date_is_a_timestamp(catalog):
    """Truncation is where the two sources have the most room to disagree: it is the one
    place a spec turns a date column into something else, and both sources answer a
    `date_trunc` with a timestamp rather than with the type they were given."""
    rows = execute(json.loads(ORDERS_PER_MONTH.read_text()), catalog)

    assert {type(row["month"]) for row in rows} == {dt.datetime}


def test_a_truncated_date_is_a_timestamp_from_the_duckdb_connector_too(duckdb_catalog):
    rows = execute(json.loads(ORDERS_PER_MONTH.read_text()), duckdb_catalog)

    assert {type(row["month"]) for row in rows} == {dt.datetime}


@needs_warehouse
def test_a_truncated_date_is_a_timestamp_from_the_workspace_too(live_catalog):
    rows = execute(json.loads(ORDERS_PER_MONTH.read_text()), live_catalog)

    assert {type(row["month"]) for row in rows} == {dt.datetime}
