import json
from pathlib import Path

import pytest
from conftest import needs_warehouse
from fastapi.testclient import TestClient
from test_ask import ScriptedModel
from test_spec_validation import EXPECTED_ERROR

from vizmith.api import CONFIGURATION, MODEL_CONFIGURATION, app, model, source
from vizmith.query import build
from vizmith.spec import output_columns

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))

REVENUE_BY_COUNTRY = FIXTURES / "valid" / "revenue_by_country.json"


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


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_an_invalid_fixture_is_refused_with_its_own_validator_error(path, client, catalog):
    response = client.post("/api/execute", json={"spec": load(path)})

    assert response.status_code == 400
    expected = EXPECTED_ERROR[path.name]
    errors = response.json()["errors"]
    assert any(expected in error for error in errors), f"expected {expected!r}, got {errors!r}"
    assert catalog.statements == [], "an invalid spec reached the database"


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


def test_a_request_cannot_name_a_source_or_carry_sql(client, catalog):
    """The source is server configuration, so anything else a request holds is not one."""
    response = client.post(
        "/api/execute",
        json={"spec": load(REVENUE_BY_COUNTRY), "source": "postgres://elsewhere/db", "sql": "SELECT 1"},
    )

    assert response.status_code == 200
    assert response.json()["rows"]
    assert "elsewhere" not in "".join(catalog.statements)
