import json
import re
from pathlib import Path

import httpx
import pytest
from conftest import needs_warehouse
from fastapi.testclient import TestClient
from test_ask import ScriptedModel
from test_model import ENDPOINT
from test_spec_validation import EXPECTED_ERROR

from vizmith.api import (
    CONFIGURATION,
    MODEL_CONFIGURATION,
    answers,
    app,
    constrains,
    model,
    saved,
    source,
)
from vizmith.ask import ATTEMPTS, SCHEMA
from vizmith.catalog import WAIT_LIMIT
from vizmith.dashboards import Dashboards
from vizmith.model import PROBE_PROMPT, Model, ModelError
from vizmith.profiler import SAMPLE_THRESHOLD, TableProfile, profile_table
from vizmith.query import build
from vizmith.relationships import CONFIRMED, OPEN, REJECTED, Confirmations
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
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_reports_ok_without_a_configured_source(monkeypatch):
    for name in CONFIGURATION:
        monkeypatch.delenv(name, raising=False)

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["source"] is False


def test_health_reports_a_source_once_it_is_configured(monkeypatch):
    for name in CONFIGURATION:
        monkeypatch.setenv(name, "configured")

    assert TestClient(app).get("/api/health").json()["source"] is True


def test_health_reports_whether_a_model_is_configured(monkeypatch):
    for name in MODEL_CONFIGURATION:
        monkeypatch.delenv(name, raising=False)
    assert TestClient(app).get("/api/health").json()["model"] is False

    for name in MODEL_CONFIGURATION:
        monkeypatch.setenv(name, "configured")
    assert TestClient(app).get("/api/health").json()["model"] is True


ORDERS = "vizmith.shop.orders"

# A table with a column the profiler leaves without samples, so the threshold can be
# asserted on a real profile rather than on a table that happens to sit below it.
SCANS = "vizmith.shop.shipment_scans"


def test_a_get_lists_every_table_in_the_configured_schema(client, catalog):
    response = client.get("/api/tables")

    assert response.status_code == 200
    assert response.json()["tables"] == catalog.tables()


def test_a_get_returns_the_profile_the_prompt_path_was_given(catalog):
    """The panel's claim is that these are the figures the model saw, so the test is that
    the endpoint's answer is in the prompt, not that it looks like a plausible profile."""
    scripted = ScriptedModel(json.dumps(load(REVENUE_BY_COUNTRY)))
    app.dependency_overrides[source] = lambda: catalog
    app.dependency_overrides[model] = lambda: scripted
    try:
        client = TestClient(app)
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
        TestClient(app).get("/api/tables")
        profiled = list(catalog.statements)
        listed = TestClient(app).get("/api/tables")
    finally:
        app.dependency_overrides.clear()

    assert profiled
    assert listed.json()["tables"] == catalog.tables()
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
        return TestClient(app)

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
        return TestClient(app), sent

    yield client
    app.dependency_overrides.clear()
    constrains.cache_clear()


class RefusingCatalog:
    """A source that answers every statement with a failure. It is the fixture catalog for
    everything else, so a spec still compiles against real names and the failure is the
    only thing being tested. No warehouse is reached."""

    def __init__(self, catalog, message: str):
        self.dialect = catalog.dialect
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
        return TestClient(app)

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
    yield TestClient(app), writer
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
        response = TestClient(app).post("/api/execute", json={"spec": spec})
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
    yield TestClient(app)
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
    yield TestClient(app)
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
        client = TestClient(app)
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
