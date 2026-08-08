import inspect
import json
import typing
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from vizmith.ask import ATTEMPTS, Answer, ask, prompt
from vizmith.model import Completion, Endpoint, Model
from vizmith.profiler import TableProfile, profile_table

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = json.loads((FIXTURES / "valid" / "revenue_by_country.json").read_text())
REJECTED = json.loads((FIXTURES / "invalid" / "missing_limit.json").read_text())


class ScriptedModel:
    """A model that answers from a list instead of from a network, and keeps every prompt
    it was given so a test can read what the loop said the second time."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema: dict | None = None) -> Completion:
        self.prompts.append(prompt)
        text = self.answers.pop(0) if self.answers else "{}"
        return Completion(text=text, model="scripted", finish_reason="stop", usage={})

    def constrains_output(self, schema: dict) -> bool:
        """There is no endpoint here to honour a schema. Callers that probe before they
        ask, which the API does, get the answer that costs them nothing."""
        return False


def limited(*script: str | int) -> tuple[Model, list]:
    """A real adapter over a transport that answers each entry of the script in turn: an
    integer is a status the endpoint refuses with, a string is a completion. The requests
    it was actually sent come back beside it, because the point of this is the difference
    between what was sent and what the caller was handed."""
    sent: list[httpx.Request] = []
    answers = list(script)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        said = answers.pop(0) if answers else "{}"
        if isinstance(said, int):
            return httpx.Response(said, json={"error": "slow down"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": said}, "finish_reason": "stop"}]}
        )

    endpoint = Endpoint(base_url="https://endpoint.invalid/v1", model="a-model", api_key="k")
    return Model(endpoint, httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None), sent


@pytest.fixture
def tables(catalog):
    return [profile_table(catalog, "vizmith.shop.orders")]


def test_a_valid_answer_is_returned_on_the_first_attempt(tables):
    model = ScriptedModel(json.dumps(VALID))

    answer = ask("revenue by country", tables, model)

    assert answer == Answer(spec=VALID, errors=[], attempts=1)
    assert len(model.prompts) == 1


def test_a_rejected_answer_is_asked_again_with_the_validator_s_words(tables):
    model = ScriptedModel(json.dumps(REJECTED), json.dumps(VALID))

    answer = ask("revenue by country", tables, model)

    assert answer.spec == VALID
    assert answer.attempts == 2
    assert "'limit' is a required property" in model.prompts[1]
    assert "'limit' is a required property" not in model.prompts[0]


def test_the_attempts_are_bounded_and_the_last_errors_come_back(tables):
    model = ScriptedModel(*[json.dumps(REJECTED)] * 10)

    answer = ask("revenue by country", tables, model, attempts=3)

    assert answer.spec is None
    assert answer.attempts == 3
    assert len(model.prompts) == 3
    assert any("'limit' is a required property" in error for error in answer.errors)


def test_the_default_attempt_limit_is_the_documented_one(tables):
    model = ScriptedModel(*[json.dumps(REJECTED)] * 10)

    assert ask("revenue by country", tables, model).attempts == ATTEMPTS


def test_a_rate_limit_does_not_spend_an_attempt_this_loop_is_counting(tables):
    """The two loops are separate on purpose and this is what that separation buys. A
    transport retry is the same prompt sent again because the endpoint would not take it;
    an attempt here is a different prompt, because the validator rejected the answer. A
    shared budget would mean a 429 costing the model a chance to correct itself."""
    model, sent = limited(429, json.dumps(REJECTED), 429, 429, json.dumps(VALID))

    answer = ask("revenue by country", tables, model)

    assert answer.spec == VALID
    assert answer.attempts == 2, "a retried request was counted as an attempt"
    assert len(sent) == 5, "the request was not sent again for the rate limits"


def test_an_answer_that_is_not_json_is_a_failed_attempt_rather_than_a_crash(tables):
    model = ScriptedModel("Certainly! Here is your chart:", json.dumps(VALID))

    answer = ask("revenue by country", tables, model)

    assert answer.spec == VALID
    assert "was not JSON" in model.prompts[1]


def test_json_that_is_not_a_spec_is_a_failed_attempt(tables):
    model = ScriptedModel(json.dumps({"chart": "a bar chart of revenue"}), json.dumps(VALID))

    answer = ask("revenue by country", tables, model)

    assert answer.spec == VALID
    assert "is a required property" in model.prompts[1]


def test_an_answer_is_never_repaired_into_shape(tables):
    """The loop asks again. It does not fill in what the model left out, because a spec
    nobody specified would validate and answer a different question."""
    model = ScriptedModel(json.dumps(REJECTED))

    answer = ask("revenue by country", tables, model, attempts=1)

    assert answer.spec is None


def test_a_schema_is_only_sent_where_the_endpoint_honours_one(tables):
    sent = []

    class Recording(ScriptedModel):
        def complete(self, prompt, schema=None):
            sent.append(schema)
            return super().complete(prompt, schema)

    ask("revenue by country", tables, Recording(json.dumps(VALID)))
    ask("revenue by country", tables, Recording(json.dumps(VALID)), constrained=True)

    assert sent[0] is None
    assert sent[1]["$defs"]["chart"]["properties"]["mark"]["enum"]


def test_the_prompt_carries_the_profile_and_the_question(tables):
    written = prompt("revenue by country", tables)

    assert "revenue by country" in written
    assert "vizmith.shop.orders" in written
    assert "status string" in written
    assert "values: cancelled, delivered" in written, "a low cardinality column lists its values"
    assert "6000 rows" in written


def test_the_prompt_says_which_figures_are_estimates(tables):
    """A distinct count is usually approximate and the samples beside it are exact. A
    prompt that does not say so hands a guess over as a fact."""
    written = prompt("revenue by country", tables)

    assert "distinct, approximate" in written
    assert "values: cancelled, delivered" in written, "samples are exact and say nothing about it"


def test_the_prompt_builder_takes_nothing_a_result_set_could_arrive_in():
    """The rule is enforced by the signature rather than by a comment: there is no
    parameter a row could be passed through, and no catch-all to smuggle one into."""
    signature = inspect.signature(prompt)
    hints = typing.get_type_hints(prompt)

    assert list(signature.parameters) == ["question", "tables", "errors", "constrained", "withheld"]
    assert hints["question"] is str
    assert hints["tables"] == Sequence[TableProfile]
    assert hints["errors"] == Sequence[str]
    assert hints["constrained"] is bool
    assert hints["withheld"] is int
    assert not any(
        parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
        for parameter in signature.parameters.values()
    )


def test_nothing_in_this_module_reads_a_result_set():
    """`ask` receives a model and profiles. It has no catalog, so it cannot run a query,
    so there is no result set for it to leak."""
    hints = typing.get_type_hints(ask)

    assert "catalog" not in inspect.signature(ask).parameters
    assert hints["tables"] == Sequence[TableProfile]


def test_a_constrained_endpoint_is_not_sent_the_schema_twice(tables):
    """The endpoint enforcing the schema was also handed a copy of it in the prose, about
    1,350 tokens per attempt and up to six times per question, buying nothing."""
    constrained = prompt("revenue by country", tables, constrained=True)
    unconstrained = prompt("revenue by country", tables)

    assert "must validate against this JSON Schema" in unconstrained
    assert "$defs" in unconstrained
    assert "$defs" not in constrained
    assert len(constrained) < len(unconstrained)


def test_the_instructions_are_sent_whether_the_schema_is_attached_or_not(tables):
    """They say what a schema cannot, and they are why the retry loop converges."""
    for written in (prompt("q", tables), prompt("q", tables, constrained=True)):
        assert "group_by" in written
        assert "limit_by" in written
        assert "one figure" in written


def test_everything_before_the_question_is_the_same_for_two_questions(tables):
    """The prefix a provider's prompt cache can hit. Nothing else would fail if somebody
    moved the question above the tables, so this is what makes the order structural."""
    for constrained in (False, True):
        first = prompt("revenue by country", tables, constrained=constrained)
        second = prompt("orders per month", tables, constrained=constrained)
        shared = first[: len(_common(first, second))]

        assert shared.endswith("Question: ")
        assert "Tables:" in shared
        assert "vizmith.shop.orders" in shared


def test_the_errors_of_a_retry_come_after_the_question(tables):
    """So an attempt shares its prefix with the one before it as well."""
    first = prompt("revenue by country", tables)
    retried = prompt("revenue by country", tables, ["query: 'limit' is a required property"])

    assert retried.startswith(first)


def _common(left: str, right: str) -> str:
    for at, (one, two) in enumerate(zip(left, right, strict=False)):
        if one != two:
            return left[:at]
    return left[: min(len(left), len(right))]
