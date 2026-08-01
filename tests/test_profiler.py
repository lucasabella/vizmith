import json
from concurrent.futures import ThreadPoolExecutor

from conftest import DUCKDB, FixtureCatalog, needs_warehouse

from vizmith.catalog import TIMESTAMP, Dialect
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
