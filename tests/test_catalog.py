import time
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


def manifest_column(type_name, type_text, name="value"):
    """A column as the statement manifest describes one, with the two ways it names a
    type. The SDK fills in one, the other or both."""
    enum = SimpleNamespace(value=type_name) if type_name else None
    return SimpleNamespace(name=name, type_name=enum, type_text=type_text)


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


class DetailExecution:
    """A statement execution answering one fixed result set, described by name. What it
    exists for is `DESCRIBE DETAIL`, whose columns have moved between runtime versions, so
    reading `lastModified` off a position rather than off the manifest is a bug waiting for
    an upgrade."""

    def __init__(self, columns, row, state=None):
        from databricks.sdk.service.sql import StatementState

        self._columns = columns
        self._row = row
        self._state = state or StatementState.SUCCEEDED
        self.statements = []

    def execute_statement(self, **arguments):
        self.statements.append(arguments.get("statement"))
        return SimpleNamespace(
            statement_id="statement-1",
            status=SimpleNamespace(state=self._state, error="[TABLE_OR_VIEW_NOT_FOUND] no"),
            manifest=SimpleNamespace(
                truncated=False,
                total_chunk_count=1,
                schema=SimpleNamespace(
                    columns=[manifest_column("STRING", "STRING", name=name) for name in self._columns]
                ),
            ),
            result=SimpleNamespace(data_array=[self._row] if self._row else []),
        )


DETAIL = ["format", "id", "name", "location", "createdAt", "lastModified", "numFiles"]
DETAIL_ROW = ["delta", "abc", "orders", "s3://x", "2024-01-01", "2026-07-31T09:12:44Z", "12"]


def describing(*arguments, **keywords):
    execution = DetailExecution(*arguments, **keywords)
    catalog = DatabricksCatalog(profile="unused", catalog=CATALOG, schema=SCHEMA, warehouse="unused")
    catalog._client = SimpleNamespace(statement_execution=execution)
    return catalog, execution


def test_a_modified_time_is_read_off_the_manifest_rather_than_a_column_position():
    """DESCRIBE DETAIL has gained columns between runtime versions, so a position that is
    right today is a silently wrong timestamp after an upgrade."""
    catalog, execution = describing(DETAIL, DETAIL_ROW)
    moved, row = ["lastModified"] + DETAIL, ["2026-07-31T09:12:44Z"] + DETAIL_ROW
    elsewhere, _ = describing(moved, row)

    assert catalog.modified("orders") == "2026-07-31T09:12:44Z"
    assert elsewhere.modified("orders") == "2026-07-31T09:12:44Z"
    assert execution.statements == [f"DESCRIBE DETAIL `{CATALOG}`.`{SCHEMA}`.`orders`"]


def test_a_source_object_that_cannot_be_described_has_no_modified_time():
    """A view is the case: DESCRIBE DETAIL refuses one. What that means for a caller is
    that the object cannot be cached, which is None rather than a failure."""
    from databricks.sdk.service.sql import StatementState

    refusing, _ = describing(DETAIL, DETAIL_ROW, state=StatementState.FAILED)
    empty, _ = describing(DETAIL, [])

    assert refusing.modified("a_view") is None
    assert empty.modified("orders") is None


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


# How long the metastore is given to publish a write before a modified time that has not
# moved is called one that does not move. Generous, because the answer this test gives is
# what the profile cache is keyed on and a slow publish is not the same as a wrong key.
MODIFIED_WAIT = 30


@needs_warehouse
def test_the_modified_time_moves_when_the_data_changes():
    """The profile cache is keyed on this, so a modified time that only tracks the table's
    definition would cache a profile of data that is gone and never re-read it.

    Databricks documents that `last_altered`, which is the information schema's name for
    the `updated_at` this could otherwise have read for free, does not move for an insert,
    an update or a delete. `DESCRIBE DETAIL` is their own answer to that, and this is what
    checks it is still true. A workspace is the only place that can.

    The table is created and dropped here, so nothing in the fixture schema is written to.
    A workspace that will not take a temporary table skips rather than fails: the point is
    the timestamp, not the permission."""
    catalog = DatabricksCatalog(profile=PROFILE, catalog=CATALOG, schema=SCHEMA, warehouse=WAREHOUSE)
    name = "vizmith_modified_probe"
    quoted = catalog.dialect.qualified(catalog.qualify(name))
    try:
        catalog.run(f"CREATE OR REPLACE TABLE {quoted} (n INT)")
    except RuntimeError as refusal:
        pytest.skip(f"the workspace would not take a temporary table: {refusal}")

    try:
        catalog.run(f"INSERT INTO {quoted} VALUES (1)")
        before = catalog.modified(name)
        catalog.run(f"INSERT INTO {quoted} VALUES (2)")
        # Read until it moves rather than once, because the metastore does not necessarily
        # publish the write the moment it lands. What this must not do is wait forever: a
        # time that never moves is the answer the test exists to get.
        deadline = time.monotonic() + MODIFIED_WAIT
        while (after := catalog.modified(name)) == before and time.monotonic() < deadline:
            time.sleep(2)
    finally:
        catalog.run(f"DROP TABLE IF EXISTS {quoted}")

    assert before is not None, "the source reports no modified time, so nothing can be cached"
    assert after != before, (
        f"the modified time did not move in {MODIFIED_WAIT} seconds after a write, so it cannot "
        f"key the profile cache and ROADMAP.md's entry on it is wrong"
    )


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
