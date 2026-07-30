import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_spec_validation import EXPECTED_ERROR

from vizmith.api import app, source
from vizmith.query import build
from vizmith.spec import output_columns

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))

REVENUE_BY_COUNTRY = FIXTURES / "valid" / "revenue_by_country.json"

CONFIGURATION = [
    "VIZMITH_DATABRICKS_PROFILE",
    "VIZMITH_DATABRICKS_CATALOG",
    "VIZMITH_DATABRICKS_SCHEMA",
    "VIZMITH_DATABRICKS_WAREHOUSE",
]


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


def test_a_request_cannot_name_a_source_or_carry_sql(client, catalog):
    """The source is server configuration, so anything else a request holds is not one."""
    response = client.post(
        "/api/execute",
        json={"spec": load(REVENUE_BY_COUNTRY), "source": "postgres://elsewhere/db", "sql": "SELECT 1"},
    )

    assert response.status_code == 200
    assert response.json()["rows"]
    assert "elsewhere" not in "".join(catalog.statements)
