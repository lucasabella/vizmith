"""Who may ask, once the server answers beyond one local user.

What is pinned down here is the order the refusals arrive in and the wall between two
principals' state. A shared deployment answers an unauthenticated request before it has
built a source or asked a model anything, so the first tests here are about what a
request without a token costs: nothing. The rest are about what a request with a token
can reach, which is its own dashboards, its own relationship answers, and nobody else's.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vizmith import cli
from vizmith.api import app, source
from vizmith.auth import USERS_FILE, Principal, Principals
from vizmith.state import Damaged

A_TOKEN = "a-token"
B_TOKEN = "b-token"
A = {"Authorization": f"Bearer {A_TOKEN}"}
B = {"Authorization": f"Bearer {B_TOKEN}"}

SPECS = Path(__file__).parent / "fixtures" / "specs"


@pytest.fixture(autouse=True)
def shared_mode(monkeypatch):
    """Every test here runs against a server in shared mode, because that is the only
    mode where any of this behaves differently from the default."""
    monkeypatch.setenv("VIZMITH_SHARED_MODE", "1")


@pytest.fixture(autouse=True)
def issued():
    """Two principals with known tokens. Their digests are written straight into the
    users file rather than issued through `Principals`, so the tokens are fixed strings
    a reader can follow instead of ones a test would have to fish out of output."""
    people = Principals()
    for name, token in (("ada", A_TOKEN), ("grace", B_TOKEN)):
        people._users[name] = {"token": hashlib.sha256(token.encode()).hexdigest()}
    from vizmith.state import write

    write(people._path, json.dumps({"users": people._users}, indent=2))


@pytest.fixture
def client(catalog):
    app.dependency_overrides[source] = lambda: catalog
    yield TestClient(app, base_url="http://127.0.0.1:8000")
    app.dependency_overrides.clear()


def tile(name: str) -> dict:
    return {
        "spec": json.loads((SPECS / "valid" / name).read_text()),
        "width": 1,
    }


def test_an_unauthenticated_request_is_refused_before_any_source_work(client):
    """The refusal happens in the middleware, before a dependency builds a source: a
    request without a token must cost the warehouse nothing at all."""
    reached = []
    app.dependency_overrides[source] = lambda: reached.append("built")

    response = client.get("/api/tables")

    assert response.status_code == 401
    assert reached == []
    assert "WWW-Authenticate" in response.headers


def test_an_issued_token_is_answered(client):
    assert client.get("/api/tables", headers=A).status_code == 200


def test_a_token_nobody_issued_is_refused(client):
    assert client.get("/api/tables", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_health_stays_answerable_without_credentials(client):
    """It is what a deployment check runs, it names no data, and the interface reads it
    to learn that it should be asking for a token."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["shared"] is True


def test_the_host_check_still_applies_in_shared_mode(client):
    response = client.get("/api/tables", headers={**A, "Host": "somewhere-else.example"})

    assert response.status_code == 403


def test_an_origin_that_is_not_this_server_is_refused_in_shared_mode(client):
    response = client.get(
        "/api/tables", headers={**A, "Origin": "https://somewhere-else.example"}
    )

    assert response.status_code == 403


def test_one_principal_cannot_read_another_s_dashboards(client):
    saved = client.put("/api/dashboards/sales", json={"tiles": [tile("revenue_by_country.json")]}, headers=A)
    assert saved.status_code == 200

    listed = client.get("/api/dashboards", headers=B)
    assert listed.json()["dashboards"] == []

    read = client.get("/api/dashboards/sales", headers=B)
    assert read.status_code == 404


def test_two_principals_saving_under_one_name_keep_their_own(client):
    revenue = tile("revenue_by_country.json")
    monthly = tile("orders_per_month.json")

    assert client.put("/api/dashboards/sales", json={"tiles": [revenue]}, headers=A).status_code == 200
    assert client.put("/api/dashboards/sales", json={"tiles": [monthly]}, headers=B).status_code == 200

    seen_by_a = client.get("/api/dashboards/sales", headers=A).json()
    seen_by_b = client.get("/api/dashboards/sales", headers=B).json()
    assert seen_by_a != seen_by_b
    assert seen_by_a["tiles"][0]["spec"]["title"] == revenue["spec"]["title"]
    assert seen_by_b["tiles"][0]["spec"]["title"] == monthly["spec"]["title"]


def named(relationship: dict) -> tuple[str, str, str, str]:
    return (
        relationship["left_table"],
        relationship["left_column"],
        relationship["right_table"],
        relationship["right_column"],
    )


def test_one_principal_cannot_see_another_s_relationship_answers(client):
    offered = client.get("/api/relationships", headers=A).json()["relationships"]
    suggested = next(r for r in offered if r["kind"] == "suggested")
    answered = client.post(
        "/api/relationships",
        json={
            "left_table": suggested["left_table"],
            "left_column": suggested["left_column"],
            "right_table": suggested["right_table"],
            "right_column": suggested["right_column"],
            "answer": "confirmed",
        },
        headers=A,
    )
    assert answered.status_code == 200

    def state_for(headers) -> str:
        shown = client.get("/api/relationships", headers=headers).json()["relationships"]
        return next(r["state"] for r in shown if named(r) == named(suggested))

    # The answer is the answerer's: what ada confirmed stays confirmed for ada and is
    # still an open question for grace, who was never asked her own.
    assert state_for(A) == "confirmed"
    assert state_for(B) == "open"


def test_without_shared_mode_no_token_is_asked_for(client, monkeypatch):
    monkeypatch.delenv("VIZMITH_SHARED_MODE")

    assert client.get("/api/tables").status_code == 200


def test_issue_answers_a_working_token():
    people = Principals()

    token = people.issue("ada")

    assert people.verify(token) == Principal("ada")


def test_revoke_takes_the_answer_back():
    people = Principals()
    token = people.issue("ada")

    assert people.revoke("ada") is True

    assert people.verify(token) is None
    assert people.revoke("ada") is False


def test_the_users_file_holds_digests_and_never_a_token(state_dir):
    token = Principals().issue("ada")

    held = (state_dir / USERS_FILE).read_text()
    assert token not in held
    assert "ada" in held


def test_a_name_cannot_name_somewhere_else():
    for name in ("../escape", "..", ".hidden", "", "a/b", "a b"):
        with pytest.raises(ValueError):
            Principals().issue(name)


def test_a_damaged_users_file_is_refused_rather_than_read_as_empty(tmp_path):
    path = tmp_path / USERS_FILE
    path.write_text("{not json")

    with pytest.raises(Damaged):
        Principals(path)


def test_verify_answers_none_where_there_is_no_header():
    assert Principals().verify(None) is None
    assert Principals().verify("") is None


def test_verify_answers_none_on_anything_but_its_own_scheme():
    carried = Principals()

    assert carried.verify("Basic dXNlcjpwYXNz") is None


def test_the_command_issues_lists_and_revokes(monkeypatch, capsys):
    def run(*argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["vizmith", *argv])
        try:
            cli.main()
        except SystemExit as exited:
            return exited.code
        raise AssertionError("the command returned without exiting")

    assert run("user", "add", "ada") == 0
    printed = capsys.readouterr().out
    token = printed.splitlines()[0].removeprefix("issued ada: ")
    assert Principals().verify(token) == Principal("ada")

    assert run("user", "list") == 0
    assert "ada" in capsys.readouterr().out.splitlines()

    assert run("user", "remove", "ada") == 0
    assert Principals().verify(token) is None

    assert run("user", "remove", "ada") == 2


def test_add_warns_where_shared_mode_is_off(monkeypatch, capsys):
    monkeypatch.delenv("VIZMITH_SHARED_MODE")

    def run(*argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["vizmith", *argv])
        try:
            cli.main()
        except SystemExit as exited:
            return exited.code
        raise AssertionError("the command returned without exiting")

    assert run("user", "add", "ada") == 0
    assert "VIZMITH_SHARED_MODE" in capsys.readouterr().err
