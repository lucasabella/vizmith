import datetime as dt
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from conftest import CATALOG, PROFILE, RECORDED, SCHEMA, WAREHOUSE, needs_warehouse
from databricks.sdk.service.catalog import TableInfo
from databricks.sdk.service.sql import StatementState

from vizmith.catalog import (
    DECLARED,
    SHAPES,
    TIMESTAMP,
    TYPES,
    UNSUPPORTED,
    Column,
    Held,
    Relationship,
    Table,
    conform,
)
from vizmith.sources import databricks as databricks_module
from vizmith.sources.databricks import (
    WAIT_LIMIT,
    DatabricksCatalog,
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


def naive(year, month, day, hour=0, minute=0):
    """A datetime with no zone, which is the contract's shape for a timestamp. Combined
    rather than called directly, because the linter reads a bare `datetime(...)` as a
    forgotten zone and here the absent zone is the thing being asserted."""
    return dt.datetime.combine(dt.date(year, month, day), dt.time(hour, minute))


def test_a_value_comes_back_as_the_type_the_manifest_reported():
    assert _value("123", "LONG") == 123
    assert _value("18.08", "DECIMAL") == 18.08
    assert _value("true", "BOOLEAN") is True
    assert _value("hello", "STRING") == "hello"
    assert _value("2024-01-01", "DATE") == dt.date(2024, 1, 1)
    assert _value("2024-01-01T00:00:00", "TIMESTAMP_NTZ") == naive(2024, 1, 1)
    assert _value(None, "STRING") is None


@pytest.mark.parametrize("type_name", ["DATE", "TIMESTAMP", "TIMESTAMP_NTZ"])
def test_a_temporal_value_is_an_object_rather_than_the_text_the_api_sent(type_name):
    """The one shape a result set holds for a temporal column, per ROADMAP.md. The
    statement API answers in text, so without this a chart drawn from a warehouse gets a
    string where the same chart drawn from anywhere else gets a date."""
    value = _value("2024-01-01" if type_name == "DATE" else "2024-01-01T09:30:00.000", type_name)

    assert type(value) is SHAPES[TYPES[type_name]]


def test_a_timestamp_with_a_zone_arrives_in_utc_without_one():
    """A TIMESTAMP comes back with a `Z` where a TIMESTAMP_NTZ comes back without one, and
    the contract has one shape for both. Timezones are not a feature here: the instant is
    kept and the zone is dropped, so two sources cannot disagree about what a row holds."""
    assert _value("2024-01-01T00:00:00.000Z", "TIMESTAMP") == naive(2024, 1, 1)
    assert _value("2024-01-01T02:30:00.000+02:00", "TIMESTAMP") == naive(2024, 1, 1, 0, 30)
    assert _value("2024-01-01T00:00:00.000Z", "TIMESTAMP").tzinfo is None


def test_a_temporal_value_the_source_mangles_is_the_sources_failure():
    """RuntimeError rather than ValueError, because the API reads the first as the source
    and the second as the spec, and a spec that asked for a date is not what went wrong.
    Handing the text back instead would put the shape this exists to remove into a result
    set, where only whatever reads the value much later would notice."""
    with pytest.raises(RuntimeError, match="not-a-date"):
        _value("not-a-date", "DATE")


def test_a_value_the_source_already_shaped_is_conformed_rather_than_guessed_at():
    """`conform` is what a catalog whose client answers in Python objects calls, the test
    harness among them. A decimal and a zoned timestamp move; everything else is already
    the contract's shape and is left alone."""
    assert conform(Decimal("18.08")) == 18.08
    assert type(conform(Decimal("18.08"))) is float
    berlin = dt.datetime(2024, 1, 1, 2, 30, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert conform(berlin) == naive(2024, 1, 1, 0, 30)
    assert conform([Decimal("1.50"), "shipped", None]) == [1.5, "shipped", None]
    assert conform(dt.date(2024, 1, 1)) == dt.date(2024, 1, 1)
    assert conform(None) is None


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


def test_the_workspaces_own_calls_go_through_the_configured_scope():
    """`DESCRIBE DETAIL` needs the full name whether the caller wrote one or not, so this
    connector still resolves a short name — through the scope every catalog carries rather
    than through a rule of its own. What that rule is, and that a name outside it is
    refused, is `tests/test_scope.py`, which holds every catalog to it rather than this
    one."""
    catalog = DatabricksCatalog(profile="unused", catalog=CATALOG, schema=SCHEMA, warehouse="unused")

    assert catalog.scope.values == (CATALOG, SCHEMA)
    assert catalog.qualify("orders") == catalog.scope.qualify("orders") == f"{CATALOG}.{SCHEMA}.orders"
    with pytest.raises(ValueError, match="outside the configured schema"):
        catalog.qualify("hr.people.salaries")


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
    monkeypatch.setattr(databricks_module, "time", Clock())
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


class Ticks:
    """A clock a test moves by hand, so the window is tested rather than waited out.
    Not the `Clock` above, which stands in for the whole time module."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def held(catalog, hold=10.0, shape=100.0, ceiling=40.0):
    clock = Ticks()
    return Held(catalog, hold=hold, shape=shape, ceiling=ceiling, clock=clock), clock


def test_a_freshness_answer_inside_the_window_is_asked_of_the_source_once(catalog):
    """What the hold is for. A page load, then a question, then a drag of a field into a
    well each read the profiles, and each used to pay a DESCRIBE DETAIL per table to do
    it. Inside one burst that is the same question with the same answer."""
    source, _ = held(catalog)

    answers = [source.modified("vizmith.shop.orders") for _ in range(3)]

    assert answers == ["1", "1", "1"]
    assert catalog.freshness_checks == ["vizmith.shop.orders"]


def test_a_freshness_answer_is_asked_again_once_the_window_has_passed(catalog):
    """The window is what keeps this from being the cache the profile file exists to
    refuse. A table rewritten under a running server is still noticed, late by the hold
    rather than not at all."""
    source, clock = held(catalog)
    source.modified("vizmith.shop.orders")

    clock.now += 10.0
    catalog.modified_times["orders"] = "2"

    assert source.modified("vizmith.shop.orders") == "2"
    assert catalog.freshness_checks == ["vizmith.shop.orders"] * 2


def test_a_burst_that_is_still_reading_keeps_what_it_read_at_the_start_of_it(catalog):
    """The bug the measurement found. A cold read of 152 tables takes about 25 seconds
    against a 30 second window, so held per answer the front of the schema was seconds from
    expiring by the time the back of it was read, and the next request paid for the front
    again. Here the whole read takes longer than the hold, and it is still one burst.

    Eight tables at two ticks each is 16, well past the hold of 10, and the answer taken
    first is still the answer served at the end of it."""
    source, clock = held(catalog)

    for name in catalog.tables():
        source.modified(name)
        clock.now += 2.0
    first = catalog.tables()[0]
    source.modified(first)

    assert catalog.freshness_checks == catalog.tables(), "the front of the schema was read twice"
    assert source.modified(first) == "1"


def test_a_burst_ends_when_the_reading_does(catalog):
    """What keeps a burst from being the cache the profile file exists to refuse. It runs
    while the gaps between reads are shorter than the hold, and one gap longer than that
    ends it, whatever came before."""
    source, clock = held(catalog)
    source.modified("vizmith.shop.orders")

    clock.now += 5.0
    source.modified("vizmith.shop.customers")
    clock.now += 10.0
    catalog.modified_times["orders"] = "2"

    assert source.modified("vizmith.shop.orders") == "2", "a burst outlived the reading"


def test_a_burst_cannot_hold_an_answer_past_the_ceiling(catalog):
    """Where the harm is bounded. Somebody working steadily reads often enough that the gaps
    never reach the hold, so without this the burst — and the answer it is holding — would
    last as long as they kept working."""
    source, clock = held(catalog, hold=10.0, ceiling=40.0)
    tables = catalog.tables()
    source.modified(tables[0])

    # A read every five seconds, so the hold never sees a gap, and a table it has not read
    # before every time, so each one is a read of the source rather than a hit. Seven of
    # them reaches 35 seconds, and the eighth gesture lands at 40.
    for name in tables[1:]:
        clock.now += 5.0
        source.modified(name)
    clock.now += 5.0
    catalog.freshness_checks.clear()

    assert source.modified(tables[0]) == "1"
    assert catalog.freshness_checks == [tables[0]], "the ceiling did not end the burst"


def test_holding_one_tables_answer_says_nothing_about_another(catalog):
    """Held per table, because a schema is read a table at a time and one of them being
    current is not an answer about the rest."""
    source, _ = held(catalog)

    source.modified("vizmith.shop.orders")
    source.modified("vizmith.shop.customers")

    assert catalog.freshness_checks == ["vizmith.shop.orders", "vizmith.shop.customers"]


def test_a_source_with_no_modified_time_to_give_is_held_like_any_other_answer(catalog):
    """A view costs a failed statement to find that out, and finding it out three times
    inside one burst is the same waste as asking three times about a table. What it must
    not do is turn into a cached profile: `Profiles` never stores one against None."""
    catalog._modified = None
    source, _ = held(catalog)

    answers = [source.modified("vizmith.shop.a_view") for _ in range(3)]

    assert answers == [None, None, None]
    assert catalog.freshness_checks == ["vizmith.shop.a_view"]


def test_a_listing_and_a_statement_still_reach_the_source_every_time(catalog):
    """What is held is a question with an answer that does not change between two calls a
    second apart. A listing is not one of those — it is how a table somebody created is
    noticed, and it is one call for the whole schema rather than one per table — and a
    statement is a different question every time by construction."""
    source, _ = held(catalog)

    assert source.dialect is catalog.dialect
    assert source.tables() == catalog.tables()

    catalog.statements.clear()
    source.run("SELECT 1")
    source.run("SELECT 1")
    assert catalog.statements == ["SELECT 1", "SELECT 1"]


def test_a_description_inside_the_window_is_asked_of_the_source_once(catalog):
    """What the shape hold is for. Every join path resolved rebuilds the relationship
    graph, and building it describes every table, so a person dragging a second column
    from a second table used to pay the whole schema again."""
    expected = catalog.describe("vizmith.shop.orders")
    source, _ = held(catalog)
    catalog.described.clear()

    described = [source.describe("vizmith.shop.orders") for _ in range(3)]

    assert described == [expected] * 3
    assert catalog.described == ["vizmith.shop.orders"], "a held description was asked again"


def test_a_description_is_asked_again_once_the_window_has_passed(catalog):
    """The window is what keeps a description from being held for the life of the process.
    A table altered under a running server is described as it was for at most this long,
    and the table nothing profiles is the one the window is the only bound on."""
    source, clock = held(catalog)
    source.describe("vizmith.shop.orders")
    catalog.described.clear()

    clock.now += 100.0
    source.describe("vizmith.shop.orders")

    assert catalog.described == ["vizmith.shop.orders"]


def test_a_table_that_moved_drops_the_description_held_for_it(catalog):
    """The hole a held description would otherwise open in the profile cache.

    `profile_table` describes the table it profiles, so a profile built from a description
    taken before a column was added would be stored under the freshness token taken after
    it: current, and missing a column, until the table changed again. The token moving is
    what says the table was written to or altered, so it drops the description with it.

    The clock is moved past the freshness window and not past the shape's, which is the
    case this is about: the description is still inside its own window and is dropped
    anyway, because the source has said the table is not the one that was described."""
    source, clock = held(catalog)
    source.modified("vizmith.shop.orders")
    source.describe("vizmith.shop.orders")
    catalog.described.clear()

    clock.now += 10.0
    catalog.modified_times["orders"] = "2"
    assert source.modified("vizmith.shop.orders") == "2"
    source.describe("vizmith.shop.orders")

    assert catalog.described == ["vizmith.shop.orders"], "a table that moved kept its shape"


def test_a_table_that_did_not_move_keeps_the_description_held_for_it(catalog):
    """The other half, and what makes the drop above a check rather than a cost: an
    unchanged token is the source saying neither the data nor the definition moved, so the
    columns it listed are still the columns."""
    source, clock = held(catalog)
    source.modified("vizmith.shop.orders")
    source.describe("vizmith.shop.orders")
    catalog.described.clear()

    clock.now += 10.0
    assert source.modified("vizmith.shop.orders") == "1"
    source.describe("vizmith.shop.orders")

    assert catalog.described == []


def test_the_declared_relationships_are_held_on_the_shape_window(catalog):
    """A constraint is declared by hand and read by every question asked, so it is held
    like a description rather than asked for per request. Held whole, because nothing asks
    for one table's keys on their own.

    Counted in descriptions, since rolling the keys up is what describing every table
    is for on a source that holds a constraint on the table declaring it."""
    expected = catalog.relationships()
    source, clock = held(catalog)
    catalog.described.clear()

    burst = [source.relationships() for _ in range(3)]
    asked_in_the_burst = len(catalog.described)
    clock.now += 100.0
    later = source.relationships()

    assert burst == [expected] * 3
    assert later == expected
    assert asked_in_the_burst == len(catalog.tables()), "a burst rolled the keys up again"
    assert len(catalog.described) == 2 * len(catalog.tables()), "the window never passed"


def test_a_held_description_is_not_a_row(catalog):
    """The boundary this whole layer exists to keep. A description is names and types, so
    holding one holds no value out of any table, which is why it can be held for minutes
    where a profile is keyed on the source's own token."""
    source, _ = held(catalog)

    described = source.describe("vizmith.shop.orders")

    assert [column.name for column in described.columns]
    assert not hasattr(described, "rows")
    assert catalog.statements == []


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


class OverlappingTables:
    """A workspace whose `tables.get` records how many calls were in flight at once.

    Every call waits, because a control plane call is nearly all waiting and one that
    returns instantly cannot show whether two of them overlapped."""

    WAIT = 0.02

    def __init__(self, names, constrained=()):
        self._names = names
        self._constrained = dict(constrained)
        self._lock = threading.Lock()
        self.asked: list[str] = []
        self.in_flight = 0
        self.peak = 0

    def list(self, catalog_name, schema_name):
        return [SimpleNamespace(full_name=name) for name in self._names]

    def get(self, name):
        with self._lock:
            self.asked.append(name)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        time.sleep(self.WAIT)
        with self._lock:
            self.in_flight -= 1
        return SimpleNamespace(full_name=name, columns=[], table_constraints=self._constrained.get(name, []))


def foreign_key(child, parent_table, parent):
    return SimpleNamespace(
        foreign_key_constraint=SimpleNamespace(
            child_columns=[child], parent_table=parent_table, parent_columns=[parent]
        )
    )


def test_reading_the_declared_relationships_does_not_wait_for_one_table_at_a_time():
    """A constraint is held on the table that declares it and there is nothing to ask for
    the set of them, so every table is read. That was a loop, which put a schema's worth of
    round trips one after another in front of every join path resolved and every question
    asked.

    The overlap is what is asserted rather than a duration: a timing assertion goes red on a
    loaded runner for a reason nobody caused. Every table is still read exactly once, and
    the answer is still sorted, because a graph that renumbered between two calls would
    renumber the answers stored against it.

    Once is also the count that matters now that a description carries the constraints: this
    is the same read `describe` makes rather than a second one beside it, so a caller that
    does both no longer reads the schema twice."""
    names = [f"{CATALOG}.{SCHEMA}.table_{n}" for n in range(24)]
    orders, customers = names[0], names[1]
    workspace = OverlappingTables(names, {orders: [foreign_key("customer_id", customers, "id")]})
    catalog = DatabricksCatalog(profile="unused", catalog=CATALOG, schema=SCHEMA, warehouse="unused")
    catalog._client = SimpleNamespace(tables=workspace)

    found = catalog.relationships()

    assert sorted(workspace.asked) == sorted(names), "a table was read twice or not at all"
    assert workspace.peak > 1, "the tables were read one at a time"
    assert [(r.left_table, r.left_column, r.right_table, r.right_column) for r in found] == [
        (orders, "customer_id", customers, "id")
    ]
    assert found == sorted(found)


def test_a_description_carries_the_keys_its_table_declares():
    """What makes the read once rather than twice. The constraints are in the response that
    lists the columns, so a caller describing the schema has been told them and nothing
    above has to ask a second time.

    A composite key is several column pairs, read positionally, because that is the order
    the constraint declares them in."""
    parent = f"{CATALOG}.{SCHEMA}.customers"
    child = f"{CATALOG}.{SCHEMA}.order_items"
    described = _table(
        TableInfo.from_dict(
            {
                "full_name": child,
                "columns": [
                    {"name": "order_id", "type_name": "INT", "nullable": False},
                    {"name": "line", "type_name": "INT", "nullable": False},
                ],
                "table_constraints": [
                    {
                        "foreign_key_constraint": {
                            "name": "fk",
                            "child_columns": ["order_id", "line"],
                            "parent_table": parent,
                            "parent_columns": ["id", "line"],
                        }
                    }
                ],
            }
        )
    )

    assert described.relationships == (
        Relationship(child, "order_id", parent, "id", kind=DECLARED),
        Relationship(child, "line", parent, "line", kind=DECLARED),
    )


def test_a_table_that_declares_nothing_carries_no_relationships():
    """A workspace where nobody has declared a foreign key answers with the field absent
    rather than empty, on every table, which is the common case in a lakehouse. What fills
    the gap is a suggestion a person confirms, and that is not the source's word to give."""
    assert _table(SimpleNamespace(full_name="a.b.c", columns=[])).relationships == ()


def test_a_constraint_that_is_not_a_foreign_key_is_not_a_relationship():
    """A primary key is a constraint on the same list and joins nothing to anything. It is
    read past rather than reported, because a graph is what a join path is resolved
    against and a table related to itself is not a path."""
    described = _table(
        TableInfo.from_dict(
            {
                "full_name": f"{CATALOG}.{SCHEMA}.orders",
                "columns": [{"name": "id", "type_name": "INT", "nullable": False}],
                "table_constraints": [{"primary_key_constraint": {"name": "pk", "child_columns": ["id"]}}],
            }
        )
    )

    assert described.relationships == ()
    assert [column.name for column in described.columns] == ["id"]


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
