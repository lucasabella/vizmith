import json
from pathlib import Path

import pytest

from vizmith.spec import SCHEMA_PATH, validate_filters, validate_spec
from vizmith.spec.validate import names_table, output_columns

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))

# The wording of an error is part of the interface: the HTTP API returns these
# unchanged and the retry loop feeds them back to a model. Asserting only that a
# spec was rejected passes when it is rejected for the wrong reason, which is the
# failure that actually happens.
EXPECTED_ERROR = {
    "unknown_mark.json": "chart/mark: 'donut' is not one of",
    "missing_limit.json": "query: 'limit' is a required property",
    "sql_injection_in_column.json": (
        "query/aggregates/0/column: 'total FROM orders; DROP TABLE orders --' does not match"
    ),
    "sum_without_column.json": "query/aggregates/0: 'column' is a required property",
    "in_filter_with_scalar_value.json": "query/filters/0/value: 'shipped' is not of type 'array'",
    "unqualified_ref_with_join.json": (
        "query.group_by: 'country' must be qualified with a table name when the query joins"
    ),
    "filter_on_unjoined_table.json": (
        "query.filters: 'customers.country' refers to table 'customers', which is not in the query"
    ),
    "select_with_group_by.json": "query: 'select' cannot be combined with 'group_by' or 'aggregates'",
    "group_by_without_aggregates.json": "query: 'group_by' without 'aggregates' produces no measure",
    "no_select_or_group_by.json": "query: needs 'select', or 'group_by' and 'aggregates'",
    "duplicate_output_column.json": "query: output column 'value' is produced more than once",
    "order_by_not_in_output.json": (
        "query.order_by: 'revenue' is not an output column of the query"
    ),
    "encoding_field_not_in_output.json": (
        "chart.encoding.y: 'total' is not an output column of the query"
    ),
    "limit_by_column_not_in_output.json": (
        "query.limit_by.column: 'brand' is not an output column of the query"
    ),
    "limit_by_ranked_by_a_dimension.json": (
        "query.limit_by.by: 'status' is not one of the query's aggregate aliases, and "
        "ranking 'country' needs a measure to rank it by"
    ),
    "any_with_a_condition_beside_it.json": (
        "query/filters/0: Additional properties are not allowed "
        "('column', 'op', 'value' were unexpected)"
    ),
    "any_of_one_condition.json": "query/filters/0/any: [{'column': 'status', 'op': '=', 'value': 'pending'}] is too short",
    "computed_column_and_a_column.json": (
        "query.aggregates: 'doubled' has both a 'column' and an 'expression', and only one "
        "of them can be what it reads"
    ),
    "computed_column_of_two_numbers.json": (
        "query.aggregates: 'constant' computes 2 * 3, which names no column and is the same "
        "number in every row"
    ),
    "truncate_on_a_computed_column.json": (
        "query.group_by: 'difference' computes a number, and 'truncate' rounds a date to a "
        "unit, so there is nothing here for it to round"
    ),
    "format_on_a_dimension.json": (
        "chart.encoding.x: 'format' says how a number reads, and 'country' is bound as "
        "'nominal'. Only a quantitative channel carries one"
    ),
    "value_axis_bound_to_a_dimension.json": (
        "chart.encoding.y: 'country' is bound to the value axis as 'nominal', but the value "
        "axis carries a measure, so its type is 'quantitative'"
    ),
    "limit_by_ranked_by_itself.json": (
        "query.limit_by: 'column' and 'by' must differ, ranking needs a measure"
    ),
    "multi_series_without_limit_by.json": "query: a multi series chart needs 'limit_by'",
    "measure_against_itself.json": (
        "chart.encoding: 'revenue' is bound to both 'x' and 'y', which plots a measure "
        "against itself"
    ),
    "figure_with_group_by.json": (
        "chart.encoding: a chart without 'x' draws one figure, so its query cannot have "
        "'select' or 'group_by'"
    ),
    "figure_with_colour.json": (
        "chart.encoding: 'color' needs an 'x', because one figure has nothing to colour"
    ),
    "having_names_a_measure_the_query_lacks.json": (
        "query.having: 'profit' is not one of this query's aggregate aliases"
    ),
    "relative_value_with_a_key_it_does_not_read.json": (
        "query.filters: a relative value of 'now' takes neither, so 'unit' does nothing "
        "here and the filter does not mean what it says"
    ),
    "relative_start_without_a_unit.json": ("'unit' is a required property"),
    "window_over_a_dimension.json": (
        "query.windows: 'share_of_revenue' is taken over 'country', which is not one of this "
        "query's aggregate aliases ['revenue']. A window compares a measure across rows"
    ),
    "window_on_a_figure.json": (
        "query.windows: 'share_of_revenue' reads a row against the other rows, and a query "
        "that groups by nothing produces one row"
    ),
    "window_partitioned_by_a_measure.json": (
        "query.windows: 'place' partitions by 'revenue', which is not among this query's "
        "dimensions ['country', 'status']"
    ),
    "window_partitioned_by_every_dimension.json": (
        "query.windows: 'share_of_revenue' partitions by every dimension the query has, so "
        "each partition holds one row and there is nothing to read it against"
    ),
    "window_crossing_a_dimension.json": (
        "query.windows: 'revenue_so_far' walks 'month' and the query also groups by "
        "'status', so one step of the window crosses from one value of 'status' to another"
    ),
    "previous_along_a_measure.json": (
        "query.windows: 'month_before' walks 'revenue', which is a measure. Two rows can hold "
        "the same measure and which of them comes first is then the source's to decide"
    ),
    # The schema's half: a rank is taken over its own measure, so there is nowhere for a
    # column to be walked and `along` is refused rather than ignored.
    "rank_walking_a_column.json": "should not be valid under {'required': ['along']}",
    "limit_by_ranks_a_window.json": (
        "query.limit_by.column: 'share_of_revenue' is a window, which is worked out over the "
        "rows the ranking keeps and so does not exist where the ranking runs"
    ),
    "ambiguous_table_qualifier.json": (
        "query.group_by: 'orders.status' is ambiguous, 'orders' names "
        "'vizmith.archive.orders' and 'vizmith.shop.orders', so qualify it with more segments"
    ),
}


def load(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_fixture_has_no_errors(path):
    assert validate_spec(load(path)) == []


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_fixture_is_rejected_for_its_own_reason(path):
    errors = validate_spec(load(path))

    assert errors, "fixture was accepted"
    expected = EXPECTED_ERROR[path.name]
    assert any(expected in error for error in errors), (
        f"expected {expected!r}, got {errors!r}"
    )


def test_every_invalid_fixture_has_a_stated_expectation():
    on_disk = {path.name for path in INVALID}

    assert on_disk == set(EXPECTED_ERROR), (
        "a fixture without an expected error would pass by being rejected for any reason"
    )


def test_valid_set_covers_every_mark_the_schema_allows():
    allowed = set(json.loads(SCHEMA_PATH.read_text())["$defs"]["chart"]["properties"]["mark"]["enum"])
    covered = {load(path)["chart"]["mark"] for path in VALID}

    assert covered == allowed


def test_valid_set_covers_every_window_the_schema_allows():
    """The same gate the marks get, for the same reason. A window the schema allows and no
    fixture uses is one nothing compiles, nothing runs against DuckDB, and nothing renders —
    six functions with one template each is exactly where a wrong one hides."""
    allowed = set(json.loads(SCHEMA_PATH.read_text())["$defs"]["window"]["properties"]["fn"]["enum"])
    covered = {
        window["fn"] for path in VALID for window in load(path)["query"].get("windows", [])
    }

    assert covered == allowed


def test_a_schema_failure_hides_the_semantic_pass():
    spec = load(FIXTURES / "valid" / "revenue_by_country.json")
    spec["chart"]["mark"] = "donut"
    spec["query"]["order_by"] = [{"column": "not_an_output_column"}]

    errors = validate_spec(spec)

    assert any("'donut' is not one of" in error for error in errors)
    assert not any("is not an output column" in error for error in errors)


def test_a_column_reference_carrying_sql_never_reaches_the_semantic_pass():
    errors = validate_spec(load(FIXTURES / "invalid" / "sql_injection_in_column.json"))

    assert len(errors) == 1
    assert errors[0].startswith("query/aggregates/0/column: ")
    assert "does not match" in errors[0], "the identifier pattern should be what rejects it"
    assert not any("refers to table" in error for error in errors)


def test_a_null_check_carrying_a_value_is_rejected_in_words():
    spec = load(FIXTURES / "valid" / "orders_per_month.json")
    spec["query"]["filters"] = [{"column": "order_date", "op": "is_null", "value": 1}]

    assert validate_spec(spec) == [
        "query.filters: 'is_null' takes no value, but one was given for 'order_date'"
    ]


@pytest.mark.parametrize("spec", [None, [], "spec", 7, {"spec_version": "2"}])
def test_a_spec_of_the_wrong_shape_returns_errors_rather_than_raising(spec):
    assert validate_spec(spec)


def formatted(**format_: object) -> dict:
    """A valid spec whose measure carries the format under test."""
    spec = load(FIXTURES / "valid" / "orders_per_month.json")
    spec["chart"]["encoding"]["y"]["format"] = format_
    return spec


@pytest.mark.parametrize(
    "format_",
    [
        {"kind": "number"},
        {"kind": "number", "decimals": 0, "group": False},
        {"kind": "percent", "decimals": 1},
        {"kind": "currency", "symbol": "€"},
        {"kind": "unit", "symbol": "kg", "decimals": 2},
    ],
    ids=lambda f: str(f["kind"]),
)
def test_the_four_ways_a_number_reads_are_accepted(format_):
    assert validate_spec(formatted(**format_)) == []


@pytest.mark.parametrize(
    ("format_", "because"),
    [
        ({}, "'kind' is a required property"),
        ({"kind": "scientific"}, "is not one of"),
        ({"kind": "number", "symbol": "kg"}, "should not be valid"),
        ({"kind": "currency"}, "'symbol' is a required property"),
        ({"kind": "unit"}, "'symbol' is a required property"),
        ({"kind": "number", "decimals": 7}, "is greater than the maximum"),
        ({"kind": "number", "decimals": -1}, "is less than the minimum"),
        ({"kind": "number", "places": 2}, "Additional properties are not allowed"),
    ],
    ids=["no kind", "unknown kind", "symbol without one", "currency", "unit", "7dp", "-1dp", "extra key"],
)
def test_a_format_outside_the_vocabulary_is_refused(format_, because):
    """The vocabulary is closed on purpose: a format string is a small language, and a
    model that can write one is writing something the renderer then executes. Each of these
    is a way of reaching past the four kinds, and the schema refuses all of them — a symbol
    on a kind that places none included, since where it would be drawn is undefined."""
    errors = validate_spec(formatted(**format_))

    assert errors, f"{format_} should not validate"
    assert any(because in error for error in errors), errors


def test_a_format_on_a_dimension_is_refused_in_words():
    """The rule the schema cannot state, because it depends on a sibling property. A format
    describes a number, so a channel bound to a category has nothing for it to apply to."""
    spec = load(FIXTURES / "valid" / "revenue_by_country.json")
    spec["chart"]["encoding"]["x"]["format"] = {"kind": "percent"}

    assert validate_spec(spec) == [
        (
            "chart.encoding.x: 'format' says how a number reads, and 'country' is bound as "
            "'nominal'. Only a quantitative channel carries one"
        )
    ]


class TestAWindowReadsTheRowsItWasWrittenFor:
    """The rules the schema cannot state, because every one of them is about a name that
    only exists once the rest of the query has been read.

    Each of these is a window that compiles, runs, and answers a question nobody asked —
    which is the class of failure this project spends its effort on, and the reason the
    grammar refuses rather than computing something plausible."""

    def monthly(self, **window: object) -> dict:
        """A month by month query carrying one window, which is the shape most of these
        rules are about."""
        spec = load(FIXTURES / "valid" / "revenue_running_total.json")
        spec["query"]["windows"] = [{"of": "revenue", "as": "read", **window}]
        spec["chart"]["encoding"]["y"] = {"field": "read", "type": "quantitative"}
        return spec

    def test_a_running_total_walks_the_dimension_the_query_groups_by(self):
        assert validate_spec(self.monthly(fn="running_total", along="month")) == []

    def test_a_running_total_may_walk_its_own_measure_because_its_frame_survives_a_tie(self):
        """A cumulative total under a RANGE frame gives every tied row the total through the
        tie, so the answer does not depend on the order the source returned them in. That is
        what makes the descending-measure walk — a Pareto curve — askable at all."""
        assert validate_spec(self.monthly(fn="running_total", along="revenue", direction="desc")) == []

    @pytest.mark.parametrize("fn", ["previous", "difference", "change"])
    def test_a_window_that_reaches_back_a_row_may_not_walk_a_measure(self, fn):
        errors = validate_spec(self.monthly(fn=fn, along="revenue"))

        assert errors and f"so '{fn}' walks a dimension" in errors[0]

    def test_a_window_that_walks_what_it_partitions_by_never_moves(self):
        errors = validate_spec(self.monthly(fn="previous", along="month", partition_by=["month"]))

        assert errors == [
            (
                "query.windows: 'read' walks 'month' and partitions by it as well, so every step "
                "stays inside one value of it and the window never moves"
            )
        ]

    def test_a_window_walking_something_the_query_does_not_produce_is_refused_by_name(self):
        errors = validate_spec(self.monthly(fn="running_total", along="quarter"))

        assert errors == [
            "query.windows: 'read' walks 'quarter', which is not an output column of the query"
        ]

    def test_a_running_total_walking_a_measure_still_leaves_one_dimension_to_walk_over(self):
        """The Pareto walk is over the rows of one dimension in the order of their measure.
        Where two dimensions are left, a partition holds a row per combination of them and
        each step of the total crosses between them, which is the same crossing the walk
        along a dimension is refused for."""
        spec = load(FIXTURES / "valid" / "category_rank_by_country.json")
        spec["query"]["windows"] = [
            {"fn": "running_total", "of": "revenue", "along": "revenue", "as": "read"}
        ]

        errors = validate_spec(spec)

        assert errors == [
            (
                "query.windows: 'read' walks the measure 'revenue' over rows that are one per "
                "combination of 'country' and 'category', so each step crosses from one of them "
                "to another. Partition by all of them but one"
            )
        ]

    def test_a_window_cannot_be_read_along_another_window(self):
        """Which is an output column, so the sentence below it — 'not an output column' —
        would have been false. They are all worked out in one pass over the same rows, so
        the one being walked does not exist yet where the walking happens."""
        spec = self.monthly(fn="running_total", along="month")
        spec["query"]["windows"].append(
            {"fn": "previous", "of": "revenue", "along": "read", "as": "before"}
        )

        errors = validate_spec(spec)

        assert errors == [
            (
                "query.windows: 'before' walks 'read', which is a window. They are all "
                "worked out in one pass over the same rows, so one cannot be read along another"
            )
        ]

    def test_a_share_is_over_the_whole_result_where_nothing_partitions_it(self):
        assert validate_spec(self.monthly(fn="share")) == []

    def test_a_window_alias_that_collides_with_a_measure_is_an_output_produced_twice(self):
        spec = self.monthly(fn="share")
        spec["query"]["windows"][0]["as"] = "revenue"
        spec["chart"]["encoding"]["y"] = {"field": "revenue", "type": "quantitative"}

        assert validate_spec(spec) == ["query: output column 'revenue' is produced more than once"]

    def test_a_window_is_an_output_column_a_chart_may_bind_and_an_order_may_name(self):
        spec = self.monthly(fn="running_total", along="month")
        spec["query"]["order_by"] = [{"column": "read", "direction": "desc"}]

        assert validate_spec(spec) == []

    def test_each_window_is_refused_for_the_first_thing_wrong_with_it_and_not_for_three(self):
        """A window over something that is not a measure has no measure to partition or walk,
        so the rules below it would report the same mistake twice more. A retry loop reading
        three sentences about one mistake is one that changes three things."""
        errors = validate_spec(self.monthly(fn="running_total", of="month", along="revenue"))

        assert len(errors) == 1
        assert "is taken over 'month'" in errors[0]


def qualified(column: str) -> dict:
    """A two table query over the same schema, with one column reference to vary."""
    return {
        "spec_version": "1",
        "query": {
            "from": "vizmith.shop.orders",
            "joins": [
                {
                    "table": "vizmith.shop.customers",
                    "on": [
                        {
                            "left": "vizmith.shop.orders.customer_id",
                            "right": "vizmith.shop.customers.id",
                        }
                    ],
                }
            ],
            "group_by": [{"column": column, "as": "grouped"}],
            "aggregates": [{"fn": "count", "as": "order_count"}],
            "limit": 10,
        },
        "chart": {
            "mark": "bar",
            "encoding": {
                "x": {"field": "grouped", "type": "nominal"},
                "y": {"field": "order_count", "type": "quantitative"},
            },
        },
    }


@pytest.mark.parametrize(
    "column",
    [
        "vizmith.shop.customers.country",
        "shop.customers.country",
        "customers.country",
    ],
)
def test_any_trailing_part_of_a_table_reference_names_it(column):
    assert validate_spec(qualified(column)) == []


def test_a_qualifier_naming_no_table_in_the_query_is_still_rejected():
    assert validate_spec(qualified("vizmith.archive.customers.country")) == [
        (
            "query.group_by: 'vizmith.archive.customers.country' refers to table "
            "'vizmith.archive.customers', which is not in the query"
        )
    ]


def test_the_last_segment_is_the_column_however_long_the_reference():
    spec = qualified("vizmith.shop.customers.country")
    del spec["query"]["group_by"][0]["as"]
    spec["chart"]["encoding"]["x"]["field"] = "country"

    assert validate_spec(spec) == []


def test_a_left_join_fixture_exists_because_the_query_builder_needs_one():
    joins = [join for path in VALID for join in load(path)["query"].get("joins", [])]

    assert any(join.get("type") == "left" for join in joins)


MIRRORS = Path(__file__).parent / "fixtures" / "mirrors"
REFERENCES = json.loads((MIRRORS / "references.json").read_text())


@pytest.mark.parametrize("case", REFERENCES["names_table"], ids=lambda case: case["reference"])
def test_a_reference_names_a_table_the_way_the_wells_expect(case):
    """The wells resolve a reference before they write one into a spec, which is a second
    copy of this rule. A disagreement is a spec that looks right in the browser and is
    refused here, so both sides are asked these cases."""
    assert names_table(case["table"], case["reference"]) is case["names"]


@pytest.mark.parametrize("case", REFERENCES["output_columns"], ids=lambda case: case["why"])
def test_the_output_columns_are_what_the_wells_expect(case):
    """The result set contract, which the builder compiles and the browser predicts when it
    names a field in an encoding."""
    assert output_columns(case["query"]) == case["columns"]


class TestAFilterListHeldOutsideAQuery:
    """A dashboard's filters, judged before they are applied to anything.

    They are the one place the grammar's `filter` appears without a query around it, so the
    rules that resolve a column against a `from` cannot run and the rules about the
    condition itself still must. What is checked here is that the second set really does
    run, and that the first is replaced by something rather than dropped."""

    def test_a_condition_the_grammar_allows_inside_a_query_is_allowed_here(self):
        assert validate_filters([{"column": "shop.orders.status", "op": "=", "value": "shipped"}]) == []

    def test_a_disjunction_is_allowed_here_too_since_it_is_one_filter(self):
        assert (
            validate_filters(
                [
                    {
                        "any": [
                            {"column": "shop.orders.status", "op": "=", "value": "shipped"},
                            {"column": "shop.orders.status", "op": "=", "value": "packed"},
                        ]
                    }
                ]
            )
            == []
        )

    def test_a_column_with_no_table_is_refused_because_it_would_mean_a_different_one_per_tile(self):
        errors = validate_filters([{"column": "status", "op": "=", "value": "shipped"}])

        assert errors and "names no table" in errors[0]

    def test_is_null_with_a_value_is_refused_here_the_way_it_is_inside_a_query(self):
        errors = validate_filters(
            [{"column": "shop.orders.shipped_at", "op": "is_null", "value": None}]
        )

        assert errors == [
            "filters: 'is_null' takes no value, but one was given for 'shop.orders.shipped_at'"
        ]

    def test_a_relative_value_carrying_a_key_its_token_ignores_is_refused_here_too(self):
        errors = validate_filters(
            [
                {
                    "column": "shop.orders.order_date",
                    "op": ">=",
                    "value": {"relative": "now", "unit": "month"},
                }
            ]
        )

        assert errors and errors[0].startswith("filters: a relative value of 'now'")

    def test_the_message_names_where_the_filters_are_rather_than_a_query_that_has_none(self):
        """The same list is a query's `filters` and a dashboard's, and a message naming the
        wrong one sends somebody to look inside a spec for a filter that is on the
        dashboard around it."""
        errors = validate_filters(
            [{"column": "shop.orders.shipped_at", "op": "is_not_null", "value": 1}]
        )

        assert errors and not errors[0].startswith("query.")

    def test_something_that_is_not_a_list_is_refused_rather_than_read(self):
        assert validate_filters({"column": "shop.orders.status", "op": "=", "value": "x"}) != []

    def test_the_cap_on_a_query_s_filters_is_the_cap_here(self):
        """Read off the schema rather than repeated, so the two cannot disagree: a
        dashboard that could hold more filters than a query could carry would be one whose
        every tile refuses at the moment it is applied."""
        cap = json.loads(SCHEMA_PATH.read_text())["$defs"]["query"]["properties"]["filters"]["maxItems"]
        one = {"column": "shop.orders.status", "op": "=", "value": "shipped"}

        assert validate_filters([one] * cap) == []
        assert validate_filters([one] * (cap + 1)) != []
