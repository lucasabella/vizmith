"""What the endpoints that spend money may be asked to spend.

Two bounds, and the tests are about the difference between them: a rate is how many may
start in a minute, and a count in flight is how many may be running at once. The number
that matters to both is a dashboard, because opening one is the largest burst anybody makes
on purpose and a limit that refuses it is a limit that gets turned off.
"""

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vizmith.api import app, model, rations, source
from vizmith.ask import ATTEMPTS
from vizmith.dashboards import TILE_LIMIT
from vizmith.model import Completion
from vizmith.rationing import IN_FLIGHT, MODEL, QUERY, Bucket, Exhausted, Rations, ceiling

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
VALID = json.loads((FIXTURES / "valid" / "revenue_by_country.json").read_text())


class Clock:
    """A clock a test moves by hand, because a limiter tested against the real one is a
    test that either sleeps or is flaky."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def forward(self, seconds: float):
        self.now += seconds


def test_a_bucket_allows_its_size_as_a_burst_and_then_refuses():
    """A bucket's size is its burst, which is the property a dashboard needs: every tile
    starts at once and none of them waits."""
    bucket = Bucket(per_minute=4, clock=Clock())

    assert [bucket.take() for _ in range(4)] == [None, None, None, None]
    assert bucket.take() is not None


def test_a_bucket_refills_at_its_rate_and_says_how_long_until_it_has():
    """The wait is what the refusal carries, so it has to be the real one rather than a
    round number: at four a minute, a token arrives every fifteen seconds."""
    clock = Clock()
    bucket = Bucket(per_minute=4, clock=clock)
    for _ in range(4):
        bucket.take()

    assert bucket.take() == pytest.approx(15.0)

    clock.forward(15)
    assert bucket.take() is None


def test_a_bucket_does_not_fill_past_its_size():
    """A server left alone overnight is not a server owed a thousand requests in the
    morning."""
    clock = Clock()
    bucket = Bucket(per_minute=4, clock=clock)
    clock.forward(3600)

    assert [bucket.take() for _ in range(5)] == [None, None, None, None, pytest.approx(15.0)]


def test_a_ceiling_of_zero_turns_a_ration_off(monkeypatch):
    """The escape hatch the eval harness needs. A ration protects a person from a loop
    they did not mean; a harness is a loop somebody meant."""
    monkeypatch.setenv("VIZMITH_QUERY_PER_MINUTE", "0")
    allowance = Rations(Clock())

    for _ in range(500):
        allowance.spend("127.0.0.1", QUERY)


def test_a_ceiling_that_is_not_a_number_is_the_default(monkeypatch):
    """A typo in a knob on a tool somebody runs on their own machine should not stop the
    server starting. It falls back and carries on."""
    monkeypatch.setenv("VIZMITH_MODEL_PER_MINUTE", "twenty")

    assert ceiling("VIZMITH_MODEL_PER_MINUTE", 20) == 20


def test_one_client_cannot_spend_another_s_ration(monkeypatch):
    """There is one caller today. The day there is a second is not the day to find out
    that the first can starve it."""
    monkeypatch.setenv("VIZMITH_MODEL_PER_MINUTE", "2")
    allowance = Rations(Clock())

    allowance.spend("127.0.0.1", MODEL)
    allowance.spend("127.0.0.1", MODEL)
    with pytest.raises(Exhausted):
        allowance.spend("127.0.0.1", MODEL)

    allowance.spend("::1", MODEL)


def test_the_two_classes_are_rationed_apart(monkeypatch):
    """A model call and a warehouse statement cost different amounts and are refused at
    different rates, so a person who has asked twenty questions can still open a chart."""
    monkeypatch.setenv("VIZMITH_MODEL_PER_MINUTE", "1")
    allowance = Rations(Clock())

    allowance.spend("127.0.0.1", MODEL)
    with pytest.raises(Exhausted):
        allowance.spend("127.0.0.1", MODEL)

    allowance.spend("127.0.0.1", QUERY)


def test_the_refusal_says_which_ration_and_when(monkeypatch):
    """A 429 with nothing in it is a 429 somebody debugs for an hour. It names the ceiling,
    the variable that moves it, and a whole number of seconds — never zero, which a client
    reads as 'immediately' and comes straight back on."""
    monkeypatch.setenv("VIZMITH_MODEL_PER_MINUTE", "1")
    allowance = Rations(Clock())
    allowance.spend("127.0.0.1", MODEL)

    with pytest.raises(Exhausted) as refusal:
        allowance.spend("127.0.0.1", MODEL)

    assert "VIZMITH_MODEL_PER_MINUTE" in str(refusal.value)
    assert refusal.value.retry_after >= 1


def test_the_number_in_flight_is_capped_and_given_back():
    """A rate says how many may start in a minute and nothing about how many may be
    running. A hundred statements waiting on a slow warehouse are a hundred being paid
    for."""
    allowance = Rations(Clock())

    for _ in range(IN_FLIGHT):
        allowance.enter()
    with pytest.raises(Exhausted):
        allowance.enter()

    allowance.leave()
    allowance.enter()


def test_a_slot_given_back_twice_does_not_widen_the_cap():
    """A counter that can go negative is a limiter that quietly stops limiting after one
    mismatched pair, which is the failure nobody notices until the bill."""
    allowance = Rations(Clock())
    allowance.leave()
    allowance.leave()

    for _ in range(IN_FLIGHT):
        allowance.enter()
    with pytest.raises(Exhausted):
        allowance.enter()


def test_the_in_flight_cap_is_the_tile_cap():
    """The largest legitimate burst is a dashboard, and the two numbers being the same is
    the reasoning rather than a coincidence."""
    assert IN_FLIGHT == TILE_LIMIT


@pytest.fixture
def client(catalog):
    app.dependency_overrides[source] = lambda: catalog
    yield TestClient(app, base_url="http://127.0.0.1:8000")
    app.dependency_overrides.clear()


def test_a_dashboard_s_worth_of_tiles_is_not_refused(client, monkeypatch):
    """The number that decides both ceilings. Every tile of a full dashboard runs when it
    is opened, so a full dashboard has to fit through in one go or the limiter is one
    somebody switches off on their first afternoon."""
    responses = [client.post("/api/execute", json={"spec": VALID}) for _ in range(TILE_LIMIT)]

    assert [response.status_code for response in responses] == [200] * TILE_LIMIT


def test_past_the_ceiling_the_endpoint_answers_429_in_the_shape_of_a_refusal(client, monkeypatch):
    """Every refusal in this API is an `errors` list, so the interface has one way to show
    one. A 429 with a `detail` would be a second."""
    monkeypatch.setenv("VIZMITH_QUERY_PER_MINUTE", "1")
    rations.cache_clear()

    assert client.post("/api/execute", json={"spec": VALID}).status_code == 200
    refused = client.post("/api/execute", json={"spec": VALID})

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert "VIZMITH_QUERY_PER_MINUTE" in refused.json()["errors"][0]
    # Which part refused, so the interface does not report Vizmith's own ceiling as
    # something a warehouse that was never reached said.
    assert refused.json()["spoke"] == "rations"


def test_a_question_is_rationed_apart_from_a_query(client, monkeypatch):
    """`/api/ask` is up to three billed model calls and then a statement, which is the most
    expensive thing here, so it has the tighter ceiling and spends nothing of the other."""
    monkeypatch.setenv("VIZMITH_MODEL_PER_MINUTE", "1")
    rations.cache_clear()

    class Counting:
        """Answers something the validator refuses, and counts what it was asked."""

        def __init__(self):
            self.asked = 0

        def complete(self, prompt, schema=None):
            self.asked += 1
            return Completion(text="{}", model="counting", finish_reason="stop", usage={})

        def constrains_output(self, schema):
            return False

    writer = Counting()
    app.dependency_overrides[model] = lambda: writer
    try:
        first = client.post("/api/ask", json={"question": "revenue by country"})
        second = client.post("/api/ask", json={"question": "revenue by country"})
    finally:
        app.dependency_overrides.pop(model, None)

    # The first reaches the model and is refused by the validator; the second never gets
    # that far, which is the point: a rationed question spends nothing.
    assert first.status_code == 400
    assert second.status_code == 429
    assert writer.asked == ATTEMPTS, "the refused question made no further billed call"
    assert client.post("/api/execute", json={"spec": VALID}).status_code == 200


def test_the_endpoints_that_cost_nothing_are_not_rationed(client, monkeypatch):
    """A ration is about money. Validating a spec reaches nothing, and a person correcting
    one by hand does it far faster than they ask a question."""
    monkeypatch.setenv("VIZMITH_QUERY_PER_MINUTE", "1")
    monkeypatch.setenv("VIZMITH_MODEL_PER_MINUTE", "1")
    rations.cache_clear()

    statuses = {client.post("/api/validate", json={"spec": VALID}).status_code for _ in range(20)}
    statuses |= {client.get("/api/health").status_code for _ in range(20)}
    statuses |= {client.get("/api/dashboards").status_code for _ in range(20)}

    assert statuses == {200}


def test_a_request_that_failed_still_gives_its_slot_back(client, monkeypatch):
    """The slot is released in a `finally` on the far side of the yield, so a handler that
    raised does not leak one. Twenty-five bad specs in a row would otherwise fill the cap
    permanently and the server would answer 429 to everything until it was restarted."""
    for _ in range(IN_FLIGHT + 5):
        assert client.post("/api/execute", json={"spec": {"not": "a spec"}}).status_code == 400

    assert client.post("/api/execute", json={"spec": VALID}).status_code == 200


def test_requests_in_flight_are_counted_across_threads(client):
    """The counter is shared, so it is locked. Held requests are what a slow warehouse
    produces, and the cap only means anything if concurrent callers see one number."""
    allowance = rations()
    seen = []
    barrier = threading.Barrier(9)

    def take():
        barrier.wait()
        try:
            allowance.enter()
            seen.append(True)
        except Exhausted:
            seen.append(False)

    allowance.flight = 4
    threads = [threading.Thread(target=take) for _ in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sum(seen) == 4, "exactly the cap got in, whatever order they arrived in"
