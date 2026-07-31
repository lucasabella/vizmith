from types import SimpleNamespace

import pytest
from conftest import CATALOG, PROFILE, RECORDED, SCHEMA, WAREHOUSE, needs_warehouse
from databricks.sdk.service.catalog import TableInfo
from databricks.sdk.service.sql import StatementState

from vizmith import catalog as catalog_module
from vizmith.catalog import (
    TIMESTAMP,
    TYPES,
    UNSUPPORTED,
    WAIT_LIMIT,
    Column,
    DatabricksCatalog,
    Table,
    _parameter,
    _parameter_type,
    _table,
    _type,
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


def manifest_column(type_name, type_text):
    """A column as the statement manifest describes one, with the two ways it names a
    type. The SDK fills in one, the other or both."""
    enum = SimpleNamespace(value=type_name) if type_name else None
    return SimpleNamespace(type_name=enum, type_text=type_text)


def test_a_column_the_sdk_leaves_untyped_is_read_from_the_manifest_text():
    """A TIMESTAMP_NTZ arrives with no enum, and reading the enum anyway crashed every
    statement that returned one, which is every profile of a table with a timestamp."""
    column = manifest_column(None, "TIMESTAMP_NTZ")

    assert _type(column) == "TIMESTAMP_NTZ"
    assert TYPES[_type(column)] == TIMESTAMP


def test_a_column_the_sdk_does_type_is_read_from_the_enum():
    """The text carries a decimal's precision and scale, which the closed set has no entry
    for, so the enum stays the primary."""
    assert _type(manifest_column("DECIMAL", "DECIMAL(10,2)")) == "DECIMAL"


def test_an_array_arrives_as_its_values_rather_than_as_their_text():
    """collect_set answers with an array and the statement API sends the JSON of one, so
    without this the sample threshold counts characters and the samples are punctuation."""
    assert _value('["normal","high","low","urgent"]', "ARRAY") == ["normal", "high", "low", "urgent"]
    assert _value("[]", "ARRAY") == []


def test_an_array_column_is_still_not_chartable():
    """Reading an array out of a result set says nothing about charting one, and the
    closed set is what decides that."""
    assert TYPES.get("ARRAY", UNSUPPORTED) == UNSUPPORTED


def test_a_shorter_name_is_filled_in():
    catalog = DatabricksCatalog(profile="unused", catalog=CATALOG, schema=SCHEMA, warehouse="unused")
    assert catalog.qualify("orders") == f"{CATALOG}.{SCHEMA}.orders"
    assert catalog.qualify(f"{SCHEMA}.orders") == f"{CATALOG}.{SCHEMA}.orders"
    assert catalog.qualify(f"{CATALOG}.{SCHEMA}.orders") == f"{CATALOG}.{SCHEMA}.orders"


class Clock:
    """Time as the polling loop reads it. A sleep advances it instead of taking it, so a
    test can sit out the whole cap without waiting for it."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeExecution:
    """The statement execution API, answering with one state per call and repeating the
    last one, which is how a statement that never finishes is expressed. A cancellation is
    recorded rather than performed. A warehouse cannot be made to hang on demand, so this
    is the only way to test the wait at all."""

    def __init__(self, *states, cancel_raises=False):
        self._states = list(states)
        self._cancel_raises = cancel_raises
        self.cancelled = []
        self.polls = 0

    def execute_statement(self, **arguments):
        self.statement = arguments.get("statement")
        return self._response()

    def get_statement(self, statement_id):
        self.polls += 1
        return self._response()

    def cancel_execution(self, statement_id):
        if self._cancel_raises:
            raise RuntimeError("the source would not take the cancellation")
        self.cancelled.append(statement_id)

    def _response(self):
        state = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        return SimpleNamespace(
            statement_id="statement-1",
            status=SimpleNamespace(state=state, error="[NO_ERROR] none"),
            manifest=SimpleNamespace(
                truncated=False,
                total_chunk_count=1,
                schema=SimpleNamespace(columns=[manifest_column("LONG", "BIGINT")]),
            ),
            result=SimpleNamespace(data_array=[["7"]]),
        )


def waiting(monkeypatch, *states, cancel_raises=False):
    """A catalog whose statements answer with the given states, over a clock that costs
    nothing to advance. No workspace is built and no warehouse is reached."""
    monkeypatch.setattr(catalog_module, "time", Clock())
    execution = FakeExecution(*states, cancel_raises=cancel_raises)
    catalog = DatabricksCatalog(profile="unused", catalog=CATALOG, schema=SCHEMA, warehouse="unused")
    catalog._client = SimpleNamespace(statement_execution=execution)
    return catalog, execution


def states(pending, final):
    return [StatementState.PENDING] * pending + [final]


def test_a_statement_that_never_finishes_is_given_up_on(monkeypatch):
    """The loop had no cap, so a statement that never finished never returned: a request
    holding a worker, a control that says Running with no end, and a warehouse billing for
    an answer nobody will read."""
    catalog, execution = waiting(monkeypatch, StatementState.PENDING)

    with pytest.raises(RuntimeError) as refusal:
        catalog.run("SELECT 1")

    assert f"{WAIT_LIMIT} seconds" in str(refusal.value)
    assert execution.polls < WAIT_LIMIT + 2, "the cap is on the wait, not on the number of polls"


def test_giving_up_cancels_the_statement_at_the_source(monkeypatch):
    """A query left running after its caller has gone is money spent on a result that has
    nowhere to go."""
    catalog, execution = waiting(monkeypatch, StatementState.PENDING)

    with pytest.raises(RuntimeError):
        catalog.run("SELECT 1")

    assert execution.cancelled == ["statement-1"]


def test_a_source_that_will_not_take_the_cancellation_still_says_it_gave_up(monkeypatch):
    """Cancelling is what happens after the wait has already failed. Raising from it would
    replace the message about waiting with one about cancelling."""
    catalog, _ = waiting(monkeypatch, StatementState.PENDING, cancel_raises=True)

    with pytest.raises(RuntimeError, match="not finished"):
        catalog.run("SELECT 1")


def test_a_statement_that_finishes_inside_the_cap_is_unaffected(monkeypatch):
    """A warehouse that has to start comes back pending for minutes every time, so a wait
    that ends in an answer is the case this must not break."""
    catalog, execution = waiting(monkeypatch, *states(WAIT_LIMIT - 2, StatementState.SUCCEEDED))

    assert catalog.run("SELECT 1") == [(7,)]
    assert execution.cancelled == []


def test_a_statement_that_fails_inside_the_cap_still_reports_what_the_source_said(monkeypatch):
    """Giving up is a second reason to raise, not a replacement for the first one."""
    catalog, _ = waiting(monkeypatch, *states(2, StatementState.FAILED))

    with pytest.raises(RuntimeError, match="NO_ERROR"):
        catalog.run("SELECT 1")


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
