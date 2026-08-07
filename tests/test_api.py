import json
import re
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import needs_warehouse
from fastapi.testclient import TestClient
from test_ask import ScriptedModel
from test_model import ENDPOINT
from test_spec_validation import EXPECTED_ERROR

from vizmith.api import (
    LOOPBACK,
    MODEL_CONFIGURATION,
    _allowed_hosts,
    _hostname,
    answers,
    app,
    constrains,
    model,
    saved,
    source,
)
from vizmith.ask import ATTEMPTS, SCHEMA
from vizmith.catalog import UNSUPPORTED, Held
from vizmith.config import SETTINGS, source_settings
from vizmith.dashboards import Dashboards
from vizmith.model import PROBE_PROMPT, Model, ModelError
from vizmith.profiler import SAMPLE_THRESHOLD, TableProfile, profile_table
from vizmith.query import build
from vizmith.relationships import CONFIRMED, OPEN, REJECTED, Confirmations
from vizmith.sources.databricks import WAIT_LIMIT
from vizmith.spec import output_columns

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))

REVENUE_BY_COUNTRY = FIXTURES / "valid" / "revenue_by_country.json"
ORDERS_PER_MONTH = FIXTURES / "valid" / "orders_per_month.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture
def client(catalog):
    """The API over the fixture database, which is also what records whether a request
    reached a source at all."""
    app.dependency_overrides[source] = lambda: catalog
    yield TestClient(app, base_url="http://127.0.0.1:8000")
    app.dependency_overrides.clear()


def test_health_reports_ok_without_a_configured_source(monkeypatch):
    for name in source_settings():
        monkeypatch.delenv(name, raising=False)

    response = TestClient(app, base_url="http://127.0.0.1:8000").get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["source"] is False


def test_health_reports_a_source_once_it_is_configured(monkeypatch):
    for name in source_settings():
        monkeypatch.setenv(name, "configured")

    assert TestClient(app, base_url="http://127.0.0.1:8000").get("/api/health").json()["source"] is True


def test_health_reports_whether_a_model_is_configured(monkeypatch):
    for name in MODEL_CONFIGURATION:
        monkeypatch.delenv(name, raising=False)
    assert TestClient(app, base_url="http://127.0.0.1:8000").get("/api/health").json()["model"] is False

    for name in MODEL_CONFIGURATION:
        monkeypatch.setenv(name, "configured")
    assert TestClient(app, base_url="http://127.0.0.1:8000").get("/api/health").json()["model"] is True


ORDERS = "vizmith.shop.orders"

# A table with a column the profiler leaves without samples, so the threshold can be
# asserted on a real profile rather than on a table that happens to sit below it.
SCANS = "vizmith.shop.shipment_scans"


def test_a_get_answers_with_the_profile_of_every_table_in_the_configured_schema(client, catalog):
    """The profiles rather than the names: this endpoint built them either way, and the
    panel asking for each one back paid a second freshness check per table."""
    response = client.get("/api/tables")

    assert response.status_code == 200
    tables = response.json()["tables"]
    assert [table["table"] for table in tables] == catalog.tables()
    assert all(table["columns"] and table["row_count"] for table in tables)


def test_a_get_returns_the_profile_the_prompt_path_was_given(catalog):
    """The panel's claim is that these are the figures the model saw, so the test is that
    the endpoint's answer is in the prompt, not that it looks like a plausible profile."""
    scripted = ScriptedModel(json.dumps(load(REVENUE_BY_COUNTRY)))
    app.dependency_overrides[source] = lambda: catalog
    app.dependency_overrides[model] = lambda: scripted
    try:
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        client.post("/api/ask", json={"question": "revenue by country"})
        response = client.get(f"/api/tables/{ORDERS}")
    finally:
        app.dependency_overrides.clear()
        constrains.cache_clear()

    profile = TableProfile.from_dict(response.json())
    assert response.status_code == 200
    assert profile == profile_table(catalog, ORDERS)

    asked = scripted.prompts[0]
    assert f"{profile.table}, {profile.row_count} rows" in asked
    status = next(column for column in profile.columns if column.name == "status")
    assert f"{status.distinct_count} distinct" in asked
    assert "values: " + ", ".join(status.samples) in asked


def test_a_column_above_the_sample_threshold_comes_back_with_no_samples(client, fixture_db):
    """The threshold is the security boundary and this API does not widen it: none of the
    500 location codes reaches the response, not a trimmed list of them."""
    response = client.get(f"/api/tables/{SCANS}")

    columns = {column["name"]: column for column in response.json()["columns"]}
    assert columns["location_code"]["distinct_count"] > SAMPLE_THRESHOLD
    assert columns["location_code"]["samples"] == []
    values = fixture_db.execute(f"SELECT DISTINCT location_code FROM {SCANS}").fetchall()
    assert len(values) > SAMPLE_THRESHOLD
    assert [value for (value,) in values if value in response.text] == []


def test_no_table_endpoint_answers_with_a_row(client, catalog):
    """A profile of every column of every table, and nothing in any of them that a row
    could arrive through."""
    for name in catalog.tables():
        body = client.get(f"/api/tables/{name}").json()
        assert set(body) == {"table", "row_count", "columns"}
        for column in body["columns"]:
            assert set(column) == {
                "name",
                "type",
                "null_rate",
                "distinct_count",
                "distinct_count_exact",
                "minimum",
                "maximum",
                "samples",
            }


def test_a_second_request_for_a_table_does_not_profile_it_again(client, catalog):
    """Profiling a table is two warehouse queries. A panel that reads a profile per table
    would pay for the schema again on every render."""
    client.get("/api/tables")
    client.get(f"/api/tables/{ORDERS}")
    client.get(f"/api/tables/{SCANS}")
    profiled = list(catalog.statements)

    client.get(f"/api/tables/{ORDERS}")
    client.get(f"/api/tables/{SCANS}")
    client.get("/api/tables")

    assert profiled
    assert catalog.statements == profiled


def test_reading_one_table_does_not_profile_the_schema_around_it(client, catalog):
    """The panel asks for a profile per table, so a request that profiled everything to
    answer for one would pay for the schema once per table in the tree."""
    client.get(f"/api/tables/{ORDERS}")

    assert catalog.statements
    assert all('"orders"' in statement for statement in catalog.statements)


def test_a_question_after_a_profile_request_profiles_nothing_again(asking, catalog):
    """Both read the same profiles, so the wait is paid once however it is reached."""
    client = asking(json.dumps(load(REVENUE_BY_COUNTRY)))
    client.get("/api/tables")
    profiled = list(catalog.statements)

    response = client.post("/api/ask", json={"question": "revenue by country"})

    assert response.status_code == 200
    assert catalog.statements[: len(profiled)] == profiled
    assert len(catalog.statements) == len(profiled) + 1, "the question ran its own query and no more"


def test_a_restarted_server_does_not_profile_the_schema_again(catalog):
    """The cache is on disk, so what a restart used to cost is now paid once. Two clients
    over one state directory is what a restart looks like from here."""
    app.dependency_overrides[source] = lambda: catalog
    try:
        TestClient(app, base_url="http://127.0.0.1:8000").get("/api/tables")
        profiled = list(catalog.statements)
        listed = TestClient(app, base_url="http://127.0.0.1:8000").get("/api/tables")
    finally:
        app.dependency_overrides.clear()

    assert profiled
    assert [table["table"] for table in listed.json()["tables"]] == catalog.tables()
    assert catalog.statements == profiled


def test_a_table_written_to_under_a_running_server_is_profiled_again(client, catalog):
    """What the old cache could not do. The schema moved, and the panel and the prompt both
    describe the table as it is now rather than as it was when the server started."""
    client.get("/api/tables")
    profiled = list(catalog.statements)

    catalog.modified_times["orders"] = "2"
    response = client.get(f"/api/tables/{ORDERS}")

    fresh = catalog.statements[len(profiled) :]
    assert response.status_code == 200
    assert fresh
    assert all('"orders"' in statement for statement in fresh)


def test_a_table_name_the_schema_does_not_hold_is_refused_naming_it(client):
    """The panel takes its names from the list, so a miss means the table went away between
    the two requests. That reads as a missing table, not as a crash."""
    response = client.get("/api/tables/vizmith.shop.nowhere")

    assert response.status_code == 404
    assert "vizmith.shop.nowhere" in response.json()["errors"][0]


def test_a_source_that_refuses_while_profiling_is_named_as_the_source(refusing):
    response = refusing(REFUSALS[0]).get("/api/tables")

    assert response.status_code == 502
    assert response.json() == {"errors": [REFUSALS[0]], "spoke": "source"}


@pytest.fixture
def asking(catalog):
    """The API with a scripted model behind it, so a question is answered without a call."""

    def client(*answers):
        # One model across requests, so a second question meets a model that has already
        # said what it had to say rather than a fresh one repeating itself.
        scripted = ScriptedModel(*answers)
        app.dependency_overrides[source] = lambda: catalog
        app.dependency_overrides[model] = lambda: scripted
        return TestClient(app, base_url="http://127.0.0.1:8000")

    yield client
    app.dependency_overrides.clear()
    constrains.cache_clear()


@pytest.fixture
def posting(catalog):
    """The API over the real adapter and a transport that answers without a network, so a
    test reads the request that reached the endpoint rather than the call that reached the
    adapter. `probe` is the status each probe is answered with, the last one repeating."""
    sent: list[httpx.Request] = []

    def client(*answers: str, probe: tuple[int, ...] = (200,)):
        replies = list(answers)
        statuses = list(probe)

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            if _is_probe(request):
                status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
                if status == 200:
                    return httpx.Response(200, json=_completion('{"ok": true}'))
                return httpx.Response(status, json={"error": "not answering that"})
            return httpx.Response(200, json=_completion(replies.pop(0) if replies else "{}"))

        writer = Model(ENDPOINT, httpx.Client(transport=httpx.MockTransport(handler)))
        app.dependency_overrides[source] = lambda: catalog
        app.dependency_overrides[model] = lambda: writer
        return TestClient(app, base_url="http://127.0.0.1:8000"), sent

    yield client
    app.dependency_overrides.clear()
    constrains.cache_clear()


class RefusingCatalog:
    """A source that answers every statement with a failure. It is the fixture catalog for
    everything else, so a spec still compiles against real names and the failure is the
    only thing being tested. No warehouse is reached."""

    def __init__(self, catalog, message: str):
        self.dialect = catalog.dialect
        self.scope = catalog.scope
        self._catalog = catalog
        self._message = message

    def tables(self):
        return self._catalog.tables()

    def describe(self, name):
        return self._catalog.describe(name)

    def relationships(self):
        return self._catalog.relationships()

    def modified(self, name):
        """None, which is what a source whose statements all fail honestly reports: a
        modified time is a statement too, and one that cannot run has no time to give. So
        nothing is cached and the failure below is what every request meets."""

    def run(self, sql, parameters=None):
        raise RuntimeError(self._message)


class UnreachableModel(ScriptedModel):
    """An endpoint that cannot be reached: a wrong base URL, a refused connection or a
    timeout, which the adapter reports as one type. The prompt is kept before the failure,
    so a test can count what an endpoint that answers nothing was asked."""

    def complete(self, prompt: str, schema: dict | None = None):
        self.prompts.append(prompt)
        raise ModelError("connection refused")


# What DatabricksCatalog raises: a statement the warehouse did not finish, quoting the
# source's own error, a result too large for the one chunk a response carries, and a
# statement it waited out and cancelled.
REFUSALS = (
    "statement StatementState.FAILED: [TABLE_OR_VIEW_NOT_FOUND] the table cannot be found",
    "statement returned more rows than one chunk holds",
    f"statement not finished after {WAIT_LIMIT} seconds, cancelled",
)


@pytest.fixture
def refusing(catalog):
    """The API over a source that refuses every statement, and a model that answers without
    a call, so a source failure is the only thing that can happen."""

    def client(message: str):
        app.dependency_overrides[source] = lambda: RefusingCatalog(catalog, message)
        app.dependency_overrides[model] = lambda: ScriptedModel()
        return TestClient(app, base_url="http://127.0.0.1:8000")

    yield client
    app.dependency_overrides.clear()
    constrains.cache_clear()


@pytest.fixture
def unreachable(catalog):
    """The API and the model behind it, so a test reads how often an endpoint that answers
    nothing was asked."""
    writer = UnreachableModel()
    app.dependency_overrides[source] = lambda: catalog
    app.dependency_overrides[model] = lambda: writer
    yield TestClient(app, base_url="http://127.0.0.1:8000"), writer
    app.dependency_overrides.clear()
    constrains.cache_clear()


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _is_probe(request: httpx.Request) -> bool:
    """The probe is whichever request carries the probe's prompt, so what answers one
    depends on the request rather than on where it fell in the sequence. Its schema no
    longer tells it apart from a question, which is the whole point of the probe."""
    body = json.loads(request.content)
    return body["messages"][0]["content"] == PROBE_PROMPT


def test_a_question_comes_back_as_the_spec_it_wrote_and_the_rows_it_returned(asking):
    spec = load(REVENUE_BY_COUNTRY)

    body = asking(json.dumps(spec)).post("/api/ask", json={"question": "revenue by country"}).json()

    assert body["spec"] == spec
    assert [list(row) for row in body["rows"]] == [output_columns(spec["query"])] * len(body["rows"])


def test_a_model_that_never_writes_a_valid_spec_answers_with_the_validator(asking):
    """The server did its job. The model did not, and the difference has to be readable."""
    refused = json.dumps(load(FIXTURES / "invalid" / "missing_limit.json"))

    response = asking(refused, refused, refused).post("/api/ask", json={"question": "anything"})

    assert response.status_code == 400
    assert any("'limit' is a required property" in error for error in response.json()["errors"])


def test_no_answer_carries_the_model_key(asking, monkeypatch):
    """The key is server configuration. A question is not a way to read one back."""
    monkeypatch.setenv("VIZMITH_MODEL_KEY", "the-key-that-stays-here")

    client = asking(json.dumps(load(REVENUE_BY_COUNTRY)))
    answered = client.post("/api/ask", json={"question": "revenue by country"})
    # The scripted model has nothing left to say, so this one fails on its own.
    failed = client.post("/api/ask", json={"question": "anything"})

    assert failed.status_code == 400
    assert "the-key-that-stays-here" not in answered.text
    assert "the-key-that-stays-here" not in failed.text


def test_an_endpoint_that_honours_a_schema_is_sent_the_spec_schema(posting):
    """The probe says the endpoint can be constrained, so the question that follows it
    carries the schema instead of describing it and hoping."""
    client, sent = posting(json.dumps(load(REVENUE_BY_COUNTRY)))

    client.post("/api/ask", json={"question": "revenue by country"})

    probe, question = (json.loads(request.content) for request in sent)
    assert probe["response_format"]["json_schema"]["schema"] == SCHEMA
    assert question["response_format"]["json_schema"]["schema"] == SCHEMA


def test_an_endpoint_that_refuses_a_schema_is_asked_without_one_and_still_retries(posting):
    """Ollama's route answers the probe with a refusal, and so does OpenAI, whose
    structured output is a subset of JSON Schema that this one sits outside of. The retry
    loop is the fallback in both cases, so it has to still run rather than the question
    failing with the probe."""
    refused = json.dumps(load(FIXTURES / "invalid" / "missing_limit.json"))
    client, sent = posting(*[refused] * ATTEMPTS, probe=(400,))

    response = client.post("/api/ask", json={"question": "anything"})

    assert response.status_code == 400
    assert len(sent) == 1 + ATTEMPTS
    assert all("response_format" not in json.loads(request.content) for request in sent[1:])


def test_the_endpoint_is_probed_once_however_many_questions_follow(posting):
    """The probe is a billed request. Paying for the same answer per question is what the
    cache exists to prevent."""
    spec = json.dumps(load(REVENUE_BY_COUNTRY))
    client, sent = posting(spec, spec)

    client.post("/api/ask", json={"question": "revenue by country"})
    client.post("/api/ask", json={"question": "revenue by country"})

    schemas = [json.loads(request.content)["response_format"]["json_schema"]["schema"] for request in sent]
    assert schemas == [SCHEMA, SCHEMA, SCHEMA]


def test_a_probe_that_never_got_an_answer_is_not_remembered_as_a_no(posting):
    """A 503 says nothing about the endpoint. Remembering it would send every question for
    the rest of the process down the unconstrained path for a reason that has nothing to
    do with what the endpoint can do."""
    client, sent = posting(json.dumps(load(REVENUE_BY_COUNTRY)), probe=(503, 200))

    failed = client.post("/api/ask", json={"question": "revenue by country"})
    answered = client.post("/api/ask", json={"question": "revenue by country"})

    assert failed.status_code == 502
    assert failed.json()["spoke"] == "model"
    assert answered.status_code == 200
    assert [_is_probe(request) for request in sent] == [True, True, False]


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_a_valid_fixture_returns_the_result_set_contract(path, client):
    spec = load(path)

    body = client.post("/api/execute", json={"spec": spec}).json()

    assert body["spec"] == spec
    assert body["rows"]
    for row in body["rows"]:
        assert list(row) == output_columns(spec["query"])


def test_a_measure_arrives_as_a_number_rather_than_as_text(client):
    """A quantitative axis reads a number. Serialising the rows a second time on the way
    out would turn a decimal into a string, which draws as a category."""
    response = client.post("/api/execute", json={"spec": load(REVENUE_BY_COUNTRY)})

    assert all(isinstance(row["revenue"], (int, float)) for row in response.json()["rows"])


def test_a_temporal_value_arrives_as_iso_8601_text(client):
    """JSON has no date, so text is the shape a temporal value crosses the wire in, and
    the contract says which text: what `isoformat` writes, with no zone on the end of it.
    That is what the renderer parses, so a second encoder that wrote a different one would
    move every mark on a time axis rather than fail. See ROADMAP.md."""
    body = client.post("/api/execute", json={"spec": load(ORDERS_PER_MONTH)}).json()

    assert body["rows"]
    assert all(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", row["month"]) for row in body["rows"]
    ), body["rows"][0]


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_an_invalid_fixture_is_refused_with_its_own_validator_error(path, client, catalog):
    response = client.post("/api/execute", json={"spec": load(path)})

    assert response.status_code == 400
    expected = EXPECTED_ERROR[path.name]
    errors = response.json()["errors"]
    assert any(expected in error for error in errors), f"expected {expected!r}, got {errors!r}"
    assert catalog.statements == [], "an invalid spec reached the database"


@pytest.mark.parametrize("message", REFUSALS)
def test_a_statement_the_source_refused_comes_back_with_the_sources_message(message, refusing):
    """A 500 with no words told the person to check the source and handed them nothing to
    check it with. The message is what the source said, passed on rather than paraphrased,
    in the shape the validator's refusal already uses."""
    response = refusing(message).post("/api/execute", json={"spec": load(REVENUE_BY_COUNTRY)})

    assert response.status_code == 502
    assert response.json() == {"errors": [message], "spoke": "source"}


def test_a_limit_by_that_cannot_be_re_aggregated_comes_back_with_the_builders_message(client, catalog):
    """Whether a measure can be re-aggregated is a property of the aggregate behind it,
    which the validator has not resolved and the builder has. So it is the one spec rule
    that runs after validation, and what refused is the spec rather than the source."""
    spec = load(FIXTURES / "valid" / "revenue_by_category_stacked.json")
    spec["query"]["aggregates"][0]["fn"] = "avg"

    response = client.post("/api/execute", json={"spec": spec})

    assert response.status_code == 400, "nothing behind the server was reached"
    assert response.json()["spoke"] == "spec"
    assert "cannot be re-aggregated" in response.json()["errors"][0]
    assert catalog.statements == [], "a spec the builder refused reached the database"


def test_a_model_that_cannot_be_reached_comes_back_with_the_adapters_message(unreachable):
    """The same refusal a failed statement gets, because a question that reached no model
    is as much a failure behind the server as one the source refused. The retry loop is
    what a rejected answer gets, so an endpoint that answered nothing is asked once rather
    than three times."""
    client, writer = unreachable

    response = client.post("/api/ask", json={"question": "revenue by country"})

    assert response.status_code == 502
    assert response.json() == {"errors": ["connection refused"], "spoke": "model"}
    assert len(writer.prompts) == 1


def test_a_source_that_fails_while_profiling_names_the_source_rather_than_the_model(refusing):
    """Profiling reads the source before the model is asked anything, so it is the first
    thing a question can fail on. Calling that a model failure would send the person to
    the endpoint over something the warehouse said."""
    client = refusing(REFUSALS[0])

    response = client.post("/api/ask", json={"question": "anything"})

    assert response.status_code == 502
    assert response.json() == {"errors": [REFUSALS[0]], "spoke": "source"}


def test_a_spec_that_is_not_an_object_gets_a_validator_error(client):
    """The request leaves the spec untyped so that these reach the validator. A typed one
    would answer in pydantic's wording instead, which no retry loop can use."""
    response = client.post("/api/execute", json={"spec": "not a spec"})

    assert response.status_code == 400
    assert any("is not of type 'object'" in error for error in response.json()["errors"])


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_no_response_carries_the_sql_it_ran(path, client, catalog):
    spec = load(path)
    sql, _ = build(spec, catalog)

    for endpoint in ("/api/validate", "/api/execute"):
        body = client.post(endpoint, json={"spec": spec}).text
        assert sql not in body
        assert "SELECT" not in body


def test_validating_a_valid_spec_reaches_no_source(client, catalog):
    response = client.post("/api/validate", json={"spec": load(REVENUE_BY_COUNTRY)})

    assert response.json() == {"errors": []}
    assert catalog.statements == []


@needs_warehouse
def test_the_api_answers_with_rows_from_the_warehouse(live_catalog):
    """The whole path a user's chart takes: spec in over HTTP, warehouse round trip, rows
    out in the renderer's contract. Nothing below this is mocked."""
    spec = load(REVENUE_BY_COUNTRY)
    app.dependency_overrides[source] = lambda: live_catalog
    try:
        response = TestClient(app, base_url="http://127.0.0.1:8000").post("/api/execute", json={"spec": spec})
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert [list(row) for row in body["rows"]] == [output_columns(spec["query"])] * len(body["rows"])
    assert all(isinstance(row["revenue"], (int, float)) for row in body["rows"])
    assert "SELECT" not in response.text


@pytest.fixture
def browsing(catalog, tmp_path):
    """The API over the fixture catalog with a confirmations file of its own, which is
    what the relationship endpoints write to. Nothing here reaches a home directory."""
    app.dependency_overrides[source] = lambda: catalog
    app.dependency_overrides[answers] = lambda: Confirmations(tmp_path / "relationships.json")
    yield TestClient(app, base_url="http://127.0.0.1:8000")
    app.dependency_overrides.clear()


ORDERS = "vizmith.shop.orders"
CUSTOMERS = "vizmith.shop.customers"
CARRIERS = "vizmith.shop.carriers"
SHIPMENTS = "vizmith.shop.shipments"

CARRIER_ID = {
    "left_table": SHIPMENTS,
    "left_column": "carrier_id",
    "right_table": CARRIERS,
    "right_column": "id",
}


def test_relationships_report_what_is_declared_and_what_is_suggested(browsing):
    body = browsing.get("/api/relationships").json()["relationships"]
    kinds = {f"{r['left_table']}.{r['left_column']}": (r["kind"], r["state"]) for r in body}

    assert kinds[f"{ORDERS}.customer_id"] == ("declared", CONFIRMED)
    assert kinds[f"{SHIPMENTS}.carrier_id"] == ("suggested", OPEN)


class Overlapping:
    """A source that records how many descriptions were in flight at once.

    Every call waits, because a metadata read is nearly all waiting and a call that returns
    instantly cannot show whether two of them overlapped. The wait is short enough that the
    suite does not notice and long enough that a sequential caller cannot reach the peak a
    concurrent one does."""

    WAIT = 0.02

    def __init__(self, catalog):
        self.dialect = catalog.dialect
        self.scope = catalog.scope
        self._catalog = catalog
        self._lock = threading.Lock()
        self.described: list[str] = []
        self.in_flight = 0
        self.peak = 0

    def tables(self):
        return self._catalog.tables()

    def describe(self, name):
        with self._lock:
            self.described.append(name)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        time.sleep(self.WAIT)
        with self._lock:
            self.in_flight -= 1
        return self._catalog.describe(name)

    def relationships(self):
        return self._catalog.relationships()

    def modified(self, name):
        return self._catalog.modified(name)

    def run(self, sql, parameters=None):
        return self._catalog.run(sql, parameters)


@pytest.fixture
def overlapping(catalog, tmp_path):
    source_ = Overlapping(catalog)
    app.dependency_overrides[source] = lambda: source_
    app.dependency_overrides[answers] = lambda: Confirmations(tmp_path / "relationships.json")
    yield TestClient(app, base_url="http://127.0.0.1:8000"), source_
    app.dependency_overrides.clear()


def test_a_join_path_describes_the_schema_without_waiting_for_one_table_at_a_time(overlapping):
    """Resolving a join path builds the relationship graph, and building it is one round
    trip per table. Those ran one after another, so a drop paid a schema's worth of latency
    before the well could answer — on a hundred and fifty tables, most of a minute per drag.

    What this asserts is the overlap rather than a duration: a timing assertion goes red on
    a loaded runner for a reason nobody caused. Each table is still described exactly once,
    because concurrency is not licence to ask twice."""
    client, source_ = overlapping

    client.get("/api/join-path", params={"left": ORDERS, "right": CUSTOMERS})

    assert sorted(source_.described) == sorted(source_.tables())
    assert source_.peak > 1, "the descriptions ran one at a time"


def test_a_join_path_reads_each_table_once_rather_than_twice(browsing, catalog):
    """The graph is a table's columns and the keys it declares, and both arrive in the one
    response a source answers a description with. Asking `relationships()` for the keys was
    the schema described a second time inside the same request, so a drag paid two round
    trips per table for one graph.

    Each table exactly once, and the declared keys still in the answer, because halving the
    reads by losing a fact is not halving anything."""
    catalog.described.clear()

    resolved = browsing.get("/api/join-path", params={"left": ORDERS, "right": CUSTOMERS})

    assert sorted(catalog.described) == sorted(catalog.tables()), "a table was read twice"
    assert resolved.json()["joins"] == [
        {"table": CUSTOMERS, "on": [{"left": f"{ORDERS}.customer_id", "right": f"{CUSTOMERS}.id"}]}
    ]


def test_the_drag_after_the_first_describes_nothing(catalog, tmp_path):
    """What holding the shape buys, on the gesture direct manipulation exists for. Dragging
    a column from a table the query does not already read asks for a join path, which
    rebuilds the graph, which described the whole schema — every time, with no cache on the
    one path a person triggers most.

    Through the configured source rather than the bare fixture, because the hold is what
    `source` wraps the catalog in and this is the saving it exists for. The listing is not
    held and is not counted: it is one call for the schema, and it is how a table somebody
    created is noticed."""
    # One source for both requests, which is what `source` is: a hold belongs to the source
    # object, so a source rebuilt per request would hold nothing.
    held = Held(catalog, hold=30.0, shape=300.0, clock=lambda: 0.0)
    app.dependency_overrides[source] = lambda: held
    app.dependency_overrides[answers] = lambda: Confirmations(tmp_path / "relationships.json")
    try:
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        client.get("/api/join-path", params={"left": ORDERS, "right": CUSTOMERS})
        catalog.described.clear()
        client.get("/api/join-path", params={"left": ORDERS, "right": CUSTOMERS})
    finally:
        app.dependency_overrides.clear()

    assert catalog.described == [], "the second drag described the schema again"


def test_confirming_a_suggestion_makes_it_resolve(browsing):
    """The confirmation is the whole gate. Before it there is no path, after it there is
    one, and nothing in between guesses."""
    refused = browsing.get("/api/join-path", params={"left": SHIPMENTS, "right": CARRIERS})

    browsing.post("/api/relationships", json={**CARRIER_ID, "answer": CONFIRMED})
    resolved = browsing.get("/api/join-path", params={"left": SHIPMENTS, "right": CARRIERS})

    assert refused.status_code == 400
    assert "no confirmed relationship" in refused.json()["errors"][0]
    assert resolved.json()["joins"] == [
        {
            "table": CARRIERS,
            "on": [{"left": f"{SHIPMENTS}.carrier_id", "right": f"{CARRIERS}.id"}],
        }
    ]


def test_an_answer_survives_a_reload(browsing):
    browsing.post("/api/relationships", json={**CARRIER_ID, "answer": CONFIRMED})

    listed = browsing.get("/api/relationships").json()["relationships"]
    carriers = next(r for r in listed if r["left_column"] == "carrier_id")

    assert carriers["state"] == CONFIRMED


def test_a_rejected_suggestion_is_not_listed_again(browsing):
    browsing.post("/api/relationships", json={**CARRIER_ID, "answer": REJECTED})

    listed = browsing.get("/api/relationships").json()["relationships"]

    assert all(r["left_column"] != "carrier_id" for r in listed)


def test_a_confirmation_can_be_taken_back(browsing):
    browsing.post("/api/relationships", json={**CARRIER_ID, "answer": CONFIRMED})
    browsing.post("/api/relationships", json={**CARRIER_ID, "answer": OPEN})

    resolved = browsing.get("/api/join-path", params={"left": SHIPMENTS, "right": CARRIERS})

    assert resolved.status_code == 400


def test_a_declared_relationship_is_not_a_persons_to_approve(browsing):
    response = browsing.post(
        "/api/relationships",
        json={
            "left_table": ORDERS,
            "left_column": "customer_id",
            "right_table": CUSTOMERS,
            "right_column": "id",
            "answer": REJECTED,
        },
    )

    assert response.status_code == 400
    assert "declared by the source" in response.json()["errors"][0]


def test_an_answer_about_a_pair_nothing_suggested_is_refused(browsing):
    response = browsing.post(
        "/api/relationships",
        json={
            "left_table": ORDERS,
            "left_column": "status",
            "right_table": CUSTOMERS,
            "right_column": "country",
            "answer": CONFIRMED,
        },
    )

    assert response.status_code == 404
    assert "nothing relates" in response.json()["errors"][0]


def test_a_join_path_crosses_an_intermediate_table(browsing):
    """customers to order_items through orders, both hops declared, so this resolves with
    nothing confirmed by hand. Each step names the table being joined rather than the one
    the foreign key points at."""
    body = browsing.get(
        "/api/join-path", params={"left": CUSTOMERS, "right": "vizmith.shop.order_items"}
    ).json()

    assert [join["table"] for join in body["joins"]] == [ORDERS, "vizmith.shop.order_items"]


def test_a_request_cannot_name_a_source_or_carry_sql(client, catalog):
    """The source is server configuration, so anything else a request holds is not one."""
    response = client.post(
        "/api/execute",
        json={"spec": load(REVENUE_BY_COUNTRY), "source": "postgres://elsewhere/db", "sql": "SELECT 1"},
    )

    assert response.status_code == 200
    assert response.json()["rows"]
    assert "elsewhere" not in "".join(catalog.statements)


@pytest.fixture
def keeping(catalog, tmp_path):
    """The API with a dashboard file of its own. The source is overridden too, so a test
    can assert that saving and listing reach no warehouse rather than that they happen to
    have credentials."""
    app.dependency_overrides[source] = lambda: catalog
    app.dependency_overrides[saved] = lambda: Dashboards(tmp_path / "dashboards.json")
    yield TestClient(app, base_url="http://127.0.0.1:8000")
    app.dependency_overrides.clear()


def test_a_dashboard_is_saved_and_read_back_over_http(keeping):
    tiles = [
        {"spec": load(REVENUE_BY_COUNTRY), "width": 2},
        {"spec": load(ORDERS_PER_MONTH), "width": 1},
    ]

    saving = keeping.put("/api/dashboards/Revenue", json={"tiles": tiles})
    read = keeping.get("/api/dashboards/Revenue")

    assert saving.status_code == 200
    assert read.status_code == 200
    assert read.json() == {"name": "Revenue", "tiles": tiles}


def test_the_list_names_every_dashboard_and_how_many_tiles_it_holds(keeping):
    keeping.put("/api/dashboards/Revenue", json={"tiles": [{"spec": load(REVENUE_BY_COUNTRY)}]})
    keeping.put(
        "/api/dashboards/Shipping",
        json={"tiles": [{"spec": load(REVENUE_BY_COUNTRY)}, {"spec": load(ORDERS_PER_MONTH)}]},
    )

    body = keeping.get("/api/dashboards").json()

    assert body["dashboards"] == [
        {"name": "Revenue", "tiles": 1},
        {"name": "Shipping", "tiles": 2},
    ]


def test_the_list_carries_no_spec(keeping):
    """A menu of names is what it is for. Answering it with every spec of every dashboard
    would send the whole store to draw a list."""
    keeping.put("/api/dashboards/Revenue", json={"tiles": [{"spec": load(REVENUE_BY_COUNTRY)}]})

    assert "query" not in keeping.get("/api/dashboards").text


def test_a_dashboard_is_deleted_and_is_then_a_404(keeping):
    keeping.put("/api/dashboards/Revenue", json={"tiles": [{"spec": load(REVENUE_BY_COUNTRY)}]})

    deleted = keeping.delete("/api/dashboards/Revenue")
    gone = keeping.get("/api/dashboards/Revenue")

    assert deleted.status_code == 200
    assert gone.status_code == 404
    assert "Revenue" in gone.json()["errors"][0]
    assert keeping.delete("/api/dashboards/Revenue").status_code == 404


def test_a_tile_the_validator_rejects_is_refused_with_the_validators_words(keeping):
    """The same `errors` list every other refusal here uses, so the interface keeps one
    way to show one, and the tile is named by where it sits on the grid."""
    response = keeping.put(
        "/api/dashboards/Revenue",
        json={"tiles": [{"spec": load(REVENUE_BY_COUNTRY)}, {"spec": load(INVALID[0])}]},
    )

    assert response.status_code == 400
    assert all(error.startswith("tile 2: ") for error in response.json()["errors"])
    assert keeping.get("/api/dashboards/Revenue").status_code == 404


def test_saving_and_listing_dashboards_reach_no_source(keeping, catalog):
    """A dashboard is specs. Running one is what runs a query, and that goes through
    /api/execute like any other spec."""
    keeping.put("/api/dashboards/Revenue", json={"tiles": [{"spec": load(REVENUE_BY_COUNTRY)}]})
    keeping.get("/api/dashboards")
    keeping.get("/api/dashboards/Revenue")

    assert catalog.statements == []


def test_a_dashboard_is_kept_in_the_state_directory_and_nowhere_else(catalog, state_dir):
    """The default path rather than an overridden one, which is what says a saved
    dashboard sits beside the relationship answers instead of somewhere a test invented."""
    app.dependency_overrides[source] = lambda: catalog
    try:
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        client.put("/api/dashboards/Revenue", json={"tiles": [{"spec": load(REVENUE_BY_COUNTRY)}]})
    finally:
        app.dependency_overrides.clear()

    assert json.loads((state_dir / "dashboards.json").read_text())["dashboards"]["Revenue"]


def test_every_tile_of_a_saved_dashboard_still_runs_through_execute(keeping):
    """What a tile is drawn from. There is no endpoint that runs a dashboard, because a
    second path to the source would be a second answer to what a spec means."""
    tiles = [{"spec": load(REVENUE_BY_COUNTRY)}, {"spec": load(ORDERS_PER_MONTH)}]
    keeping.put("/api/dashboards/Revenue", json={"tiles": tiles})

    for tile in keeping.get("/api/dashboards/Revenue").json()["tiles"]:
        response = keeping.post("/api/execute", json={"spec": tile["spec"]})
        assert response.status_code == 200
        assert response.json()["rows"]


DAMAGED = "{ not json at all"


@pytest.fixture
def unreadable(catalog, tmp_path):
    """The API over state files that hold damage. Both stores are built per request, so
    what this pins down is the shape of the answer rather than the shape of the failure:
    a person gets a sentence naming the file, not a stack trace and a status."""
    (tmp_path / "dashboards.json").write_text(DAMAGED)
    (tmp_path / "relationships.json").write_text(DAMAGED)
    app.dependency_overrides[source] = lambda: catalog
    app.dependency_overrides[saved] = lambda: Dashboards(tmp_path / "dashboards.json")
    app.dependency_overrides[answers] = lambda: Confirmations(tmp_path / "relationships.json")
    yield TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False), tmp_path
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "endpoint", "file"),
    [
        ("get", "/api/dashboards", "dashboards.json"),
        ("get", "/api/dashboards/Revenue", "dashboards.json"),
        ("delete", "/api/dashboards/Revenue", "dashboards.json"),
        ("get", "/api/relationships", "relationships.json"),
        ("get", f"/api/join-path?left={ORDERS}&right={CUSTOMERS}", "relationships.json"),
    ],
)
def test_a_state_file_that_cannot_be_read_is_refused_in_words(unreadable, method, endpoint, file):
    client, state = unreadable

    response = getattr(client, method)(endpoint)

    assert response.status_code == 503
    assert str(state / file) in response.json()["errors"][0]
    assert (state / file).read_text() == DAMAGED, "the damaged file was left where it is"


def test_a_save_against_a_damaged_file_writes_nothing_over_it(unreadable):
    """The refusal the reading endpoints give is worth little if the next save empties the
    file anyway, and that is the failure the store refuses to read for."""
    client, state = unreadable

    response = client.put(
        "/api/dashboards/Revenue", json={"tiles": [{"spec": load(REVENUE_BY_COUNTRY)}]}
    )

    assert response.status_code == 503
    assert (state / "dashboards.json").read_text() == DAMAGED


def test_browsing_the_relationships_costs_no_statement_and_no_freshness_check(browsing, catalog):
    """`suggest` reads a column's name and its type, which is what `describe` answers with.
    Building the graph from the profiles instead paid a freshness check per table — a
    DESCRIBE DETAIL the warehouse bills for — and two passes over every table on a cold
    cache, for figures nothing in the graph looks at. Dragging a column across tables asks
    for a join path, so that was the price of a drag."""
    relationships = browsing.get("/api/relationships")
    resolved = browsing.get("/api/join-path", params={"left": SHIPMENTS, "right": CARRIERS})

    assert relationships.status_code == 200
    assert resolved.status_code in (200, 400), resolved.text
    assert catalog.statements == [], "a question about two tables profiled the schema"
    assert catalog.freshness_checks == [], "a question about two tables asked what changed"


def test_a_key_of_an_unsupported_type_is_still_offered_as_a_join(catalog, tmp_path):
    """A profile leaves out a column whose type the catalog calls unsupported, so a graph
    built from the profiles silently dropped those pairs. A key of a type nothing can chart
    still joins perfectly well, and `describe` is where the full column list lives."""
    described = catalog.describe

    def unchartable_keys(name):
        """The same schema with the carrier key, and the id it points at, of a type this
        cannot draw. Nothing else about them changes."""
        table = described(name)
        columns = tuple(
            replace(column, type=UNSUPPORTED)
            if column.name in ("carrier_id", "id") and table.name.endswith(("carriers", "shipments"))
            else column
            for column in table.columns
        )
        return replace(table, columns=columns)

    catalog.describe = unchartable_keys
    app.dependency_overrides[source] = lambda: catalog
    app.dependency_overrides[answers] = lambda: Confirmations(tmp_path / "relationships.json")
    try:
        body = TestClient(app, base_url="http://127.0.0.1:8000").get("/api/relationships").json()
    finally:
        app.dependency_overrides.clear()
        catalog.describe = described

    suggested = {
        (entry["left_table"], entry["left_column"], entry["right_table"], entry["right_column"])
        for entry in body["relationships"]
    }
    assert (SHIPMENTS, "carrier_id", CARRIERS, "id") in suggested


class UnreachableCatalog:
    """A source whose metadata reads fail: a metastore that cannot be reached, or a
    workspace that refused the credential. `RefusingCatalog` above fails statements and
    answers metadata, which is a warehouse that is down behind a metastore that is up. This
    is the other half, and it is what every endpoint that reads the schema meets."""

    dialect = None

    def __init__(self, message: str):
        self._message = message

    def tables(self):
        raise RuntimeError(self._message)

    def describe(self, name):
        raise RuntimeError(self._message)

    def relationships(self):
        raise RuntimeError(self._message)

    def modified(self, name):
        raise RuntimeError(self._message)

    def run(self, sql, parameters=None):
        raise RuntimeError(self._message)


UNREACHABLE = "the metastore did not answer"


@pytest.fixture
def unreachable_source(tmp_path):
    app.dependency_overrides[source] = lambda: UnreachableCatalog(UNREACHABLE)
    app.dependency_overrides[answers] = lambda: Confirmations(tmp_path / "relationships.json")
    yield TestClient(app, base_url="http://127.0.0.1:8000")
    app.dependency_overrides.clear()


def test_a_source_that_cannot_be_read_is_named_as_the_source_on_every_endpoint(unreachable_source):
    """A failure after validation reaches the person as what failed and says which part
    failed, which is the branch that runs on the day the warehouse is down. It exists on
    five endpoints and was tested on one."""
    requests = [
        unreachable_source.get("/api/tables"),
        unreachable_source.get(f"/api/tables/{ORDERS}"),
        unreachable_source.get("/api/relationships"),
        unreachable_source.post("/api/relationships", json={**CARRIER_ID, "answer": CONFIRMED}),
        unreachable_source.get("/api/join-path", params={"left": SHIPMENTS, "right": CARRIERS}),
    ]

    for response in requests:
        assert response.status_code == 502, response.text
        assert response.json() == {"errors": [UNREACHABLE], "spoke": "source"}


def test_an_answer_that_is_not_one_is_refused_in_the_stores_words(browsing):
    """Confirm, not a match, or take it back. Anything else is a client that invented an
    answer, and what it gets back is the sentence the store refused it with."""
    response = browsing.post("/api/relationships", json={**CARRIER_ID, "answer": "probably"})

    assert response.status_code == 400
    assert "is not an answer" in response.json()["errors"][0]


def test_a_panel_load_asks_when_a_table_changed_once_per_table(client, catalog):
    """What filling the Fields panel costs, on a warm cache. It used to be a listing plus a
    request per table: one freshness check per table here, a second one there, and a schema
    listing per table for the 404 check on the single table endpoint. On a fifty table
    schema that is about 150 source calls, 100 of them statements a warehouse bills for,
    before anybody has asked a question."""
    client.get("/api/tables")  # the cold profile, which is a different question
    catalog.statements.clear()
    catalog.freshness_checks.clear()

    body = client.get("/api/tables").json()

    names = catalog.tables()
    assert [table["table"] for table in body["tables"]] == names
    assert catalog.freshness_checks == names, "a warm panel load asked twice per table"
    assert catalog.statements == [], "a warm panel load re-profiled something"


def test_a_burst_of_requests_asks_when_a_table_changed_once_per_table(catalog):
    """The floor the entry above left behind, taken lower. One request already asks once
    per table; a person produces several requests seconds apart — the panel, then a
    question, then a drag of a field into a well — and each of those used to pay a
    DESCRIBE DETAIL per table again. The source holds that answer for a window, so a burst
    asks once per table rather than once per table per request.

    Through the configured source rather than the bare fixture, because the hold is what
    `source` wraps the catalog in and this is the saving it exists for."""
    held = Held(catalog, hold=30.0, clock=lambda: 0.0)
    app.dependency_overrides[source] = lambda: held
    try:
        browser = TestClient(app, base_url="http://127.0.0.1:8000")
        for _ in range(4):
            browser.get("/api/tables")
    finally:
        app.dependency_overrides.clear()

    assert sorted(catalog.freshness_checks) == catalog.tables(), "a burst asked once per request"


def test_the_configured_source_holds_what_it_was_asked_about_freshness(monkeypatch):
    """The wiring, not the wrapper: a source built without it pays the burst above in
    billed statements, and nothing else in the suite would notice, since every test here
    puts its own catalog in the source's place."""
    for name in source_settings():
        monkeypatch.setenv(name, "configured")
    source.cache_clear()
    try:
        assert isinstance(source(), Held)
    finally:
        source.cache_clear()


def test_the_configured_kind_is_what_gets_built(monkeypatch, duckdb_file):
    """Which source Vizmith reads is configuration and never a request, so this is the one
    place a name in a settings file becomes a catalog. A checkout that predates there being
    a choice sets nothing and still gets the warehouse."""
    monkeypatch.setenv("VIZMITH_SOURCE", "duckdb")
    monkeypatch.setenv("VIZMITH_DUCKDB_PATH", str(duckdb_file))
    monkeypatch.setenv("VIZMITH_DUCKDB_DATABASE", "vizmith")
    monkeypatch.setenv("VIZMITH_DUCKDB_SCHEMA", "shop")
    source.cache_clear()
    try:
        built = source()
        assert isinstance(built, Held)
        assert built.scope.values == ("vizmith", "shop")
        assert built.tables()[0].startswith("vizmith.shop.")
    finally:
        source.cache_clear()


def test_a_kind_nothing_knows_says_what_the_choices_are(monkeypatch):
    """A person one character away from the kind they meant, told so at configuration
    rather than as a failed query."""
    monkeypatch.setenv("VIZMITH_SOURCE", "postgres")
    source.cache_clear()
    try:
        with pytest.raises(ValueError, match="databricks, duckdb"):
            source()
    finally:
        source.cache_clear()


def test_health_reports_which_kind_of_source_is_configured(monkeypatch):
    """The interface names the missing piece rather than letting it arrive as a failed
    query, and which piece is missing depends on the kind."""
    for name, _ in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIZMITH_SOURCE", "duckdb")
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    assert client.get("/api/health").json() == {
        "status": "ok",
        "version": client.get("/api/health").json()["version"],
        "source": False,
        "kind": "duckdb",
        "model": False,
    }

    for name in ("VIZMITH_DUCKDB_PATH", "VIZMITH_DUCKDB_DATABASE", "VIZMITH_DUCKDB_SCHEMA"):
        monkeypatch.setenv(name, "configured")
    assert client.get("/api/health").json()["source"] is True


def test_the_panel_is_filled_by_one_request(client, catalog):
    """The endpoint answers with what the panel reads, so the browser has nothing left to
    fan out for. A profile carries its columns and their figures, which is what a column
    row expands into and what a well needs to infer an aggregate."""
    table = client.get("/api/tables").json()["tables"][0]
    alone = client.get(f"/api/tables/{table['table']}").json()

    assert table == alone, "the panel would have to ask again for what it was already given"


def test_no_row_reaches_the_listing(client, catalog, fixture_db):
    """Unchanged, and worth saying again now that the listing carries profiles: a sample
    value is a value the source holds, and a column above the threshold carries none."""
    body = client.get("/api/tables").json()
    scans = next(table for table in body["tables"] if table["table"].endswith("shipment_scans"))
    columns = {column["name"]: column for column in scans["columns"]}

    assert columns["location_code"]["samples"] == [], "a high cardinality column sampled"
    assert scans["row_count"] == fixture_db.execute(
        "SELECT count(*) FROM vizmith.shop.shipment_scans"
    ).fetchone()[0]


SCANS_PER_LOCATION = FIXTURES / "valid" / "scans_per_location.json"


def as_arc(path: Path) -> dict:
    spec = load(path)
    spec["chart"]["mark"] = "arc"
    return spec


def test_a_spec_the_rules_do_not_refuse_comes_back_with_nothing_to_say(asking, catalog):
    """The common case, and it costs no request: what a critique may say is what is
    refusable, and a model asked to improve a chart that is fine will improve it."""
    scripted = ScriptedModel(json.dumps(load(SCANS_PER_LOCATION)))
    client = asking()
    app.dependency_overrides[model] = lambda: scripted

    body = client.post("/api/critique", json={"spec": load(SCANS_PER_LOCATION)}).json()

    assert body == {"findings": [], "spec": None, "errors": []}
    assert scripted.prompts == []


def test_a_critique_answers_with_what_a_rule_refused_and_a_spec_beside_it(asking, catalog):
    """A suggestion, not a change. What comes back sits beside the spec that was sent, and
    running it is `/api/execute` like every other spec, so a suggestion nobody took has
    cost no query."""
    client = asking(json.dumps(load(SCANS_PER_LOCATION)))
    client.get("/api/tables")  # the cold profile, which every path pays once
    catalog.statements.clear()

    body = client.post("/api/critique", json={"spec": as_arc(SCANS_PER_LOCATION)})

    assert body.status_code == 200
    assert body.json()["spec"] == load(SCANS_PER_LOCATION)
    assert "is the most that can" in body.json()["findings"][0]
    assert body.json()["errors"] == []
    assert catalog.statements == [], "a critique ran a query"


def test_a_critique_answers_with_no_rows(asking):
    """The endpoint that runs a spec is the one that returns rows. This one reads profiles
    and answers with a spec, which is what keeps a suggestion free."""
    body = asking(json.dumps(load(SCANS_PER_LOCATION))).post(
        "/api/critique", json={"spec": as_arc(SCANS_PER_LOCATION)}
    )

    assert "rows" not in body.json()


def test_a_spec_that_does_not_validate_is_refused_rather_than_critiqued(asking):
    """The findings are about a chart. A spec that is not one yet has a shorter list of
    things wrong with it, and it is the validator's to say."""
    response = asking().post("/api/critique", json={"spec": load(FIXTURES / "invalid" / "missing_limit.json")})

    assert response.status_code == 400
    assert any("'limit' is a required property" in error for error in response.json()["errors"])


def test_a_critique_the_model_never_answers_says_which_part_failed(asking, catalog):
    app.dependency_overrides[source] = lambda: catalog
    app.dependency_overrides[model] = lambda: UnreachableModel()

    response = TestClient(app, base_url="http://127.0.0.1:8000").post("/api/critique", json={"spec": as_arc(SCANS_PER_LOCATION)})

    assert response.status_code == 502
    assert response.json()["spoke"] == "model"


def test_a_suggestion_that_would_change_the_query_never_reaches_the_caller(asking):
    """The query is the question. What the model answered is refused here rather than
    offered, so the interface cannot put a different question under the same chart."""
    rewritten = load(SCANS_PER_LOCATION)
    rewritten["query"]["limit"] = 3

    body = asking(*[json.dumps(rewritten)] * ATTEMPTS).post(
        "/api/critique", json={"spec": as_arc(SCANS_PER_LOCATION)}
    ).json()

    assert body["spec"] is None
    assert body["findings"], "what the rule said still stands"
    assert any("does not change it" in error for error in body["errors"])


# A page the person has open in another tab is the caller these two headers exist to
# refuse. Nothing else on this API asks who is calling, so if these stop working, the
# warehouse is reachable from the web and the suite should say so.


def test_a_host_that_is_not_this_machine_is_refused(catalog):
    """DNS rebinding arrives as a same origin request, so no CORS setting is consulted and
    nothing downstream can tell it apart. What it cannot do is claim the name it dialled
    was localhost, which is why the name is what gets checked."""
    app.dependency_overrides[source] = lambda: catalog

    response = TestClient(app, base_url="http://evil.example:8000").get("/api/tables")

    assert response.status_code == 403
    assert "localhost" in response.json()["errors"][0]


def test_a_rebound_host_never_reaches_the_source(catalog):
    """The refusal happens before the endpoint body, so a rebound page costs no statement."""
    app.dependency_overrides[source] = lambda: catalog

    TestClient(app, base_url="http://evil.example:8000").get("/api/tables")

    assert catalog.statements == []


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_the_names_that_mean_this_machine_are_answered(catalog, host):
    app.dependency_overrides[source] = lambda: catalog

    response = TestClient(app, base_url=f"http://{host}:8000").get("/api/tables")

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("localhost", "localhost"),
        ("[::1]:8000", "::1"),
        ("http://127.0.0.1:8000", "127.0.0.1"),
        ("https://evil.example", "evil.example"),
        ("", None),
        (None, None),
    ],
)
def test_a_host_reduces_to_its_name(header, expected):
    """Both headers go through one reader so that `[::1]:8000` and `http://[::1]:8000`
    reduce to the same thing.

    The IPv6 case is here rather than driven through a client because TestClient cannot
    parse an IPv6 base URL, and the loopback address still has to be answered in the wild.
    """
    assert _hostname(header) == expected


def test_the_origin_a_sandboxed_frame_sends_is_not_a_name_this_server_answers():
    """`null` is a legal Origin and not a host name. It reduces to itself rather than to
    nothing, which is harmless as long as it never matches, so assert the thing that
    matters rather than the shape it takes on the way."""
    assert _hostname("null") not in _allowed_hosts()


def test_the_loopback_names_need_no_configuration():
    assert LOOPBACK <= _allowed_hosts()


def test_a_foreign_origin_is_refused_even_on_a_host_that_is_allowed(client):
    """The write half. A cross site POST carrying text/plain needs no preflight and gets
    parsed as JSON anyway, so a page that cannot read the answer can still spend money or
    confirm a relationship. It cannot forge Origin."""
    response = client.post(
        "/api/relationships",
        headers={"Origin": "https://evil.example"},
        json={
            "left": "orders", "left_column": "customer_id",
            "right": "customers", "right_column": "id", "answer": CONFIRMED,
        },
    )

    assert response.status_code == 403
    assert "evil.example" in response.json()["errors"][0]


def test_an_origin_of_this_machine_is_answered(client):
    response = client.post(
        "/api/execute",
        headers={"Origin": "http://127.0.0.1:8000"},
        json={"spec": load(REVENUE_BY_COUNTRY)},
    )

    assert response.status_code == 200


def test_a_request_with_no_origin_is_answered(client):
    """A same origin GET does not always carry one, so absent cannot be a refusal."""
    assert client.get("/api/tables").status_code == 200


def test_a_named_host_is_answered_and_only_the_named_one(catalog, monkeypatch):
    """The way out for somebody serving on a real interface on purpose. Binding elsewhere
    does not say which name clients arrive by, so it is stated rather than inferred."""
    app.dependency_overrides[source] = lambda: catalog
    monkeypatch.setenv("VIZMITH_ALLOWED_HOSTS", "vizmith.internal")

    assert TestClient(app, base_url="http://vizmith.internal:8000").get("/api/tables").status_code == 200
    assert TestClient(app, base_url="http://other.internal:8000").get("/api/tables").status_code == 403
