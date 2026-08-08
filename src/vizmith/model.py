"""One adapter for every endpoint that speaks the OpenAI chat completions convention.

It sends a prompt and returns what came back. It writes no prompts and judges nothing,
because a spec is only valid once the validator says so and that belongs to the caller.

What it does retry is the request failing to be answered at all. That is a different thing
from the caller's loop and the two must not share a budget: `ask.py` retries a spec the
validator rejected, which is a *different prompt* each time, and this retries the same
request because a rate limit or a server's own trouble says nothing about what was asked.
Rate limiting is the normal condition on a hosted endpoint rather than an exceptional one,
and the question most likely to meet one is the question that already needed two attempts.

Schema constrained output is reported rather than assumed. An OpenAI-compatible base URL
says nothing about it: OpenAI, vLLM and LM Studio take a `json_schema` response format and
Ollama's OpenAI-compatible route does not, so an endpoint has to be asked rather than
trusted. `constrains_output` asks, with the schema the caller is about to send, and costs
one completion to do it. It asks with that schema rather than one of its own because
"takes a JSON Schema" is not a capability an endpoint has: OpenAI's structured output is a
subset of the language, and a schema outside the subset is refused by an endpoint that
accepts a simpler one. A probe with a schema nobody sends answers a question nobody asked.

The key is configuration and stays out of everything a person or a log can read. It is
sent in a header and never written into a message, and any text an endpoint hands back is
redacted before it becomes an error, because an endpoint that echoes a request would
otherwise put the key in a stack trace.
"""

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx

# The probe's question. Short, because what is being tested is the schema attached to it
# and not the model's reading.
PROBE_PROMPT = "Answer with the smallest object the schema allows."

# How many times one request is sent before the caller hears about it, what is waited
# between the attempts, and what all that waiting may add up to.
#
# The budget is per request, and the number is chosen for what a *question* costs, which is
# not the same thing: `ask` sends up to `ask.ATTEMPTS` requests for one question, and the
# first question of a process pays a probe in front of them. Six seconds a request is
# therefore up to twenty-four across a question that goes badly at every step, which is
# about the most a person will sit in front of a blank canvas before deciding it is broken.
# A budget spent on one request instead would have been that four times over.
#
# The waits themselves are 1s then 2s, which never approaches the budget on their own; what
# the budget is really bounding is a `Retry-After` an endpoint asks for. Backing off with no
# jitter is deliberate — one desktop process asking one endpoint is not a herd, and a
# deterministic wait is one a test can assert.
ATTEMPTS = 3
BACKOFF = 1.0
BACKOFF_BUDGET = 6.0

# The one status that is a rate limit rather than a rejected request. It is named because
# two decisions turn on it: it is worth sending again, and it is not evidence about whether
# an endpoint honours a schema.
TOO_MANY = 429


@dataclass(frozen=True)
class Endpoint:
    """Where the model is and how to reach it. Every field is the user's to supply, and
    there is no default that reaches a paid service."""

    base_url: str
    model: str
    # Out of the repr, because the default one says every field and this is the field the
    # rest of this module spends its effort keeping quiet. A print while debugging, or a
    # framework that renders locals when an exception passes through, would otherwise
    # undo `_redacted` and `described` in one line.
    api_key: str = field(repr=False)
    timeout: float = 60.0


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    finish_reason: str | None
    usage: dict


class ModelError(Exception):
    """Everything that can go wrong reaching a model, in one type, so a caller handles a
    timeout, a refused connection and a rejected request the same way rather than
    catching whichever exception the HTTP client happens to raise. `status` is the HTTP
    status where there was one, and None where the request never got an answer.

    `again` says whether sending the same request could plausibly do better: a rate limit,
    a server that broke, a connection that dropped. It is decided where the error is raised
    rather than read back off the status later, because the status does not carry it — two
    of those three cases have no status at all, and so does an answer that came back
    malformed, which is a failure the same request would reproduce. `after` is what the
    endpoint asked to be waited, in seconds, where it said.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        again: bool = False,
        after: float | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.again = again
        self.after = after


class Model:
    def __init__(self, endpoint: Endpoint, client: httpx.Client | None = None, sleep=time.sleep):
        self._endpoint = endpoint
        self._client = client or httpx.Client(timeout=endpoint.timeout)
        # Injected so a test can assert what was waited without waiting it. Nothing else
        # passes it.
        self._sleep = sleep

    @property
    def described(self) -> tuple[str, str]:
        """Which model at which endpoint, for a record that has to say what produced it.

        A score is not comparable to another one without both. The key is not in it and
        there is no accessor that returns it, so a caller writing this into a file cannot
        write the key with it.
        """
        return self._endpoint.model, self._endpoint.base_url

    def complete(self, prompt: str, schema: dict | None = None) -> Completion:
        body: dict = {
            "model": self._endpoint.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        return self._parse(self._post(body))

    def constrains_output(self, schema: dict) -> bool:
        """Whether this endpoint honours this schema. What comes back decides it, not the
        status: a server that drops a response format it does not know answers 200 with
        ordinary prose, and calling that a yes is the expensive mistake, since a caller
        who believes it stops checking. A rejected request is an answer, so it returns
        False. A timeout, an unreachable host or a rate limit is not, so it raises."""
        body = {
            "model": self._endpoint.model,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            # The schema and nothing else. A token cap would make this cheaper and is not
            # worth it: current OpenAI models refuse `max_tokens` and name a different
            # parameter, and that refusal is a 400 that reads exactly like "no schema
            # support" from here.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "probe", "schema": schema, "strict": True},
            },
        }
        try:
            completion = self._parse(self._post(body))
        except ModelError as error:
            # A refusal is an answer. A timeout, an unreachable host, a server that broke
            # or a rate limit is not, and reporting those as "cannot constrain" would send
            # the caller down the unconstrained path for a reason that has nothing to do
            # with the endpoint's capabilities. `again` is the same judgement the retry
            # loop makes and drew from the same place, so the two cannot disagree: what
            # was worth sending again is, once the attempts are spent, still not evidence.
            if error.status is not None and not error.again:
                return False
            raise

        # An object is the evidence. The schema is the caller's, so there is no key of our
        # own to look for, and an endpoint that dropped the response format answers the
        # question in prose rather than in JSON.
        try:
            return isinstance(json.loads(completion.text), dict)
        except json.JSONDecodeError:
            return False

    def _post(self, body: dict) -> dict:
        """The request, sent again where sending it again could plausibly do better.

        The loop is separate from the one in `ask.py` and holds its own budget, because a
        transport retry is the same question asked again and a validator retry is a
        different prompt. Sharing a counter would mean a rate limit spending an attempt the
        model never got to use.
        """
        url = self._endpoint.base_url.rstrip("/") + "/chat/completions"
        attempt = 0
        waited = 0.0
        while True:
            attempt += 1
            try:
                return self._sent(url, body)
            except ModelError as error:
                delay = _backoff(error, attempt, waited)
                if delay is None:
                    raise _exhausted(error, attempt) from error
                self._sleep(delay)
                waited += delay

    def _sent(self, url: str, body: dict) -> dict:
        try:
            response = self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {self._endpoint.api_key}"},
                timeout=self._endpoint.timeout,
            )
        except httpx.TimeoutException as error:
            # Not sent again, unlike the connection failures below. The timeout is the
            # caller's own number and it has already been waited in full; spending it two
            # more times would be a three minute question on a one minute setting, which is
            # a worse answer than saying the endpoint did not answer.
            raise ModelError(f"the endpoint did not answer within {self._endpoint.timeout}s") from error
        except httpx.RequestError as error:
            raise ModelError(f"could not reach {self._redacted(url)}", again=True) from error

        if response.status_code >= 400:
            raise ModelError(
                f"the endpoint answered {response.status_code}: {self._redacted(response.text)[:500]}",
                status=response.status_code,
                # A rate limit and a server that broke are worth sending again. Every other
                # 4xx is about the request, and the request does not change between
                # attempts, so a 400 about a malformed body will be a 400 again.
                again=response.status_code == TOO_MANY or response.status_code >= 500,
                after=_retry_after(response.headers),
            )
        try:
            return response.json()
        except json.JSONDecodeError as error:
            raise ModelError("the endpoint answered with something that is not JSON") from error

    def _parse(self, payload: dict) -> Completion:
        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ModelError("the endpoint answered without a completion") from error
        if text is None:
            raise ModelError("the endpoint answered with an empty completion")
        return Completion(
            text=text,
            model=payload.get("model", self._endpoint.model),
            finish_reason=choice.get("finish_reason"),
            usage=payload.get("usage") or {},
        )

    def _redacted(self, text: str) -> str:
        """An endpoint that echoes the request, and several do, would otherwise put the
        key into an exception and from there into whatever reads one."""
        return text.replace(self._endpoint.api_key, "***") if self._endpoint.api_key else text


def _backoff(error: ModelError, attempt: int, waited: float) -> float | None:
    """How long to wait before sending the request again, or None where it should not be
    sent again.

    An endpoint that says how long to wait is waited, since it is the one that knows when
    its limit resets. It is a floor rather than an instruction: `Retry-After: 0`, and a date
    a client whose clock is a few seconds fast reads as already past, would otherwise remove
    the backoff altogether and send three requests to a rate limited endpoint inside a
    millisecond — which is worse for that endpoint than not retrying at all. So the wait is
    the longer of what was asked for and what this would have waited anyway.

    What that costs is that a `Retry-After` longer than what is left of the budget ends the
    question here rather than being slept through, which is the right way round: the
    alternative is a person watching a blank canvas for a minute on the endpoint's word.
    """
    if not error.again or attempt >= ATTEMPTS:
        return None
    delay = max(BACKOFF * 2 ** (attempt - 1), error.after or 0.0)
    return None if waited + delay > BACKOFF_BUDGET else delay


def _exhausted(error: ModelError, attempts: int) -> ModelError:
    """The last failure, saying how many times it happened where that was more than once.

    The message is what reaches the interface under "What the model said", and one 429 and
    three of them are different things to be told. The status and the rest ride along
    unchanged, because `constrains_output` reads them and a retried failure is the same
    failure.
    """
    if attempts == 1:
        return error
    return ModelError(
        f"{error} (sent {attempts} times)",
        status=error.status,
        again=error.again,
        after=error.after,
    )


def _retry_after(headers) -> float | None:
    """What the endpoint asked to be waited, in seconds, or None where it asked nothing.

    The header is either a count of seconds or an HTTP date, and both are sent in the wild.
    Anything else is read as nothing said rather than as a failure: a header nobody can
    parse is not a reason to lose the answer about the rate limit.

    The count is digits and nothing else, which is what the specification says it is. That
    matters here rather than being pedantry, because `float` also reads `inf` and `nan` and
    `1e400`: an endpoint sending any of those got a delay this file would then either sleep
    or treat as beyond the budget, from a header that says nothing at all. A date in the
    past is zero seconds, and what stops a zero from removing the backoff is `_backoff`,
    which floors it rather than obeying it.
    """
    said = headers.get("retry-after")
    if said is None:
        return None
    if said.strip().isdigit():
        return float(said.strip())
    try:
        when = parsedate_to_datetime(said)
    except (TypeError, ValueError):
        return None
    # A date with no zone is UTC by the specification's own reading, and `parsedate` returns
    # one for the `-0000` spelling. Subtracting an aware datetime from a naive one raises a
    # TypeError, which is not a ModelError and would leave this module as a 500.
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return max(0.0, (when - dt.datetime.now(dt.UTC)).total_seconds())
