import json
from pathlib import Path

import pytest

from vizmith.spec import SCHEMA_PATH, validate_spec
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
