import inspect
import json
import typing
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_ask import ScriptedModel

from vizmith import evals
from vizmith.critique import ARC_SLICES, Critique, critique, findings, misreads, prompt, reads
from vizmith.profiler import TableProfile, profile_table

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
REVENUE_BY_COUNTRY = json.loads((FIXTURES / "valid" / "revenue_by_country.json").read_text())
SCANS_PER_LOCATION = json.loads((FIXTURES / "valid" / "scans_per_location.json").read_text())
STACKED = json.loads((FIXTURES / "valid" / "revenue_by_category_stacked.json").read_text())
TOTAL_REVENUE = json.loads((FIXTURES / "valid" / "total_revenue.json").read_text())


def marked(spec: dict, mark: str) -> dict:
    """The same spec drawn with another mark. A copy, because the tests below compare a
    suggestion against the spec it was made about."""
    changed = json.loads(json.dumps(spec))
    changed["chart"]["mark"] = mark
    return changed


@pytest.fixture(scope="module")
def tables(fixture_db):
    from conftest import FixtureCatalog

    catalog = FixtureCatalog(fixture_db)
    return [profile_table(catalog, name) for name in catalog.tables()]


def test_a_chart_the_rules_do_not_refuse_has_nothing_said_about_it(tables):
    """The narrow line, in one test. What a critique may say is what is refusable, so a
    chart nothing refuses gets nothing, rather than an improvement somebody's taste made
    up."""
    assert findings(REVENUE_BY_COUNTRY, tables) == []
    assert findings(TOTAL_REVENUE, tables) == []


def test_nothing_is_asked_where_there_is_nothing_to_say(tables):
    """A model asked to improve a chart that is fine will improve it, and the bill is for
    a preference nobody can score."""
    model = ScriptedModel(json.dumps(marked(REVENUE_BY_COUNTRY, "line")))

    said = critique(REVENUE_BY_COUNTRY, tables, model)

    assert said == Critique()
    assert said.spec is None and not said.asked
    assert model.prompts == [], "no request was made"


def test_an_arc_with_more_slices_than_can_be_read_is_found_from_the_profiles(tables):
    """The harness judges this against the rows it fetched. This judges it before the query
    runs, which is the whole point of doing it here: the refusal arrives before the
    warehouse has been paid for the chart nobody can read."""
    found = findings(marked(SCANS_PER_LOCATION, "arc"), tables)

    assert len(found) == 1
    assert f"{ARC_SLICES} is the most that can" in found[0].says


def test_a_finding_says_where_its_number_came_from(tables):
    """A profile bounds a row count rather than counting one, and a distinct count is
    usually an estimate. A reader who cannot tell a bound from a fact reads it as a fact."""
    found = findings(marked(REVENUE_BY_COUNTRY, "arc"), tables)

    assert found[0].bound.startswith("the query keeps at most 10 rows")
    assert "customers.country has 15 distinct values" in found[0].bound
    assert "approximately" in found[0].bound
    assert str(found[0]).startswith(found[0].says)


def test_an_arc_over_few_enough_values_is_not_refused_for_its_limit(tables):
    """`limit` alone is not a bound worth acting on. The returns question keeps more rows
    than an arc can hold slices and asks about a column with a handful of values, so an arc
    of it is readable and a finding built out of the limit would name a problem that is not
    there."""
    spec = json.loads((FIXTURES / "valid" / "returns_share_by_reason.json").read_text())

    assert spec["query"]["limit"] > ARC_SLICES
    assert findings(marked(spec, "arc"), tables) == []


def test_a_truncated_date_is_refused_for_its_axis_rather_than_for_a_count(tables):
    """Truncation produces fewer buckets than the column has values, by an amount nothing
    here knows, so counting the column's values would put a made up number in front of a
    person. An arc over time is refused for the axis anyway, which is the better sentence."""
    per_month = json.loads((FIXTURES / "valid" / "orders_per_month.json").read_text())

    found = findings(marked(per_month, "arc"), tables)

    assert [finding.says for finding in found] == [
        "an arc has no order, and a time axis is nothing but order"
    ]
    assert found[0].bound == ""


def test_nothing_is_claimed_where_the_profiles_do_not_bound_the_rows(tables):
    """A dimension the profiles cannot resolve leaves the arc judged on its axis alone,
    rather than on the query's limit, which bounds nothing useful."""
    spec = marked(REVENUE_BY_COUNTRY, "arc")

    assert findings(spec, []) == [], "no profiles, no claim"
    assert findings(spec, tables), "the same spec with the profiles behind it"


def test_the_rule_is_the_one_the_harness_scores_a_mark_with():
    """Two implementations would be a harness that refuses a chart the assistant approves
    of, which is the failure the eval layer exists to prevent rather than commit."""
    chart = {
        "mark": "line",
        "encoding": {
            "x": {"field": "country", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
    }

    assert evals.indefensible({"chart": chart}, [{}] * 4) == misreads(chart, 4)
    assert misreads(chart, 4), "and it is not empty, so the test above compares something"


def test_a_suggestion_comes_back_validated(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")
    model = ScriptedModel(json.dumps(SCANS_PER_LOCATION))

    said = critique(asked, tables, model)

    assert said.spec == SCANS_PER_LOCATION
    assert said.attempts == 1 and said.asked
    assert said.errors == ()
    assert [str(finding) for finding in said.findings] == [str(f) for f in findings(asked, tables)]


def test_a_suggestion_that_changes_the_query_is_refused(tables):
    """The query is the question. A suggestion that rewrote a filter would answer a
    different one and draw rows the person never asked for, under a chart they did."""
    asked = marked(SCANS_PER_LOCATION, "arc")
    rewritten = json.loads(json.dumps(SCANS_PER_LOCATION))
    rewritten["query"]["limit"] = ARC_SLICES

    said = critique(asked, tables, ScriptedModel(*[json.dumps(rewritten)] * 3))

    assert said.spec is None
    assert any("does not change it" in error for error in said.errors)
    assert "query came back different" in said.errors[0]


def test_a_suggestion_that_changes_anything_but_the_chart_is_refused(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")
    retitled = {**SCANS_PER_LOCATION, "title": "Something else"}

    said = critique(asked, tables, ScriptedModel(*[json.dumps(retitled)] * 3))

    assert said.spec is None
    assert "title came back different" in said.errors[0]


def test_a_suggestion_the_same_rule_refuses_is_not_a_suggestion(tables):
    """Trading one refusal for another has answered nothing, and it is the failure a model
    asked to change a mark makes most easily."""
    asked = marked(SCANS_PER_LOCATION, "arc")
    still_wrong = marked(SCANS_PER_LOCATION, "line")

    said = critique(asked, tables, ScriptedModel(*[json.dumps(still_wrong)] * 3))

    assert said.spec is None
    assert "the same rule refuses the suggestion" in said.errors[0]
    assert "gaps between its values" in said.errors[0]


def test_a_chart_that_came_back_unchanged_is_refused(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")

    said = critique(asked, tables, ScriptedModel(*[json.dumps(asked)] * 3))

    assert said.spec is None
    assert said.errors == ("the chart came back unchanged, which is not a suggestion",)


def test_a_rejected_suggestion_is_asked_again_with_what_refused_it(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")
    model = ScriptedModel(json.dumps(marked(SCANS_PER_LOCATION, "line")), json.dumps(SCANS_PER_LOCATION))

    said = critique(asked, tables, model)

    assert said.spec == SCANS_PER_LOCATION
    assert said.attempts == 2
    assert "gaps between its values" in model.prompts[1]
    assert "gaps between its values" not in model.prompts[0]


def test_an_answer_that_is_not_json_is_a_failed_attempt_rather_than_a_crash(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")
    model = ScriptedModel("Sure! Try a bar chart.", json.dumps(SCANS_PER_LOCATION))

    said = critique(asked, tables, model)

    assert said.spec == SCANS_PER_LOCATION
    assert "was not JSON" in model.prompts[1]


def test_a_suggestion_that_does_not_validate_is_refused_in_the_validator_s_words(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")
    broken = json.loads(json.dumps(SCANS_PER_LOCATION))
    del broken["query"]["limit"]

    said = critique(asked, tables, ScriptedModel(*[json.dumps(broken)] * 3))

    assert said.spec is None
    assert any("'limit' is a required property" in error for error in said.errors)


def test_the_attempts_are_bounded(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")
    model = ScriptedModel(*[json.dumps(asked)] * 10)

    said = critique(asked, tables, model, attempts=2)

    assert said.spec is None
    assert said.attempts == 2 and len(model.prompts) == 2


def test_the_prompt_carries_the_tables_the_query_reads_and_no_others(tables):
    """A critique is about a spec that already chose its tables, so there is no selection to
    make: what goes in is what the query names, which is what bounds this prompt."""
    read = reads(STACKED, tables)
    written = prompt(STACKED, read, findings(marked(STACKED, "line"), tables))

    assert {table.table for table in read} == {
        "vizmith.shop.order_items",
        "vizmith.shop.orders",
        "vizmith.shop.customers",
        "vizmith.shop.products",
    }
    assert "vizmith.shop.shipment_scans" not in written


def test_the_prompt_carries_the_spec_and_what_the_rule_said(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")

    written = prompt(asked, reads(asked, tables), findings(asked, tables))

    assert json.dumps(asked, indent=2, sort_keys=True) in written
    assert "What the rule said about its chart:" in written
    assert f"{ARC_SLICES} is the most that can" in written
    assert "Change the chart only" in written


def test_a_schema_is_only_sent_where_the_endpoint_honours_one(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")
    sent = []

    class Recording(ScriptedModel):
        def complete(self, written, schema=None):
            sent.append(schema)
            return super().complete(written, schema)

    critique(asked, tables, Recording(json.dumps(SCANS_PER_LOCATION)))
    critique(asked, tables, Recording(json.dumps(SCANS_PER_LOCATION)), constrained=True)

    assert sent[0] is None
    assert sent[1]["$defs"]["chart"]["properties"]["mark"]["enum"]
    unconstrained = prompt(asked, reads(asked, tables), findings(asked, tables))
    constrained = prompt(asked, reads(asked, tables), findings(asked, tables), constrained=True)
    assert "$defs" in unconstrained and "$defs" not in constrained


def test_nothing_here_takes_anything_a_result_set_could_arrive_in():
    """The rule the whole design rests on, enforced by the signature rather than by a
    comment. A critique reads profiles and a spec, the same way a question does."""
    for function in (critique, prompt, findings):
        signature = inspect.signature(function)
        hints = typing.get_type_hints(function)

        assert "rows" not in signature.parameters and "catalog" not in signature.parameters
        assert hints["tables"] == Sequence[TableProfile]
        assert not any(
            parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
            for parameter in signature.parameters.values()
        )


def test_a_critique_is_serialised_as_findings_and_a_spec(tables):
    asked = marked(SCANS_PER_LOCATION, "arc")

    body = critique(asked, tables, ScriptedModel(json.dumps(SCANS_PER_LOCATION))).as_dict()

    assert body["spec"] == SCANS_PER_LOCATION
    assert body["errors"] == []
    assert len(body["findings"]) == 1 and isinstance(body["findings"][0], str)
    assert json.dumps(body), "and it survives a JSON round trip"
