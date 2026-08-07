import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import DUCKDB, FixtureCatalog, needs_warehouse, shapes

from vizmith.catalog import Scope
from vizmith.query import build, execute
from vizmith.spec import output_columns

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))

MULTI_SERIES = FIXTURES / "valid" / "revenue_by_category_stacked.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture
def offline():
    """SQL is generated from the spec and the catalog's names, so it needs no connection.
    This catalog can describe a table and cannot run anything."""
    return FixtureCatalog(None)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_a_valid_fixture_compiles(path, offline):
    sql, parameters = build(load(path), offline)
    assert sql.startswith(("SELECT ", "WITH "))
    assert parameters


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_a_valid_fixture_executes(path, catalog):
    spec = load(path)
    rows = execute(spec, catalog)
    assert rows
    assert len(rows) <= spec["query"]["limit"]


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_the_returned_columns_are_the_querys_output_columns(path, catalog):
    spec = load(path)
    for row in execute(spec, catalog):
        assert list(row) == output_columns(spec["query"])


def test_the_column_order_is_select_then_aggregates(catalog):
    spec = load(MULTI_SERIES)
    assert output_columns(spec["query"]) == ["country", "category", "revenue"]
    assert list(execute(spec, catalog)[0]) == ["country", "category", "revenue"]


def test_a_value_is_bound_rather_than_written_into_the_statement(offline):
    sql, parameters = build(load(FIXTURES / "valid" / "revenue_by_country.json"), offline)
    assert "2025-01-01" not in sql
    assert "cancelled" not in sql
    assert parameters == {"p0": "2025-01-01", "p1": "cancelled", "p2": "refunded", "p3": 10}


def test_a_quote_in_a_filter_value_is_data_rather_than_syntax(catalog):
    spec = load(FIXTURES / "valid" / "shipments_by_carrier.json")
    spec["query"]["filters"] = [{"column": "carriers.name", "op": "=", "value": "' OR 1=1 --"}]
    assert execute(spec, catalog) == []


def test_a_row_limit_is_bound_too(offline):
    sql, parameters = build(load(FIXTURES / "valid" / "scans_per_location.json"), offline)
    assert sql.endswith("LIMIT $p0")
    assert parameters == {"p0": 10}


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_an_invalid_spec_raises_before_it_reaches_the_database(path, catalog):
    with pytest.raises(ValueError):
        execute(load(path), catalog)
    assert catalog.statements == []


def test_ranking_by_an_average_raises_rather_than_approximating(offline):
    """An average of averages is not an average, so limit_by cannot re-aggregate one."""
    spec = load(MULTI_SERIES)
    spec["query"]["aggregates"][0]["fn"] = "avg"
    with pytest.raises(ValueError, match="cannot be re-aggregated"):
        build(spec, offline)


def test_ranking_by_a_dimension_raises_rather_than_running_out_of_aggregates(offline):
    """The validator refuses this too, and the builder refuses it on its own: a bare
    StopIteration here left the endpoint with a 500 and no words in it."""
    spec = load(MULTI_SERIES)
    spec["query"]["limit_by"]["by"] = "category"
    with pytest.raises(ValueError, match="needs a measure to rank it by"):
        build(spec, offline)


def test_limit_by_keeps_the_top_outer_values_whole(catalog, fixture_db):
    spec = load(MULTI_SERIES)
    rows = execute(spec, catalog)

    countries = [row["country"] for row in rows]
    assert len(set(countries)) == spec["query"]["limit_by"]["limit"]

    # What the source says those countries have, asked without going through the builder.
    expected = dict(
        fixture_db.execute(
            """
            SELECT c.country, count(DISTINCT p.category)
            FROM vizmith.shop.order_items i
            JOIN vizmith.shop.orders o ON i.order_id = o.id
            JOIN vizmith.shop.customers c ON o.customer_id = c.id
            JOIN vizmith.shop.products p ON i.product_id = p.id
            GROUP BY c.country
            """
        ).fetchall()
    )
    for country in set(countries):
        assert countries.count(country) == expected[country]


def test_limit_by_and_a_plain_limit_of_the_same_size_differ(catalog):
    """The same question asked both ways. limit_by keeps ten countries whole, limit keeps
    ten rows and cuts a country's series in half."""
    spec = load(MULTI_SERIES)
    by_dimension = execute(spec, catalog)

    by_row = copy.deepcopy(spec)
    by_row["query"]["limit"] = by_row["query"].pop("limit_by")["limit"]
    del by_row["chart"]["encoding"]["color"]
    truncated = execute(by_row, catalog)

    assert len(truncated) == 10
    assert len(by_dimension) > len(truncated)

    complete = {row["country"] for row in by_dimension}
    cut = {row["country"] for row in truncated}
    assert cut < complete
    for country in cut:
        assert sum(row["country"] == country for row in truncated) <= sum(
            row["country"] == country for row in by_dimension
        )
    assert any(
        sum(row["country"] == country for row in truncated)
        < sum(row["country"] == country for row in by_dimension)
        for country in cut
    )


def test_a_left_join_keeps_the_rows_an_inner_join_drops(catalog):
    """The fixture data gives some shipments a carrier that does not exist, so the two
    join types have to disagree."""
    spec = load(FIXTURES / "valid" / "shipments_by_carrier.json")
    left = execute(spec, catalog)

    inner = copy.deepcopy(spec)
    inner["query"]["joins"][0]["type"] = "inner"
    rows = execute(inner, catalog)

    assert len(left) > len(rows)
    assert sum(row["shipment_count"] for row in left) > sum(row["shipment_count"] for row in rows)
    assert any(row["carrier"] is None for row in left)


@needs_warehouse
@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_a_valid_fixture_executes_against_the_warehouse(path, live_catalog, catalog):
    """DuckDB says the SQL is right. This says it is right in the dialect that ships, which
    is the only claim a user's chart depends on. Both sources hold the same fixture data,
    so a row count that differs means the two disagree about the query rather than about
    the data, and that is the failure worth catching here."""
    spec = load(path)
    rows = execute(spec, live_catalog)
    offline = execute(spec, catalog)

    assert rows
    assert list(rows[0]) == output_columns(spec["query"])
    assert len(rows) == len(offline), "the workspace copy of the fixture data may be stale"
    # The rest of the result set contract, on the only two sources there are to compare.
    # A column that is a date here and a string there is what makes a renderer ask which
    # source drew the chart, which is the question the catalog interface exists to remove.
    assert shapes(rows) == shapes(offline), (
        "the two sources disagree about what a value is, against ROADMAP.md's result set contract"
    )


def test_a_truncated_column_carries_its_alias(catalog):
    rows = execute(load(FIXTURES / "valid" / "orders_per_month.json"), catalog)
    assert list(rows[0]) == ["month", "orders"]
    # Every month starts on the first, which is what the truncation did. Reading a field
    # off the value leans on the result set contract rather than on this source: a
    # temporal value is an object from every catalog, and test_result_set.py is where that
    # is established for each of them.
    assert all(row["month"].day == 1 for row in rows)


class Unreachable:
    """A source whose client raises its own exception type, the way an SDK does for a table
    that is gone or that this credential cannot read."""

    class Denied(Exception):
        pass

    dialect = DUCKDB
    scope = Scope(levels=("catalog", "schema"), values=("vizmith", "shop"))

    def describe(self, name: str):
        raise self.Denied(f"PERMISSION_DENIED: {name}")

    def run(self, sql, parameters=None):
        raise AssertionError("nothing should reach the source")


def test_a_source_exception_the_handler_does_not_know_becomes_one_it_does():
    """Every other failure on this path arrives shaped, with a spoke saying which side to
    look at. An SDK's own type is neither ValueError nor RuntimeError, so it used to come
    out as a bare 500 for a spec that named a table the credential cannot read."""
    spec = json.loads((FIXTURES / "valid" / "revenue_by_country.json").read_text())

    with pytest.raises(RuntimeError, match="could not describe"):
        build(spec, Unreachable())


def test_the_spec_is_still_the_spec_s_fault_where_it_is():
    """ValueError passes through rather than being dressed as the source, because it
    answers 400 and the spec is what has to change. `qualify` raises it for a name outside
    the configured schema."""

    class Outside(Unreachable):
        def describe(self, name: str):
            raise ValueError(f"{name} is outside the configured schema")

    spec = json.loads((FIXTURES / "valid" / "revenue_by_country.json").read_text())

    with pytest.raises(ValueError, match="outside the configured schema"):
        build(spec, Outside())


def test_truncation_is_the_dialects_spelling_rather_than_one_written_in(catalog):
    """The builder used to write `date_trunc('month', c)` into the statement, which is two
    sources' spelling stated as though it were every source's. It reads the dialect now,
    and what it must not do is stop binding the unit's neighbours: a unit is one of the
    grammar's own keywords and everything else on that line is still a parameter."""
    spec = json.loads((FIXTURES / "valid" / "orders_per_month.json").read_text())

    sql, parameters = build(spec, catalog)

    assert 'date_trunc(\'month\', "vizmith"."shop"."orders"."order_date")' in sql
    assert parameters, "the row limit stopped being bound"


def test_a_source_that_spells_truncation_differently_gets_its_own_spelling(catalog):
    """The fifth field on `Dialect`, from the outside: the same spec against a source whose
    truncation reverses its arguments and takes the unit as a bare keyword. BigQuery is why
    this exists, and this asserts it without needing BigQuery."""

    class Elsewhere:
        dialect = replace(catalog.dialect, truncate="DATE_TRUNC({column}, {unit})")
        scope = catalog.scope

        def describe(self, name):
            return catalog.describe(name)

    spec = json.loads((FIXTURES / "valid" / "orders_per_month.json").read_text())

    sql, _ = build(spec, Elsewhere())

    assert 'DATE_TRUNC("vizmith"."shop"."orders"."order_date", month)' in sql
    assert "date_trunc(" not in sql
