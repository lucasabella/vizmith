import argparse
import dataclasses
import json
from pathlib import Path

import pytest
from generate_data import COLUMNS

from vizmith import evals
from vizmith.evals import MARK, REFERENCES, RESULT, VALIDATES, Cache, Question, Run, Score
from vizmith.model import Completion, ModelError
from vizmith.profiler import profile_table

QUESTIONS = Path(__file__).parent / "fixtures" / "evals" / "questions.json"
SPECS = Path(__file__).parent / "fixtures" / "specs"


class StubModel:
    """A model that answers from a dictionary keyed on the question the prompt carries.

    It is the whole reason the harness is testable for free: every layer below the answer
    is deterministic, so the only thing a live endpoint would add to these tests is a bill.
    """

    def __init__(self, answers: dict[str, object], described=("stub", "http://stub")):
        self.answers = answers
        self.described = described
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema: dict | None = None) -> Completion:
        self.prompts.append(prompt)
        asked = next((question for question in self.answers if question in prompt), None)
        text = json.dumps(self.answers[asked]) if asked is not None else "{}"
        return Completion(text=text, model="stub", finish_reason="stop", usage={})

    def constrains_output(self, schema: dict) -> bool:
        return False


def spec(name: str) -> dict:
    return json.loads((SPECS / "valid" / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def asked():
    return evals.questions(QUESTIONS)


@pytest.fixture(scope="module")
def tables(fixture_db):
    from conftest import FixtureCatalog

    catalog = FixtureCatalog(fixture_db)
    return [profile_table(catalog, name) for name in catalog.tables()]


@pytest.fixture
def correct(asked):
    """A model that answers every question with the spec the question set calls correct."""
    return StubModel({question.question: question.reference for question in asked})


def test_the_question_set_asks_only_about_the_fixture_dataset(asked):
    """Every table named in it is one the generator writes. A question about anything else
    would need data nobody can regenerate, and its score would mean nothing offline."""
    assert asked

    for question in asked:
        assert question.tables, question.name
        for table in question.tables:
            assert table in COLUMNS, question.name
        for column in question.columns:
            table, _, name = column.partition(".")
            assert name in [column for column, _ in COLUMNS[table]], question.name


def test_every_reference_is_a_spec_that_answers_its_own_question(asked, catalog):
    """The expected result set is derived by running the reference, so a reference that
    does not run makes the question unscoreable rather than hard."""
    from vizmith import query
    from vizmith.spec import validate_spec

    for question in asked:
        assert validate_spec(question.reference) == [], question.name
        assert query.execute(question.reference, catalog), question.name


def test_a_run_against_a_stub_scores_every_question_and_reaches_no_endpoint(asked, tables, correct, catalog):
    record = evals.run(asked, tables, correct, catalog)

    assert [score.name for score in record.scores] == [question.name for question in asked]
    assert all(score.complete for score in record.scores), [
        (score.name, score.failed, score.reason) for score in record.scores if not score.complete
    ]
    assert record.totals["complete"] == record.totals["questions"] == len(asked)
    assert record.totals[VALIDATES] == record.totals[MARK] == len(asked)


def test_the_record_says_which_model_and_endpoint_produced_it(asked, tables, correct, catalog):
    """A score without them is not comparable to anything, which is the only thing this
    harness is for."""
    record = evals.run(asked[:1], tables, correct, catalog)

    assert (record.model, record.endpoint) == ("stub", "http://stub")
    assert record.as_dict()["model"] == "stub"
    assert record.at.endswith("Z")


def test_a_named_subset_runs_and_nothing_else_does(asked, tables, correct, catalog):
    record = evals.run(asked, tables, correct, catalog, only=["total_revenue", "orders_per_month"])

    assert sorted(score.name for score in record.scores) == ["orders_per_month", "total_revenue"]
    assert len(correct.prompts) == 2


def test_a_name_the_set_does_not_hold_is_refused(asked, tables, correct, catalog):
    """A typo that quietly ran nothing would report a perfect run over no questions."""
    with pytest.raises(ValueError, match="no question named revene_by_country"):
        evals.run(asked, tables, correct, catalog, only=["revene_by_country"])


def test_an_answer_that_never_validates_fails_at_the_first_layer(asked, tables, catalog):
    model = StubModel({})
    before = len(catalog.statements)

    record = evals.run(asked[:1], tables, model, catalog)
    score = record.scores[0]

    assert (score.passed, score.failed) == ((), VALIDATES)
    assert "is a required property" in score.reason
    assert catalog.statements[before:] == [], "a spec that does not validate costs no query"


def test_a_model_that_cannot_be_reached_is_a_failed_question_rather_than_a_failed_run(
    asked, tables, catalog
):
    class Unreachable(StubModel):
        def complete(self, prompt, schema=None):
            raise ModelError("could not reach http://stub/chat/completions")

    record = evals.run(asked[:2], tables, Unreachable({}), catalog)

    assert [score.failed for score in record.scores] == [VALIDATES, VALIDATES]
    assert "could not reach" in record.scores[0].reason


def test_an_answer_about_the_wrong_tables_fails_before_anything_is_run(asked, tables, catalog):
    """Layer 2 is cheap and layer 3 is two queries, so the order is what keeps a run that
    fails early from paying for the rest of it."""
    wrong = next(question for question in asked if question.name == "revenue_by_country")
    model = StubModel({wrong.question: spec("returns_share_by_reason")})
    before = len(catalog.statements)

    score = evals.run([wrong], tables, model, catalog).scores[0]

    assert (score.passed, score.failed) == ((VALIDATES,), REFERENCES)
    assert "does not reference customers" in score.reason
    assert catalog.statements[before:] == []


def test_a_spec_that_reads_more_than_it_was_asked_to_is_recorded_rather_than_failed(
    asked, tables, catalog
):
    """A join the question did not ask for either changes the rows, which layer 3 catches,
    or does not, and calling the second one a failure would score a preference."""
    question = next(question for question in asked if question.name == "orders_per_month")
    answer = spec("orders_per_month")
    answer["query"]["joins"] = [
        {"table": "customers", "on": [{"left": "orders.customer_id", "right": "customers.id"}]}
    ]
    answer["query"]["group_by"] = [{"column": "orders.order_date", "truncate": "month", "as": "month"}]
    answer["query"]["filters"] = [{"column": "orders.order_date", "op": "is_not_null"}]

    score = evals.run([question], tables, StubModel({question.question: answer}), catalog).scores[0]

    assert REFERENCES in score.passed
    assert score.note == "also reads customers"


def test_an_answer_returning_the_wrong_rows_fails_at_the_result_layer(asked, tables, catalog):
    """The shipments question says unmatched carriers are their own group, which is the
    left join. An inner join names the same tables and columns, so nothing but the rows
    can tell the two apart."""
    question = next(question for question in asked if question.name == "shipments_by_carrier")
    answer = spec("shipments_by_carrier")
    answer["query"]["joins"][0]["type"] = "inner"

    score = evals.run([question], tables, StubModel({question.question: answer}), catalog).scores[0]

    assert (score.passed, score.failed) == ((VALIDATES, REFERENCES), RESULT)
    assert "expected" in score.reason


def test_an_answer_naming_its_columns_differently_still_matches(asked, tables, catalog):
    """An alias is the model's to choose. Scoring it would score vocabulary."""
    question = next(question for question in asked if question.name == "returns_by_reason")
    answer = spec("returns_share_by_reason")
    answer["query"]["aggregates"][0]["as"] = "how_many"
    answer["query"]["order_by"] = [{"column": "how_many", "direction": "desc"}]
    answer["chart"]["encoding"]["y"]["field"] = "how_many"

    score = evals.run([question], tables, StubModel({question.question: answer}), catalog).scores[0]

    assert score.complete, score.reason


def test_an_answer_that_qualifies_every_name_scores_the_same(asked, tables, catalog):
    """A model reading a profile writes the three segment name, and the question set is
    written in the names a person says. Neither is the answer; the last segment is what
    the two share."""
    question = next(question for question in asked if question.name == "returns_by_reason")
    answer = spec("returns_share_by_reason")
    answer["query"]["from"] = "vizmith.shop.returns"
    answer["query"]["group_by"] = [{"column": "vizmith.shop.returns.reason"}]

    score = evals.run([question], tables, StubModel({question.question: answer}), catalog).scores[0]

    assert score.complete, score.reason


def test_a_mark_the_shape_does_not_support_fails_the_last_layer(asked, tables, catalog):
    """Five hundred locations drawn as an arc is a chart that is right about the data and
    unreadable, which is the one thing the last layer is for."""
    question = next(question for question in asked if question.name == "scans_per_location")
    answer = spec("scans_per_location")
    answer["chart"]["mark"] = "arc"

    score = evals.run([question], tables, StubModel({question.question: answer}), catalog).scores[0]

    assert (score.passed, score.failed) == ((VALIDATES, REFERENCES, RESULT), MARK)
    assert "10 slices" in score.reason


def test_a_cached_question_is_not_asked_a_second_time(tmp_path, asked, tables, correct, catalog):
    cache = Cache(tmp_path / "answers.json")
    subset = ["total_revenue"]

    first = evals.run(asked, tables, correct, catalog, cache=cache, only=subset)
    again = evals.run(asked, tables, correct, catalog, cache=Cache(tmp_path / "answers.json"), only=subset)

    assert len(correct.prompts) == 1, "the second run answered from the file"
    assert [score.complete for score in again.scores] == [score.complete for score in first.scores]
    assert first.scores[0].asked and not again.scores[0].asked
    assert again.totals["asked"] == 0


def test_a_changed_prompt_is_not_answered_out_of_the_cache(tmp_path, asked, tables, correct, catalog):
    """The prompt is what this harness measures, so a cache that survived a change to it
    would report the new prompt's score without ever having asked it."""
    cache = Cache(tmp_path / "answers.json")
    subset = ["total_revenue"]

    evals.run(asked, tables, correct, catalog, cache=cache, only=subset)
    evals.run(asked, tables[:1], correct, catalog, cache=cache, only=subset)

    assert len(correct.prompts) == 2
    assert correct.prompts[0] != correct.prompts[1]


def test_a_cached_answer_is_not_shared_between_two_endpoints(tmp_path, asked, tables, catalog):
    cache = Cache(tmp_path / "answers.json")
    answers = {question.question: question.reference for question in asked}
    here = StubModel(answers, described=("stub", "http://one"))
    there = StubModel(answers, described=("stub", "http://two"))

    evals.run(asked, tables, here, catalog, cache=cache, only=["total_revenue"])
    evals.run(asked, tables, there, catalog, cache=cache, only=["total_revenue"])

    assert (len(here.prompts), len(there.prompts)) == (1, 1)


def test_two_runs_of_the_same_set_differ_only_in_when_they_ran(tmp_path, asked, tables, correct, catalog):
    """The point of writing a run to a file is the diff against the last one, so anything
    that moves on its own is one line rather than one line per question."""
    record = evals.run(asked, tables, correct, catalog)

    first = evals.write(dataclasses.replace(record, at="2026-08-01T09:00:00Z"), tmp_path)
    second = evals.write(dataclasses.replace(record, at="2026-08-02T09:00:00Z"), tmp_path)

    differing = [
        (before, after)
        for before, after in zip(first.read_text().splitlines(), second.read_text().splitlines())
        if before != after
    ]
    assert len(differing) == 1
    assert '"at"' in differing[0][0]
    assert first != second, "one run does not overwrite the one before it"


def test_a_written_run_holds_every_score_and_the_totals(tmp_path, asked, tables, correct, catalog):
    record = evals.run(asked, tables, correct, catalog)

    written = json.loads(evals.write(record, tmp_path / "runs").read_text())

    assert [score["name"] for score in written["scores"]] == sorted(q.name for q in asked)
    assert written["totals"]["complete"] == len(asked)
    assert written["model"] and written["endpoint"]


def test_a_run_record_carries_no_key_and_no_row(tmp_path, asked, tables, correct, catalog):
    """The record says what produced it, which is the model and where it was. A key is not
    part of that, and neither is a value out of the data."""
    written = evals.write(evals.run(asked, tables, correct, catalog), tmp_path).read_text()

    assert "api_key" not in written and "Bearer" not in written
    assert "Netherlands" not in written, "a score is a score, not a result set"


def test_the_totals_count_questions_rather_than_averaging_layers(asked, tables, catalog):
    question = next(question for question in asked if question.name == "scans_per_location")
    answer = spec("scans_per_location")
    answer["chart"]["mark"] = "arc"
    model = StubModel({question.question: answer, asked[0].question: asked[0].reference})

    record = evals.run([question, asked[0]], tables, model, catalog)

    assert record.totals["questions"] == 2
    assert record.totals[RESULT] == 2
    assert record.totals[MARK] == 1
    assert record.totals["complete"] == 1


def test_the_command_scores_the_set_and_says_where_it_wrote_the_run(
    tmp_path, monkeypatch, capsys, asked, tables, correct, catalog
):
    """The command takes the source, the model and the probe from `api`, which is where a
    question asked from the interface gets them, so a score is produced by the path a
    person's question takes rather than by a second one that can drift from it."""
    from vizmith import api, cli

    monkeypatch.setattr(api, "source", lambda: catalog)
    monkeypatch.setattr(api, "model", lambda: correct)
    monkeypatch.setattr(api, "profiles", lambda source: tables)
    monkeypatch.setattr(api, "constrains", lambda writer: False)

    code = cli._eval(
        argparse.Namespace(
            questions=QUESTIONS, only=["total_revenue", "orders_per_month"], out=tmp_path, no_cache=True
        )
    )

    printed = capsys.readouterr().out
    assert code == 0
    assert "2/2 complete" in printed
    assert "4/4  total_revenue" in printed
    written = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert [score["name"] for score in written["scores"]] == ["orders_per_month", "total_revenue"]


def test_the_command_refuses_a_name_the_set_does_not_hold(tmp_path, monkeypatch, capsys, correct, catalog):
    from vizmith import api, cli

    monkeypatch.setattr(api, "source", lambda: catalog)
    monkeypatch.setattr(api, "model", lambda: correct)
    monkeypatch.setattr(api, "profiles", lambda source: [])
    monkeypatch.setattr(api, "constrains", lambda writer: False)

    code = cli._eval(
        argparse.Namespace(questions=QUESTIONS, only=["revene_by_country"], out=tmp_path, no_cache=True)
    )

    assert code == 2
    assert "no question named" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.json")), "a run that scored nothing writes nothing"


@pytest.mark.parametrize(
    "mark, kind, rows, refused",
    [
        ("bar", "nominal", 10, False),
        ("point", "nominal", 10, False),
        ("line", "nominal", 10, True),
        ("area", "ordinal", 10, True),
        ("line", "temporal", 30, False),
        ("area", "temporal", 30, False),
        ("bar", "temporal", 30, False),
        ("arc", "temporal", 5, True),
        ("point", "quantitative", 100, False),
        ("bar", "quantitative", 100, True),
        ("arc", "nominal", 5, False),
        ("arc", "nominal", 9, True),
    ],
)
def test_a_mark_is_judged_against_the_shape_of_the_result(mark, kind, rows, refused):
    chart = {
        "mark": mark,
        "encoding": {"x": {"field": "a", "type": kind}, "y": {"field": "b", "type": "quantitative"}},
    }

    assert bool(evals.indefensible({"chart": chart}, [{}] * rows)) is refused


def test_a_figure_has_no_mark_to_defend():
    """`mark` says nothing on a spec with no x, the same way `stack` says nothing on an
    arc, so there is nothing there for this layer to refuse."""
    for mark in ("bar", "line", "area", "point", "arc"):
        chart = {"mark": mark, "encoding": {"y": {"field": "revenue", "type": "quantitative"}}}
        assert evals.indefensible({"chart": chart}, [{"revenue": 1}]) == ""


def test_an_arc_cannot_carry_a_second_series():
    chart = {
        "mark": "arc",
        "encoding": {
            "x": {"field": "country", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "category", "type": "nominal"},
        },
    }

    assert "colour channel" in evals.indefensible({"chart": chart}, [{}] * 4)


def test_a_score_names_the_layer_that_stopped_it():
    """A number out of four says nothing a prompt can be changed on."""
    score = Score("q", (VALIDATES,), REFERENCES, "does not reference customers")

    assert not score.complete
    assert score.as_dict()["reason"] == "does not reference customers"
    assert Score("q", tuple(evals.LAYERS), None).complete


def test_a_run_with_no_scores_totals_nothing():
    assert Run(at="2026-08-01T09:00:00Z", model="m", endpoint="e").totals["questions"] == 0


def test_a_question_set_loads_its_references_from_beside_it(asked):
    question = next(question for question in asked if question.name == "total_revenue")

    assert isinstance(question, Question)
    assert question.reference["query"]["from"] == "orders"
    assert question.notes
