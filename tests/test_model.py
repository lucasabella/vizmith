import datetime as dt
import json
from email.utils import format_datetime

import httpx
import pytest

from vizmith.model import Completion, Endpoint, Model, ModelError

KEY = "not-a-real-key-and-must-never-be-read-back"
ENDPOINT = Endpoint(base_url="https://endpoint.invalid/v1", model="a-model", api_key=KEY, timeout=7.0)

ANSWER = {
    "model": "a-model",
    "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
}

# Any schema does here. The adapter never reads one, it forwards the caller's.
SIMPLE = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def model(handler, waits=None) -> Model:
    """The adapter over a transport that answers without a network, and over a clock that
    does not tick. The backoff is asserted by what it was asked to wait rather than by
    waiting it, so a suite that covers three attempts still runs in milliseconds."""
    slept = waits if waits is not None else []
    return Model(
        ENDPOINT,
        httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=slept.append,
    )


def answering(payload, status=200, capture=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        return httpx.Response(status, json=payload)

    return handler


def raising(error):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def refusing(status, headers=None, then=None):
    """An endpoint that refuses with `status` until `then` answers instead, which is what a
    rate limit that clears looks like from here. `then` of None never clears."""
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if then is not None and len(sent) > then:
            return httpx.Response(200, json=ANSWER)
        return httpx.Response(status, json={"error": "slow down"}, headers=headers or {})

    handler.sent = sent  # type: ignore[attr-defined]
    return handler


def test_a_completion_carries_the_text_and_enough_to_debug_it():
    completion = model(answering(ANSWER)).complete("a question")

    assert completion == Completion(
        text="hello", model="a-model", finish_reason="stop", usage=ANSWER["usage"]
    )


def test_a_request_names_the_model_and_carries_the_prompt():
    sent = []
    model(answering(ANSWER, capture=sent)).complete("a question")

    body = json.loads(sent[0].content)
    assert sent[0].url == httpx.URL("https://endpoint.invalid/v1/chat/completions")
    assert body["model"] == "a-model"
    assert body["messages"] == [{"role": "user", "content": "a question"}]
    assert "response_format" not in body, "a prompt without a schema asks for no format"


def test_a_schema_is_sent_as_a_strict_json_schema_response_format():
    sent = []
    schema = {"type": "object", "properties": {"spec_version": {"type": "string"}}}
    model(answering(ANSWER, capture=sent)).complete("a question", schema)

    response_format = json.loads(sent[0].content)["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == schema
    assert response_format["json_schema"]["strict"] is True


def test_the_key_travels_in_a_header_and_nowhere_else():
    sent = []
    model(answering(ANSWER, capture=sent)).complete("a question")

    assert sent[0].headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in sent[0].content.decode()


@pytest.mark.parametrize(
    "handler",
    [
        answering({"error": f"your key {KEY} is not valid"}, status=401),
        answering({"echo": {"headers": {"authorization": f"Bearer {KEY}"}}}, status=400),
    ],
    ids=["in the message", "echoed back"],
)
def test_a_key_an_endpoint_repeats_is_redacted_before_it_becomes_an_error(handler):
    """Several endpoints echo the request they refused. Without this the key reaches a
    stack trace, a log line and whatever reads either."""
    with pytest.raises(ModelError) as failure:
        model(handler).complete("a question")

    assert KEY not in str(failure.value)
    assert KEY not in repr(failure.value)
    assert "***" in str(failure.value)


def test_a_timeout_is_a_model_error_naming_the_limit():
    with pytest.raises(ModelError, match="did not answer within 7.0s"):
        model(raising(httpx.ConnectTimeout("timed out"))).complete("a question")


def test_a_connection_failure_is_a_model_error_rather_than_a_client_exception():
    with pytest.raises(ModelError, match="could not reach"):
        model(raising(httpx.ConnectError("refused"))).complete("a question")


def test_a_rate_limit_that_clears_is_answered_rather_than_ending_the_question():
    """The bug this closes. A 429 is the normal condition on a hosted endpoint, and it used
    to end the question on the spot: the person got "What the model said" with a rate limit
    message in it, for a request that would have been answered a second later."""
    endpoint = refusing(429, then=2)
    waits = []

    completion = model(endpoint, waits).complete("a question")

    assert completion.text == "hello"
    assert len(endpoint.sent) == 3, "the request was not sent again"
    assert waits == [1.0, 2.0], "the waits do not double"


def test_a_server_that_broke_is_sent_the_same_request_again():
    endpoint = refusing(503, then=1)

    assert model(endpoint).complete("a question").text == "hello"
    assert len(endpoint.sent) == 2


def test_a_rejected_request_is_not_sent_again():
    """A 400 is about the request, and the request does not change between attempts. Sending
    it again is a second bill for the same answer."""
    endpoint = refusing(400)
    waits = []

    with pytest.raises(ModelError, match="answered 400"):
        model(endpoint, waits).complete("a question")

    assert len(endpoint.sent) == 1
    assert waits == []


def test_a_dropped_connection_is_sent_again():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(200, json=ANSWER)
        raise httpx.ReadError("dropped")

    assert model(handler).complete("a question").text == "hello"
    assert len(calls) == 2


def test_a_timeout_is_not_sent_again():
    """The timeout is the caller's own number and it has already been waited in full. Three
    attempts at a sixty second setting is a three minute question, which is a worse answer
    than saying the endpoint did not answer."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(ModelError, match="did not answer within"):
        model(handler).complete("a question")

    assert len(calls) == 1


def test_the_attempts_are_bounded_and_the_last_failure_says_how_many():
    """One 429 and three of them are different things to be told, and the message is what
    reaches the interface under "What the model said"."""
    endpoint = refusing(429)

    with pytest.raises(ModelError, match=r"answered 429.*sent 3 times") as failure:
        model(endpoint).complete("a question")

    assert len(endpoint.sent) == 3
    assert failure.value.status == 429, "the status a caller reads survives the retries"


def test_an_endpoint_that_says_how_long_to_wait_is_obeyed_rather_than_doubled_against():
    """It is the one that knows when its limit resets. Doubling against it either asks too
    early, which is another 429, or too late, which is a person watching for no reason."""
    waits = []
    model(refusing(429, headers={"Retry-After": "4"}, then=1), waits).complete("a question")

    assert waits == [4.0]


def test_a_retry_after_given_as_a_date_is_read_as_the_seconds_until_it():
    """Both spellings are sent in the wild, and the date one is what a proxy tends to
    write."""
    when = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)
    waits = []
    model(refusing(429, headers={"Retry-After": format_datetime(when)}, then=1), waits).complete("q")

    assert waits and 3.0 <= waits[0] <= 5.0


def test_a_retry_after_nobody_can_parse_is_read_as_nothing_said():
    """A header that is neither a count nor a date is not a reason to lose the answer about
    the rate limit, so the backoff is the one this file would have chosen anyway."""
    waits = []
    model(refusing(429, headers={"Retry-After": "soon"}, then=1), waits).complete("a question")

    assert waits == [1.0]


def test_a_wait_longer_than_the_budget_ends_the_question_rather_than_being_slept_through():
    """Somebody is watching. An endpoint asking for a minute has said the question cannot be
    answered while they wait, whatever it says after that."""
    endpoint = refusing(429, headers={"Retry-After": "600"})
    waits = []

    with pytest.raises(ModelError, match="answered 429"):
        model(endpoint, waits).complete("a question")

    assert len(endpoint.sent) == 1
    assert waits == []


def test_an_answer_without_a_completion_is_a_model_error():
    with pytest.raises(ModelError, match="without a completion"):
        model(answering({"choices": []})).complete("a question")


def test_a_refused_schema_reports_an_endpoint_that_cannot_constrain_output():
    """Ollama's OpenAI-compatible route takes no json_schema response format. Asking is
    the only way to find that out, so a refusal is the answer rather than a failure."""
    assert model(answering({"error": "unknown parameter"}, status=400)).constrains_output(SIMPLE) is False


def test_an_endpoint_that_answers_in_the_schema_reports_that_it_can():
    honoured = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]}

    assert model(answering(honoured)).constrains_output(SIMPLE) is True


def test_an_endpoint_that_ignores_the_schema_is_not_taken_at_its_status():
    """A server that drops a response format it does not recognise still answers 200. The
    answer is what says whether the schema was honoured, and prose says it was not."""
    ignored = {"choices": [{"message": {"content": "Sure, ok is true!"}, "finish_reason": "stop"}]}

    assert model(answering(ignored)).constrains_output(SIMPLE) is False


def test_a_probe_that_never_got_an_answer_raises_rather_than_reporting_no():
    """A timeout says nothing about the endpoint's capabilities, and answering False would
    send the caller down the unconstrained path for the wrong reason."""
    with pytest.raises(ModelError):
        model(raising(httpx.ConnectError("refused"))).constrains_output(SIMPLE)

    with pytest.raises(ModelError):
        model(answering({"error": "overloaded"}, status=503)).constrains_output(SIMPLE)


def test_a_rate_limit_is_not_evidence_that_an_endpoint_cannot_constrain_output():
    """The distinction this method exists for, taken to the status that most looks like a
    refusal and is not one. Answering False here is the expensive mistake: the caller
    remembers "cannot constrain", stops sending the schema, and pays the fallback loop for
    the rest of the process over a limit that cleared in a second."""
    endpoint = refusing(429)

    with pytest.raises(ModelError, match="answered 429"):
        model(endpoint).constrains_output(SIMPLE)

    assert len(endpoint.sent) == 3, "the probe gave up without sending it again"


def test_nothing_reaches_a_service_without_configuration():
    """There is no default endpoint, model or key anywhere in the dataclass."""
    with pytest.raises(TypeError):
        Endpoint()  # type: ignore[call-arg]


def test_the_probe_asks_about_the_schema_and_nothing_else():
    """A parameter the endpoint happens to refuse comes back as a 400, which reads from
    here exactly like "this endpoint cannot constrain output". Current OpenAI models
    refuse `max_tokens` by name, and that is how a working endpoint reported False."""
    sent = []
    answered = answering({"choices": [{"message": {"content": '{"ok": true}'}}]}, capture=sent)
    model(answered).constrains_output(SIMPLE)

    body = json.loads(sent[0].content)
    assert set(body) == {"model", "messages", "response_format"}


def test_the_probe_carries_the_schema_it_was_given():
    """An endpoint takes a schema or refuses one, and OpenAI does both depending on which
    schema it is. A probe with a simpler schema than the caller's answers about a request
    that will never be sent."""
    sent = []
    answered = answering({"choices": [{"message": {"content": "{}"}}]}, capture=sent)
    model(answered).constrains_output(SIMPLE)

    body = json.loads(sent[0].content)
    assert body["response_format"]["json_schema"]["schema"] == SIMPLE


def test_the_endpoint_does_not_say_its_key_when_something_prints_it():
    """The default dataclass repr says every field, and this is the field the rest of this
    module spends its effort keeping quiet. Nothing prints an Endpoint today; a debugging
    print, or a framework that renders locals when an exception passes through, is one
    line away from undoing `_redacted` and `described` at once."""
    assert KEY not in repr(ENDPOINT)
    assert KEY not in str(ENDPOINT)


def test_the_endpoint_still_says_the_things_that_help():
    """Quiet about the key, not about everything. What is left is what identifies which
    endpoint a failure came from."""
    assert "endpoint.invalid" in repr(ENDPOINT)
    assert "a-model" in repr(ENDPOINT)
