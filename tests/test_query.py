import copy
import datetime as dt
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import DUCKDB, FixtureCatalog, needs_warehouse, shapes

from vizmith.catalog import Scope
from vizmith.query import build, execute, resolve
from vizmith.spec import output_columns

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))

MULTI_SERIES = FIXTURES / "valid" / "revenue_by_category_stacked.json"
REVENUE_BY_COUNTRY = FIXTURES / "valid" / "revenue_by_country.json"


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
        "the two sources disagree about what a value is, against DESIGN.md's result set contract"
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


def at(*when: int) -> dt.datetime:
    """A moment on the wall clock, which is what the builder resolves a relative value
    against. Naive on purpose and in one place: the zone is the machine's, because "today"
    is the civil day of the person asking rather than UTC's."""
    return dt.datetime(*when)  # noqa: DTZ001


NOON = at(2026, 8, 8, 14, 30, 45)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"relative": "now"}, "2026-08-08 14:30:45"),
        ({"relative": "today"}, "2026-08-08"),
        ({"relative": "start_of", "unit": "hour"}, "2026-08-08 14:00:00"),
        ({"relative": "start_of", "unit": "day"}, "2026-08-08"),
        ({"relative": "start_of", "unit": "week"}, "2026-08-03"),
        ({"relative": "start_of", "unit": "month"}, "2026-08-01"),
        ({"relative": "start_of", "unit": "quarter"}, "2026-07-01"),
        ({"relative": "start_of", "unit": "year"}, "2026-01-01"),
        ({"relative": "ago", "unit": "hour", "count": 6}, "2026-08-08 08:30:45"),
        ({"relative": "ago", "unit": "day", "count": 30}, "2026-07-09"),
        ({"relative": "ago", "unit": "week", "count": 2}, "2026-07-25"),
        ({"relative": "ago", "unit": "month", "count": 1}, "2026-07-08"),
        ({"relative": "ago", "unit": "quarter", "count": 1}, "2026-05-08"),
        ({"relative": "ago", "unit": "year", "count": 2}, "2024-08-08"),
    ],
    ids=lambda entry: entry if isinstance(entry, str) else "",
)
def test_a_relative_value_resolves_to_the_text_a_written_date_would_have_been(value, expected):
    """The whole closed set, against a clock a test holds still. Text rather than a date
    object because that is the shape a literal date already arrives in, so a relative filter
    travels the path a written one has always travelled."""
    assert resolve(value, NOON) == expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (at(2026, 3, 31, 9, 0), "2026-02-28"),
        (at(2024, 3, 31, 9, 0), "2024-02-29"),
        (at(2026, 1, 31, 9, 0), "2025-12-31"),
    ],
    ids=["into a shorter month", "into a leap February", "across a year"],
)
def test_a_month_ago_stays_inside_the_month_it_names(day, expected):
    """Calendar months, counted rather than approximated: thirty days before the 31st of
    March is not "a month ago" to anybody. Where the target month is shorter the day is
    clamped, which is the only answer that stays inside the month it names."""
    assert resolve({"relative": "ago", "unit": "month", "count": 1}, day) == expected


def test_a_relative_filter_is_bound_rather_than_written_into_the_statement(catalog):
    """The rule that does not move: nothing a spec carries reaches the SQL text. A relative
    value is resolved and then bound exactly as a literal one is, so the statement has a
    marker where the date goes and the date is in the parameters."""
    spec = load(REVENUE_BY_COUNTRY)
    spec["query"]["filters"] = [
        {"column": "orders.order_date", "op": ">=", "value": {"relative": "ago", "unit": "day", "count": 30}}
    ]

    sql, parameters = build(spec, catalog, now=NOON)

    assert "2026-07-09" not in sql, "a resolved date was written into the statement"
    assert "relative" not in sql
    assert "2026-07-09" in parameters.values()


def test_the_same_spec_compiled_on_two_days_asks_two_different_questions(catalog):
    """What #111 is about. A dashboard is specs, deliberately holding no rows so that a tile
    shows what the data says now — and a tile filtered on a literal date says what the data
    said the day it was saved, silently. The same stored spec has to move with the clock."""
    spec = load(REVENUE_BY_COUNTRY)
    spec["query"]["filters"] = [
        {"column": "orders.order_date", "op": ">=", "value": {"relative": "start_of", "unit": "month"}}
    ]

    _, august = build(spec, catalog, now=at(2026, 8, 20, 12, 0))
    _, september = build(spec, catalog, now=at(2026, 9, 2, 12, 0))

    assert "2026-08-01" in august.values()
    assert "2026-09-01" in september.values()


def test_a_relative_filter_runs_against_the_source_and_narrows_the_rows(catalog):
    """Driven rather than asserted on the SQL, because a date the source will not compare is
    a date this compiles happily and the warehouse refuses. The fixture orders run to
    2026-06-30, so a window that ends before them returns nothing and one that contains
    them returns rows — which is the comparison actually happening."""
    spec = load(REVENUE_BY_COUNTRY)
    spec["query"]["filters"] = [
        {"column": "orders.order_date", "op": ">=", "value": {"relative": "start_of", "unit": "year"}}
    ]

    inside = execute(spec, catalog, now=at(2024, 6, 1, 12, 0))
    after = execute(spec, catalog, now=at(2030, 1, 1, 12, 0))

    assert inside, "a window containing the fixture data returned nothing"
    assert after == [], "a window after the fixture data returned rows"


def having(spec: dict, *conditions: dict) -> dict:
    spec["query"]["having"] = list(conditions)
    return spec


def test_a_condition_on_a_measure_compiles_to_having_after_the_group_by(catalog):
    """The gap #110 names. Every filter compiled to WHERE, so a condition was applied before
    aggregation and "countries with revenue over a million" could not be written at all —
    not refused with a reason, absent from the grammar."""
    spec = having(
        load(REVENUE_BY_COUNTRY), {"aggregate": "revenue", "op": ">", "value": 1000000}
    )

    sql, parameters = build(spec, catalog)

    assert " HAVING " in sql
    assert sql.index("GROUP BY") < sql.index("HAVING")
    assert 1000000 in parameters.values(), "the threshold was written into the statement"
    assert "1000000" not in sql


def test_a_condition_on_a_measure_repeats_the_aggregate_rather_than_its_alias(catalog):
    """The dialects disagree about whether an output alias is in scope in HAVING —
    PostgreSQL says no, Spark and BigQuery say yes — so the expression is repeated and the
    grammar compiles the same everywhere."""
    spec = having(load(REVENUE_BY_COUNTRY), {"aggregate": "revenue", "op": ">=", "value": 1})

    sql, _ = build(spec, catalog)

    # The clause itself, not everything after it: ORDER BY refers to the alias quite
    # legitimately, because by then the select list has been produced.
    clause = re.split(r" (?:ORDER BY|LIMIT)|\)", sql[sql.index("HAVING") :])[0]
    assert "sum(" in clause
    assert '"revenue"' not in clause, "HAVING referred to an output alias"


def test_several_conditions_on_measures_are_all_required(catalog):
    spec = having(
        load(REVENUE_BY_COUNTRY),
        {"aggregate": "revenue", "op": ">", "value": 10},
        {"aggregate": "revenue", "op": "<", "value": 1000000},
    )

    sql, _ = build(spec, catalog)

    assert sql[sql.index("HAVING") :].count(" AND ") == 1


def test_a_condition_on_a_measure_applies_at_the_grouping_the_query_declares(catalog):
    """The decision #110 says has to be made. `limit_by` wraps the query in a `base` term,
    and whether the condition applies inside it or to the ranked result changes which series
    survive. It applies inside, which is what keeps `having` meaning one thing: it compares
    the measures this query produces, at the grouping this query declares."""
    spec = having(
        load(MULTI_SERIES), {"aggregate": "revenue", "op": ">", "value": 100}
    )

    sql, _ = build(spec, catalog)

    assert "WITH base AS (" in sql, "the fixture no longer ranks, so this proves nothing"
    assert sql.index("HAVING") < sql.index("), ranked AS ("), "the condition escaped base"


def test_a_condition_on_a_measure_narrows_the_rows_the_source_returns(catalog):
    """Driven rather than asserted on the SQL, because a HAVING the source will not take is
    one this compiles happily and the warehouse refuses."""
    spec = load(REVENUE_BY_COUNTRY)
    everything = execute(spec, catalog)
    biggest = max(row["revenue"] for row in everything)

    narrowed = execute(having(load(REVENUE_BY_COUNTRY), {"aggregate": "revenue", "op": ">=", "value": biggest}), catalog)

    assert len(everything) > 1
    assert len(narrowed) == 1, "the condition on the measure did not narrow the rows"
    assert narrowed[0]["revenue"] == biggest


def test_a_measure_the_query_does_not_produce_is_refused_by_the_builder_too(catalog):
    """The validator refuses it first. The builder does not take that on trust, which is the
    rule `_ranked` already keeps: what it compiles into SQL is never an assumption nobody
    checked."""
    spec = load(REVENUE_BY_COUNTRY)
    spec["query"]["having"] = [{"aggregate": "profit", "op": ">", "value": 1}]

    with pytest.raises(ValueError, match="profit"):
        build(spec, catalog)


def anyOf(spec: dict, *conditions: dict) -> dict:
    """The spec's filters, plus one disjunction of the conditions given."""
    spec["query"].setdefault("filters", []).append({"any": list(conditions)})
    return spec


def test_a_disjunction_compiles_to_a_bracketed_or_inside_the_conjunction(catalog):
    """The gap #112 names. Every condition was joined with AND, so "shipped, or worth more
    than five hundred" had no form in the grammar — and a model trying to write one spent
    three billed attempts finding that out.

    The brackets are the correctness of it. `a AND b OR c` is not what these three
    conditions mean, and the difference is a statement that compiles, runs, and answers a
    different question."""
    spec = anyOf(
        load(REVENUE_BY_COUNTRY),
        {"column": "orders.status", "op": "=", "value": "shipped"},
        {"column": "orders.total", "op": ">", "value": 500},
    )

    sql, parameters = build(spec, catalog)

    clause = sql[sql.index(" WHERE ") : sql.index(" GROUP BY ")]
    assert " OR " in clause
    assert re.search(r"\(\S[^()]* OR [^()]*\S\)", clause), clause
    assert clause.count(" AND ") == 2, "the fixture's own two filters still conjoin"
    assert "shipped" in parameters.values(), "a disjunct's value skipped the binding"
    assert 500 in parameters.values()
    assert "shipped" not in sql


def test_a_disjunction_widens_the_rows_rather_than_narrowing_them(catalog):
    """Driven, because the brackets are the whole point and a missing pair still compiles.
    Without them the OR would swallow the conjunction and the result would be the rows
    matching the last disjunct rather than the rows matching either, on top of everything
    the other filters already said."""
    both = load(REVENUE_BY_COUNTRY)
    both["query"]["filters"].append({"column": "orders.status", "op": "=", "value": "shipped"})
    only_shipped = execute(both, catalog)

    either = anyOf(
        load(REVENUE_BY_COUNTRY),
        {"column": "orders.status", "op": "=", "value": "shipped"},
        {"column": "orders.status", "op": "=", "value": "delivered"},
    )

    total = {row["country"]: row["revenue"] for row in execute(either, catalog)}
    shipped = {row["country"]: row["revenue"] for row in only_shipped}

    # Both queries keep the top ten countries, so the two sets are not the same countries.
    # What holds for every country in both is that widening the status cannot take revenue
    # away, and for at least one it has to add some.
    common = set(total) & set(shipped)
    assert common, "the fixture returned nothing, so this proves nothing"
    assert all(total[country] >= shipped[country] for country in common)
    assert any(total[country] > shipped[country] for country in common)


def test_a_disjunction_of_null_checks_needs_no_value_either(catalog):
    """A condition inside `any` is the same condition, so every operator reaches it. The
    two that take no value are the ones a flattening bug would trip over first."""
    spec = anyOf(
        load(REVENUE_BY_COUNTRY),
        {"column": "orders.order_date", "op": "is_null"},
        {"column": "orders.total", "op": "in", "value": [100, 200]},
    )

    sql, parameters = build(spec, catalog)

    assert "IS NULL OR" in sql
    assert 100 in parameters.values() and 200 in parameters.values()


PER_ITEM = FIXTURES / "valid" / "value_per_item_by_status.json"


def computed(spec: dict, expression: dict) -> dict:
    """The fixture's measure, taken over the expression given rather than over a column."""
    spec["query"]["aggregates"][0] = {"fn": "sum", "expression": expression, "as": "revenue"}
    return spec


def test_a_computed_column_compiles_to_one_bracketed_operation(catalog):
    """Bracketed because it lands inside an aggregate and inside a GROUP BY term, and
    `sum(a + b)` and `sum(a) + b` are two questions with the same text between brackets."""
    spec = computed(load(REVENUE_BY_COUNTRY), {"left": "orders.total", "op": "*", "right": "orders.item_count"})

    sql, _ = build(spec, catalog)

    assert re.search(r'sum\(\S*"total" \* \S*"item_count"\)', sql), sql
    assert sql.count("(") == sql.count(")")


def test_a_number_in_an_expression_is_bound_like_every_other_value(catalog):
    """The rule the whole builder keeps. A literal written into the statement would be the
    one value in a query that the source reads as text this server wrote."""
    spec = computed(load(REVENUE_BY_COUNTRY), {"left": "orders.total", "op": "*", "right": 1.21})

    sql, parameters = build(spec, catalog)

    assert "1.21" not in sql
    assert 1.21 in parameters.values()


def test_a_number_on_the_left_is_a_different_question_from_one_on_the_right(catalog):
    """'-' and '/' do not commute, which is the whole reason an operand may be either."""
    spec = computed(load(REVENUE_BY_COUNTRY), {"left": 1, "op": "-", "right": "orders.total"})

    sql, parameters = build(spec, catalog)

    marker = next(name for name, value in parameters.items() if value == 1)
    assert sql.index(marker) < sql.index('"total"')


def test_dividing_by_nothing_has_no_answer_rather_than_the_source_s_opinion(catalog):
    """The dialects disagree about a zero divisor — a NULL here, an error there — and a
    chart whose bars depend on which warehouse ran it is the failure this design is about."""
    spec = computed(load(REVENUE_BY_COUNTRY), {"left": "orders.total", "op": "/", "right": "orders.item_count"})

    sql, _ = build(spec, catalog)

    assert "NULLIF(" in sql
    rows = execute(spec, catalog)
    assert rows, "the fixture returned nothing, so this proves nothing"


def test_a_computed_measure_runs_and_answers_what_the_arithmetic_says(catalog):
    """The fixture, executed. `avg(total / item_count)` against the same rows, worked out
    twice: once by the warehouse through the spec, once here from the rows themselves."""
    per_item = {row["status"]: row["per_item"] for row in execute(load(PER_ITEM), catalog)}

    lines = execute(
        {
            "spec_version": "1",
            "query": {
                "from": "orders",
                "filters": [{"column": "total", "op": "is_not_null"}],
                "select": [
                    {"column": "orders.status", "as": "status"},
                    {"column": "orders.total", "as": "total"},
                    {"column": "orders.item_count", "as": "item_count"},
                ],
                # Above the fixture's row count on purpose: the two queries have to read
                # the same rows, and a cap that cut one of them would be comparing an
                # average of everything with an average of the first few thousand.
                "limit": 10_000,
            },
            "chart": {
                "mark": "point",
                "encoding": {
                    "x": {"field": "status", "type": "nominal"},
                    "y": {"field": "total", "type": "quantitative"},
                },
            },
        },
        catalog,
    )

    counted: dict[str, list[float]] = {}
    for row in lines:
        if row["item_count"]:
            counted.setdefault(row["status"], []).append(float(row["total"]) / row["item_count"])
    worked_out = {status: sum(each) / len(each) for status, each in counted.items()}

    assert set(per_item) == set(worked_out)
    for status, average in worked_out.items():
        assert per_item[status] == pytest.approx(average, rel=1e-6)


def test_a_computed_column_can_be_grouped_by_and_named_by_its_alias(catalog):
    """An expression has no column name to fall back on, so the alias is the output column
    and everything that references one — the order, the chart's channels — reads it."""
    spec = load(REVENUE_BY_COUNTRY)
    spec["query"]["group_by"] = [
        {"expression": {"left": "orders.total", "op": "-", "right": "orders.item_count"}, "as": "spread"}
    ]
    spec["query"]["order_by"] = [{"column": "revenue", "direction": "desc"}]
    spec["chart"]["encoding"]["x"] = {"field": "spread", "type": "quantitative"}

    sql, _ = build(spec, catalog)

    assert output_columns(spec["query"]) == ["spread", "revenue"]
    assert re.search(r'GROUP BY \(\S*"total" - \S*"item_count"\)', sql), sql
