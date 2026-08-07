"""One adapter for every endpoint that speaks the OpenAI chat completions convention.

It sends a prompt and returns what came back. It writes no prompts, validates nothing and
retries nothing, because a spec is only valid once the validator says so and that belongs
to the caller.

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

import json
from dataclasses import dataclass, field

import httpx

# The probe's question. Short, because what is being tested is the schema attached to it
# and not the model's reading.
PROBE_PROMPT = "Answer with the smallest object the schema allows."


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
    status where there was one, and None where the request never got an answer."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Model:
    def __init__(self, endpoint: Endpoint, client: httpx.Client | None = None):
        self._endpoint = endpoint
        self._client = client or httpx.Client(timeout=endpoint.timeout)

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
        False. A timeout or an unreachable host is not, so it raises."""
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
            # A refusal is an answer. A timeout, an unreachable host or a server that
            # broke is not, and reporting those as "cannot constrain" would send the
            # caller down the unconstrained path for a reason that has nothing to do
            # with the endpoint's capabilities.
            if error.status is not None and error.status < 500:
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
        url = self._endpoint.base_url.rstrip("/") + "/chat/completions"
        try:
            response = self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {self._endpoint.api_key}"},
                timeout=self._endpoint.timeout,
            )
        except httpx.TimeoutException as error:
            raise ModelError(f"the endpoint did not answer within {self._endpoint.timeout}s") from error
        except httpx.RequestError as error:
            raise ModelError(f"could not reach {self._redacted(url)}") from error

        if response.status_code >= 400:
            raise ModelError(
                f"the endpoint answered {response.status_code}: {self._redacted(response.text)[:500]}",
                status=response.status_code,
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
