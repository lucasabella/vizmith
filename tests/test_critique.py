"""What a critique may say, and what it may not.

Every test here is offline. The rules are deterministic and read the spec, the profiles and
the shape of a result, and the model is scripted, so what is proven is the whole of the
critique except the sentence a real endpoint would write into a repair.
"""

import copy
import inspect
import json
import typing
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from test_ask import ScriptedModel

from vizmith.critique import (
    READABLE,
    SUGGESTIONS,
    Fault,
    critique,
    faults,
    improve,
    revision,
    widened,
)
from vizmith.profiler import TableProfile, profile_table

FIXTURES = Path(__file__).parent / "fixtures" / "specs" / "valid"
BY_COUNTRY = json.loads((FIXTURES / "revenue_by_country.json").read_text())


@pytest.fixture(scope="module")
def tables(fixture_db):
    from conftest import FixtureCatalog

    catalog = FixtureCatalog(fixture_db)
    return tuple(profile_table(catalog, name) for name in catalog.tables())


def rows(count: int) -> list[dict]:
    """A result set of the right size. Nothing here reads a value: every rule that counts
    rows counts them, and the ones that read a column read its profile."""
    return [{"country": f"c{index}", "revenue": index} for index in range(count)]


def altered(**query) -> dict:
    spec = copy.deepcopy(BY_COUNTRY)
    spec["query"].update(query)
    return spec


def test_a_chart_nothing_is_wrong_with_gets_nothing_back(tables):
    """Ten countries, ordered by revenue, drawn as bars. Every rule read it and none of
    them had anything to say, which is the answer a critique has to be able to give: a
    layer that always finds something is one nobody reads twice."""
    assert faults(BY_COUNTRY, rows(10), tables) == ()


def test_a_clean_chart_is_never_put_to_a_model(tables):
    """The model is asked for repairs, never for opinions. Where the rules find nothing
    there is nothing to repair, so nothing is asked and nothing is billed — and there is no
    path by which a suggestion this file cannot account for reaches a person."""
    model = ScriptedModel(json.dumps(BY_COUNTRY))

    found = critique(BY_COUNTRY, rows(10), tables, model)

    assert found.suggestions == ()
    assert model.prompts == []


def test_a_limit_that_cut_the_result_with_nothing_ordering_it(tables):
    """Ten of something, and which ten is the source's choice. The chart looks like a top
    ten and is not one, which is the class of fault this whole module exists for: legal,
    drawn, and wrong."""
    spec = altered(order_by=[], limit=10)
    del spec["query"]["order_by"]

    found = faults(spec, rows(10), tables)

    assert [fault.rule for fault in found] == ["arbitrary"]
    assert "the source's choice" in found[0].says


def test_a_limit_that_cut_nothing_is_not_a_fault(tables):
    """Nine rows out of a limit of ten is every row there was, and the order of them is the
    chart's business rather than the query's."""
    spec = altered(limit=10)
    del spec["query"]["order_by"]

    assert faults(spec, rows(9), tables) == ()


def test_a_mark_the_shape_of_the_result_contradicts(tables):
    """The rule the eval harness already scores on, read here as a fault. It refuses rather
    than ranks, which is the line this module keeps: several marks suit these rows and
    naming a favourite among them would ship somebody's taste."""
    spec = copy.deepcopy(BY_COUNTRY)
    spec["chart"]["mark"] = "line"

    found = faults(spec, rows(10), tables)

    assert [fault.rule for fault in found] == ["mark"]
    assert "gaps between its values" in found[0].says


def test_more_categories_than_an_axis_can_be_read_at(tables):
    spec = altered(limit=500)

    found = faults(spec, rows(READABLE + 1), tables)

    assert [fault.rule for fault in found] == ["crowded"]
    assert str(READABLE + 1) in found[0].says


def test_a_total_of_identifiers(tables):
    """Adding keys together produces a number with a value and no meaning, and nothing
    about the chart says so: the axis has figures on it and every one of them is nonsense."""
    spec = altered(aggregates=[{"fn": "sum", "column": "orders.customer_id", "as": "revenue"}])

    found = faults(spec, rows(10), tables)

    assert [fault.rule for fault in found] == ["identifiers"]
    assert "no meaning" in found[0].says


def test_counting_a_key_column_is_not_a_fault(tables):
    """Counting is what a key column can answer, so the rule is about `sum` and `avg`
    rather than about the column."""
    spec = altered(aggregates=[{"fn": "count_distinct", "column": "orders.customer_id", "as": "revenue"}])

    assert faults(spec, rows(10), tables) == ()


def test_a_dimension_most_of_the_rows_have_no_value_for(tables):
    """Those rows are still in the result, drawn as a category with no name on it, and a
    reader takes it for a real one."""
    thin = tuple(
        replace(
            table,
            columns=tuple(
                replace(column, null_rate=0.4) if column.name == "country" else column
                for column in table.columns
            ),
        )
        for table in tables
    )

    found = faults(BY_COUNTRY, rows(10), thin)

    assert [fault.rule for fault in found] == ["nulls"]
    assert "40% null" in found[0].says


def test_a_rule_that_cannot_tell_says_nothing(tables):
    """A column no profile here describes is one this cannot judge, and a suggestion made
    out of a guess is worse than no suggestion."""
    spec = altered(
        group_by=[{"column": "customers.country"}],
        aggregates=[{"fn": "sum", "column": "orders.total", "as": "revenue"}],
    )

    assert faults(spec, rows(10), ()) == ()


def test_the_repair_prompt_carries_the_spec_the_fault_and_the_tables_it_reads(tables):
    """And nothing else of the schema: a correction may not reach for another table, so
    sending the rest would pay for tokens whose only use would be to break that rule."""
    written = revision(BY_COUNTRY, Fault("mark", "a line draws a category axis"), tables)

    assert "a line draws a category axis" in written
    assert '"revenue_by_country"' not in written, "the fixture's file name is not the spec"
    assert "Revenue by country, 2025" in written, "the specification itself goes in"
    assert "vizmith.shop.orders" in written
    assert "vizmith.shop.customers" in written
    assert "vizmith.shop.shipments" not in written
    assert "vizmith.shop.carriers" not in written


def test_the_repair_prompt_takes_nothing_a_result_set_could_arrive_in():
    """The same rule `ask.prompt` keeps, enforced the same way: there is no parameter a row
    could be passed through and no catch-all to smuggle one into. What crosses is the
    fault's sentence, which this module wrote."""
    signature = inspect.signature(revision)
    hints = typing.get_type_hints(revision)

    assert list(signature.parameters) == ["spec", "fault", "tables", "errors", "constrained"]
    assert hints["tables"] == Sequence[TableProfile]
    assert hints["fault"] is Fault
    assert not any(
        parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
        for parameter in signature.parameters.values()
    )


def test_a_repair_is_judged_by_the_validator_like_every_other_answer(tables):
    corrected = copy.deepcopy(BY_COUNTRY)
    corrected["chart"]["mark"] = "bar"
    model = ScriptedModel(json.dumps(corrected))

    suggestion = improve(BY_COUNTRY, Fault("mark", "a line draws a category axis"), tables, model)

    assert suggestion.usable
    assert suggestion.spec == corrected
    assert suggestion.attempts == 1


def test_a_repair_that_never_validates_is_not_offered(tables):
    """A person is offered a spec that runs, or nothing. The errors stay on the suggestion
    so a run record can say the critique was asked and came back empty."""
    model = ScriptedModel(*[json.dumps({"chart": "a nicer one"})] * 5)

    suggestion = improve(BY_COUNTRY, Fault("mark", "a line"), tables, model, attempts=2)

    assert not suggestion.usable
    assert suggestion.attempts == 2
    assert any("is a required property" in error for error in suggestion.errors)


def test_a_suggestion_that_reads_another_table_is_refused_and_asked_again(tables):
    """"Improve this" and "answer a different question" are one prompt away from each
    other. A repair re-shapes what the spec already reads; a spec that joined another table
    is a new question wearing the old one's clothes, and it goes back with those words."""
    widened_spec = copy.deepcopy(BY_COUNTRY)
    widened_spec["query"]["joins"].append(
        {
            "table": "shipments",
            "type": "inner",
            "on": [{"left": "orders.id", "right": "shipments.order_id"}],
        }
    )
    corrected = copy.deepcopy(BY_COUNTRY)
    corrected["chart"]["mark"] = "bar"
    model = ScriptedModel(json.dumps(widened_spec), json.dumps(corrected))

    suggestion = improve(BY_COUNTRY, Fault("mark", "a line"), tables, model)

    assert suggestion.spec == corrected
    assert suggestion.attempts == 2
    assert "reads 'shipments'" in model.prompts[1]


def test_a_column_the_specification_does_not_name_is_refused_too():
    """The table is the obvious half. Grouping by something else out of a table the spec
    already reads is the same question changed, and it is the half a prompt would not
    catch."""
    other = copy.deepcopy(BY_COUNTRY)
    other["query"]["group_by"] = [{"column": "customers.signup_date"}]

    refused = widened(BY_COUNTRY, other)

    assert refused
    assert "customers.signup_date" in refused[0]


def test_a_critique_never_changes_the_spec_it_was_about(tables):
    """A spec that changed itself is the failure mode the whole design is built against.
    What comes back is a spec the person may run; the one they have is untouched."""
    spec = copy.deepcopy(BY_COUNTRY)
    spec["chart"]["mark"] = "line"
    kept = copy.deepcopy(spec)
    corrected = copy.deepcopy(BY_COUNTRY)
    model = ScriptedModel(json.dumps(corrected))

    found = critique(spec, rows(10), tables, model)

    assert found.usable
    assert spec == kept


def test_the_requests_a_critique_costs_are_bounded(tables):
    """Every fault is a billed request and a spec somebody has to read. What is dropped is
    the least serious, because the rules are ordered."""
    spec = altered(
        limit=500,
        aggregates=[{"fn": "sum", "column": "orders.customer_id", "as": "revenue"}],
    )
    del spec["query"]["order_by"]
    spec["chart"]["mark"] = "line"
    repaired = copy.deepcopy(spec)
    repaired["chart"]["mark"] = "bar"
    model = ScriptedModel(*[json.dumps(repaired)] * 10)

    found = critique(spec, rows(500), tables, model, limit=2)

    assert len(faults(spec, rows(500), tables)) > 2
    assert len(found.suggestions) == 2
    assert len(model.prompts) == 2
    assert len(found.unasked) == len(faults(spec, rows(500), tables)) - 2
    assert SUGGESTIONS >= 1
