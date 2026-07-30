import json

import pytest
from generate_data import COLUMNS, NULLABLE

from vizmith.catalog import DATE, DECIMAL, INTEGER, STRING, TIMESTAMP, Column, Dialect, Table
from vizmith.profiler import SAMPLE_THRESHOLD, TableProfile, profile_table

DUCKDB = Dialect(
    quote='"',
    approx_distinct="approx_count_distinct({column})",
    distinct_values="array_agg(DISTINCT {column})",
)
# A source that offers no approximate count, so the exact branch is covered without a
# second database.
EXACT = Dialect(quote='"', approx_distinct=None, distinct_values=DUCKDB.distinct_values)

TYPES = {
    "INTEGER": INTEGER,
    "VARCHAR": STRING,
    "DATE": DATE,
    "TIMESTAMP": TIMESTAMP,
    "DECIMAL(10,2)": DECIMAL,
}


class FixtureCatalog:
    """The profiler's queries run against the DuckDB fixture database. Records every
    statement it is asked to execute, which is how the cost rules are tested."""

    def __init__(self, connection, dialect=DUCKDB):
        self.dialect = dialect
        self._connection = connection
        self.statements = []

    def tables(self):
        return [f"vizmith.shop.{name}" for name in sorted(COLUMNS)]

    def describe(self, name):
        table = name.rsplit(".", 1)[-1]
        return Table(
            name=f"vizmith.shop.{table}",
            columns=tuple(
                Column(name=column, type=TYPES[type_], nullable=(table, column) in NULLABLE)
                for column, type_ in COLUMNS[table]
            ),
        )

    def run(self, sql):
        self.statements.append(sql)
        return self._connection.execute(sql).fetchall()


@pytest.fixture
def catalog(fixture_db):
    return FixtureCatalog(fixture_db)


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
