import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import DUCKDB, FixtureCatalog, needs_warehouse
from test_catalog import Ticks

from vizmith import profiler
from vizmith.catalog import TIMESTAMP, Dialect, Held
from vizmith.profiler import CACHE_VERSION, SAMPLE_THRESHOLD, Profiles, TableProfile, profile_table

# A source that offers no approximate count, so the exact branch is covered without a
# second database.
EXACT = Dialect(
    quote='"',
    approx_distinct=None,
    distinct_values=DUCKDB.distinct_values,
    parameter=DUCKDB.parameter,
)


def column(profile, name):
    return next(entry for entry in profile.columns if entry.name == name)


def test_profiling_two_databases_gives_the_same_profile(catalog, other_fixture_db):
    """M2 asks for a profile that is identical across runs, so this compares two
    databases built from the same files rather than two queries against one."""
    other = FixtureCatalog(other_fixture_db)
    for table in ("orders", "shipment_scans", "customers"):
        first = json.dumps(profile_table(catalog, table).as_dict())
        second = json.dumps(profile_table(other, table).as_dict())
        assert first == second


def test_a_high_cardinality_column_has_no_samples(catalog):
    scans = profile_table(catalog, "shipment_scans")
    assert column(scans, "location_code").distinct_count > SAMPLE_THRESHOLD
    assert column(scans, "location_code").samples == ()
    assert column(scans, "scanned_at").samples == ()


def test_a_low_cardinality_column_has_samples(catalog):
    scans = profile_table(catalog, "shipment_scans")
    assert column(scans, "status").samples == (
        "delivered",
        "exception",
        "in_transit",
        "out_for_delivery",
        "received",
    )


def test_samples_are_dropped_when_the_collected_set_is_over_the_threshold(catalog):
    """An approximate distinct count can sit below the threshold while the column sits
    above it. location_code counts as 483 against a true 500, so a threshold between the
    two proves the profile drops what came back instead of trimming it."""
    scans = profile_table(catalog, "shipment_scans", threshold=490)
    assert column(scans, "location_code").distinct_count <= 490
    assert column(scans, "location_code").samples == ()


def test_the_null_rate_counts_the_column_that_has_nulls_and_no_others(catalog, fixture_db):
    orders = profile_table(catalog, "orders")
    nulls, rows = fixture_db.execute(
        "SELECT count(*) - count(order_date), count(*) FROM vizmith.shop.orders"
    ).fetchone()
    assert nulls > 0
    assert column(orders, "order_date").null_rate == nulls / rows
    assert column(orders, "status").null_rate == 0.0
    assert column(profile_table(catalog, "customers"), "country").null_rate == 0.0


def test_a_range_is_present_for_numbers_and_dates_and_absent_for_text(catalog):
    orders = profile_table(catalog, "orders")
    assert column(orders, "total").minimum == "4.59"
    assert column(orders, "total").maximum == "3340.33"
    assert column(orders, "order_date").minimum == "2024-01-01"
    assert column(orders, "order_date").maximum == "2026-06-30"
    assert column(orders, "status").minimum is None
    assert column(orders, "status").maximum is None
    assert column(profile_table(catalog, "returns"), "returned_at").minimum is not None


def test_a_profile_round_trips_through_json(catalog):
    profile = profile_table(catalog, "orders")
    assert TableProfile.from_dict(json.loads(json.dumps(profile.as_dict()))) == profile


def test_statistics_are_one_statement_and_samples_are_one_more(catalog):
    profile_table(catalog, "shipment_scans")
    assert len(catalog.statements) == 2
    assert all(statement.count('"shipment_scans"') == 1 for statement in catalog.statements)


def test_a_table_with_nothing_below_the_threshold_emits_only_the_statistics_query(catalog):
    profile = profile_table(catalog, "order_items", threshold=3)
    assert len(catalog.statements) == 1
    assert all(entry.samples == () for entry in profile.columns)


def test_the_threshold_is_configurable(catalog):
    quantity = column(profile_table(catalog, "order_items", threshold=4), "quantity")
    assert quantity.distinct_count == 4
    assert quantity.samples == ("1", "2", "3", "4")
    assert column(profile_table(catalog, "order_items", threshold=3), "quantity").samples == ()


def test_a_distinct_count_is_approximate_where_the_source_offers_the_function(fixture_db):
    approximate = profile_table(FixtureCatalog(fixture_db), "shipment_scans")
    exact = profile_table(FixtureCatalog(fixture_db, dialect=EXACT), "shipment_scans")

    assert column(approximate, "shipment_id").distinct_count_exact is False
    assert column(exact, "shipment_id").distinct_count_exact is True
    assert column(approximate, "shipment_id").distinct_count != column(exact, "shipment_id").distinct_count


def test_a_cached_profile_is_the_one_it_replaced(catalog, tmp_path):
    kept = Profiles(tmp_path / "profiles.json")
    first = kept.read(catalog, "orders")
    statements = list(catalog.statements)

    assert kept.read(catalog, "orders") == first
    assert catalog.statements == statements, "a cached profile still cost a pass over the table"


def test_a_table_whose_modified_time_moved_is_profiled_again(catalog, tmp_path):
    """The whole point of the key. A table written to since the profile was stored has
    figures that describe data that is gone, and the model reads them as current."""
    kept = Profiles(tmp_path / "profiles.json")
    kept.read(catalog, "orders")
    statements = list(catalog.statements)

    catalog.modified_times["orders"] = "2"
    kept.read(catalog, "orders")

    assert len(catalog.statements) > len(statements)


def test_a_table_nothing_is_stored_for_scans_without_waiting_for_its_freshness(catalog, tmp_path):
    """A table this file has never seen is going to be profiled whatever the source says
    about its freshness — there is nothing for the answer to serve — so waiting for that
    answer before starting the scan is a round trip per table in front of a cold read, for a
    decision nobody makes.

    Held rather than timed. The source will not answer the freshness question until this
    test lets it, so the scan either starts anyway or the read deadlocks; a clock would be
    asserting on whatever else the machine was doing.

    It also holds the correctness that makes this a concurrent start and not a reordering:
    the token is asked for no later than the scan begins. A write landing between the two
    leaves a profile newer than its token, so the next freshness answer differs and the
    table is profiled again — late by one read. Taken *after* the scan, that same write is
    stored as current and missing, and nothing notices until the table changes again."""
    held = _Withheld(catalog)
    reading = ThreadPoolExecutor(max_workers=1).submit(
        Profiles(tmp_path / "profiles.json").read, held, "orders"
    )

    assert held.scanned.wait(10), "the scan waited for a freshness answer that never came"
    assert held.asked_before_the_scan is True, "the token was taken after the scan it keys"

    held.answer.set()
    assert reading.result(timeout=10).table.endswith("orders")


def test_a_source_that_cannot_say_when_a_table_changed_reports_it_rather_than_going_quiet(
    catalog, tmp_path
):
    """Asked on a thread of its own, so what it raised has to be carried back. Swallowed, it
    would turn every table into one that cannot be cached — a profile is never stored against
    no token — and the symptom of that is a warehouse bill rather than an error."""

    class Refusing(_Passthrough):
        def modified(self, name):
            raise RuntimeError("DESCRIBE DETAIL is not supported here")

    with pytest.raises(RuntimeError, match="not supported here"):
        Profiles(tmp_path / "profiles.json").read(Refusing(catalog), "orders")


class _Passthrough:
    """Everything the catalog does, forwarded, so a subclass overrides one method."""

    def __init__(self, catalog):
        self._catalog = catalog
        self.dialect = catalog.dialect
        self.scope = catalog.scope

    def tables(self):
        return self._catalog.tables()

    def describe(self, name):
        return self._catalog.describe(name)

    def relationships(self):
        return self._catalog.relationships()

    def modified(self, name):
        return self._catalog.modified(name)

    def run(self, sql, parameters=None):
        return self._catalog.run(sql, parameters)


class _Withheld(_Passthrough):
    """A source that will not answer the freshness question until the test lets it, and
    writes down whether it had been asked by the time the scan began.

    Both halves matter. Withholding the answer is what proves the scan does not wait for
    it — a read that did would never reach a statement. Recording the order is what proves
    the token was taken no later than the scan, which is the half that keeps the cache
    honest."""

    def __init__(self, catalog):
        super().__init__(catalog)
        self.answer = threading.Event()
        self.asked = threading.Event()
        self.scanned = threading.Event()
        self.asked_before_the_scan: bool | None = None

    def modified(self, name):
        self.asked.set()
        self.answer.wait(10)
        return super().modified(name)

    def run(self, sql, parameters=None):
        if self.asked_before_the_scan is None:
            # Waited on rather than sampled: the thread has been started by now, and the
            # only reason it would not have run is that this machine has not scheduled it
            # yet. A source that asks after the scan never sets this and times out to False.
            self.asked_before_the_scan = self.asked.wait(10)
        self.scanned.set()
        return super().run(sql, parameters)


def test_a_lower_threshold_does_not_read_samples_collected_under_a_higher_one(catalog, tmp_path):
    """The threshold is the security boundary, so a stored profile cannot be allowed to
    answer for a threshold it was not built with. Lowering it has to silence the samples."""
    kept = Profiles(tmp_path / "profiles.json")

    assert column(kept.read(catalog, "order_items", threshold=4), "quantity").samples == (
        "1",
        "2",
        "3",
        "4",
    )
    assert column(kept.read(catalog, "order_items", threshold=3), "quantity").samples == ()


def test_a_source_with_no_modified_time_is_profiled_every_time(fixture_db, tmp_path):
    """Caching against nothing means caching forever, and a profile that is never re-read
    is a wrong answer with no symptom. So a source that cannot say when a table changed
    pays for every profile."""
    catalog = FixtureCatalog(fixture_db, modified=None)
    kept = Profiles(tmp_path / "profiles.json")

    kept.read(catalog, "orders")
    statements = list(catalog.statements)
    kept.read(catalog, "orders")

    assert len(catalog.statements) == 2 * len(statements)
    assert not (tmp_path / "profiles.json").exists()


def test_a_profile_survives_the_process_that_paid_for_it(catalog, tmp_path):
    """A restart is what re-profiled a whole schema before this, at two statements a table
    and a bill each time."""
    path = tmp_path / "profiles.json"
    first = Profiles(path).read(catalog, "orders")
    statements = list(catalog.statements)

    restarted = Profiles(path).read(catalog, "orders")

    assert restarted == first
    assert catalog.statements == statements


def test_a_file_written_under_another_format_is_discarded_rather_than_read(catalog, tmp_path):
    path = tmp_path / "profiles.json"
    Profiles(path).read(catalog, "orders")
    written = json.loads(path.read_text())
    path.write_text(json.dumps({**written, "version": CACHE_VERSION + 1}))
    statements = list(catalog.statements)

    Profiles(path).read(catalog, "orders")

    assert len(catalog.statements) > len(statements)


def test_a_file_that_cannot_be_read_costs_a_profile_rather_than_the_answer(catalog, tmp_path):
    """A cache nobody can read is the cheapest failure there is. Raising here would turn it
    into a server that answers nothing until somebody finds the file and deletes it."""
    path = tmp_path / "profiles.json"
    path.write_text("{not json at all")

    profile = Profiles(path).read(catalog, "orders")

    assert profile == profile_table(catalog, "orders")
    assert json.loads(path.read_text())["version"] == CACHE_VERSION


def test_profiling_a_schema_at_once_stores_every_table_it_paid_for(catalog, tmp_path):
    """The tables are profiled eight at a time and they all write one file, so this is
    what says the last writer did not drop the other seven."""
    path = tmp_path / "profiles.json"
    kept = Profiles(path)
    names = catalog.tables()
    with ThreadPoolExecutor(max_workers=8) as pool:
        profiled = list(pool.map(lambda name: kept.read(catalog, name), names))
    statements = list(catalog.statements)

    restarted = Profiles(path)
    assert [restarted.read(catalog, name) for name in names] == profiled
    assert catalog.statements == statements
    assert set(json.loads(path.read_text())["profiles"]) == set(names)


def test_one_table_changing_does_not_reprofile_the_others(catalog, tmp_path):
    kept = Profiles(tmp_path / "profiles.json")
    for name in ("orders", "order_items"):
        kept.read(catalog, name)
    statements = list(catalog.statements)

    catalog.modified_times["orders"] = "2"
    for name in ("orders", "order_items"):
        kept.read(catalog, name)

    fresh = catalog.statements[len(statements) :]
    assert fresh
    assert all('"order_items"' not in statement for statement in fresh)


@needs_warehouse
def test_a_profile_from_the_workspace_carries_a_timestamp_and_its_samples(live_catalog):
    """The two ways the manifest describes a type both had to be read wrong for this to
    pass before: a timestamp column raised, and a low cardinality column came back as the
    characters of an array. DuckDB shows neither, so only this can say they are fixed."""
    profile = profile_table(live_catalog, "shipment_scans")

    scanned = column(profile, "scanned_at")
    assert scanned.type == TIMESTAMP
    assert scanned.minimum < scanned.maximum

    status = column(profile, "status")
    assert 1 < len(status.samples) <= SAMPLE_THRESHOLD
    assert all(value.isalpha() or "_" in value for value in status.samples), status.samples


def test_a_run_of_requests_parses_the_stored_cache_once(catalog, tmp_path, monkeypatch):
    """The API builds a Profiles per request, and the file holds every table's profile
    including its samples. Parsed per request, a panel load of N single-table requests
    parsed an N-table file N times, which is seconds of server CPU on a large schema."""
    path = tmp_path / "profiles.json"
    Profiles(path).read(catalog, "orders")

    parses = 0
    parse = profiler._parse

    def counted(reading):
        nonlocal parses
        parses += 1
        return parse(reading)

    monkeypatch.setattr(profiler, "_parse", counted)
    # A cache this process did not write, which is what a server finds on its first
    # request after a restart.
    profiler._FILES.clear()
    for _ in range(5):
        assert Profiles(path).read(catalog, "orders")

    assert parses == 1, "the file was parsed once per request rather than once"


def test_a_cache_replaced_by_another_process_is_picked_up_without_a_restart(catalog, tmp_path):
    """The held copy is not a copy that goes stale: the file's own stamp says whether it is
    still the file that was parsed, and a replaced one is parsed again."""
    path = tmp_path / "profiles.json"
    Profiles(path).read(catalog, "orders")
    statements = list(catalog.statements)

    # What another process would leave behind: a cache holding no profiles at all.
    path.write_text(json.dumps({"version": CACHE_VERSION, "profiles": {}}))
    Profiles(path).read(catalog, "orders")

    assert len(catalog.statements) > len(statements), "the replaced file was not picked up"


def test_a_held_cache_is_still_refused_where_the_source_says_the_table_changed(catalog, tmp_path):
    """What makes holding the parsed file safe is the key that was already there. This is
    that key, asked of the held copy rather than of a fresh read."""
    path = tmp_path / "profiles.json"
    Profiles(path).read(catalog, "orders")
    statements = list(catalog.statements)

    catalog.modified_times["orders"] = "2"
    Profiles(path).read(catalog, "orders")

    assert len(catalog.statements) > len(statements)


def test_a_source_holding_its_freshness_answers_still_reprofiles_once_the_window_passes(
    catalog, tmp_path
):
    """The two caches meet here. A source that holds when it was last asked about a table
    is what keeps a burst of requests from paying a statement per table each; what it must
    not do is turn the profile cache into the one it replaced, which never noticed a write
    at all. Inside the window the stored profile stands, and after it the table is profiled
    again."""
    clock = Ticks()
    source = Held(catalog, hold=30.0, clock=clock)
    path = tmp_path / "profiles.json"
    Profiles(path).read(source, "orders")
    catalog.modified_times["orders"] = "2"
    statements = list(catalog.statements)

    Profiles(path).read(source, "orders")
    assert catalog.statements == statements, "the held window did not hold"

    clock.now += 30.0
    Profiles(path).read(source, "orders")
    assert len(catalog.statements) > len(statements), "a table written to was never noticed"
