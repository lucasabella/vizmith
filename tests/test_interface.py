"""The interface, driven in a browser against the committed fixture data.

Every other frontend test is a static render: it reaches the first frame and cannot press
anything. What that misses is the class of failure that only exists between interactions,
and #59 shipped one — the dashboard being arranged lived inside the view that drew it, so
going back to the Chart view to build the second chart threw the first tile away. Every
test passed. This suite is what would have caught it.

It is the real server over the real fixture database, with the source dependency replaced
by the same `FixtureCatalog` the offline tests use, so nothing here needs a warehouse, a
network or a credential. The frontend is the built one: the backend serves `web/dist` when
it exists, which means `npm run build` has to have run, and this skips rather than failing
when it has not.

Few tests, and each one a flow rather than a control. A browser suite that mirrors the unit
tests is slow twice over and gets deleted the first time it goes red for a reason nobody
caused. What belongs here is what crosses a view, a reload or a repaint.

`VIZMITH_CHROMIUM` names the browser to drive where Playwright's own copy is not the one
installed. Without it the default is used, and where there is no browser at all the suite
skips.
"""

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from vizmith.api import CONFIGURATION, WEB_DIST, app, source

pytest.importorskip("playwright", reason="pip install playwright to drive the interface")

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Page, sync_playwright

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
REVENUE_BY_COUNTRY = FIXTURES / "valid" / "revenue_by_country.json"
ORDERS_PER_MONTH = FIXTURES / "valid" / "orders_per_month.json"
MISSING_LIMIT = FIXTURES / "invalid" / "missing_limit.json"

# A chart is drawn onto a canvas and a first paint is not instant. Long enough for a query
# against the fixture database and a render, short enough that a hang is a failure rather
# than a wait.
DRAWN = 30_000

needs_built_frontend = pytest.mark.skipif(
    not WEB_DIST.is_dir(),
    reason="run npm run build in web/ so the server has an interface to serve",
)


def spec(path: Path) -> str:
    return json.dumps(json.loads(path.read_text()))


@pytest.fixture(scope="module")
def browser():
    """One browser for the suite. Launching one is the expensive part, and a page per test
    is what keeps the tests independent."""
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(
                executable_path=os.environ.get("VIZMITH_CHROMIUM") or None
            )
        except BrowserError as failure:
            pytest.skip(f"no browser to drive: {failure}")
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(fixture_db):
    """The application, on a port of the operating system's choosing, over the fixture
    data. A fixed port would collide with whatever else is listening on a developer's
    machine, and the port is only ever read back from the socket."""
    from conftest import FixtureCatalog

    app.dependency_overrides[source] = lambda: FixtureCatalog(fixture_db)
    # `/api/health` reports what is configured rather than what is injected, and the
    # interface reads it: without this the controls are disabled and every flow below
    # would be testing the Setup screen. The source itself is still the override above.
    patch = pytest.MonkeyPatch()
    for name in CONFIGURATION:
        patch.setenv(name, "fixture")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("the server did not start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)
    app.dependency_overrides.clear()
    patch.undo()


@pytest.fixture
def page(browser, served, tmp_path, monkeypatch):
    """A page on the served application. The state directory is per test, so a dashboard
    one test saved is not one the next test finds."""
    monkeypatch.setenv("VIZMITH_STATE_DIR", str(tmp_path / "state"))
    opened = browser.new_page(viewport={"width": 1500, "height": 950})
    failures: list[str] = []
    opened.on("pageerror", lambda error: failures.append(str(error)))
    opened.goto(served, wait_until="networkidle")
    yield opened
    opened.close()
    # An exception in the browser is a failure even where the assertions passed: React
    # keeps drawing the last good tree, so a component that threw can look like one that
    # worked.
    assert failures == []


def run_spec(page: Page, path: Path) -> None:
    """Paste a spec and run it, which is the one way into a chart that needs no model and
    no dragging. `{ } JSON` is a toggle, so it is opened rather than clicked."""
    editor = page.get_by_role("button", name="{ } JSON")
    if editor.get_attribute("aria-pressed") != "true":
        editor.click()
    page.locator("textarea.spec__text").fill(spec(path))
    page.get_by_role("button", name="Run spec").click()


def chart_drawn(page: Page) -> None:
    page.wait_for_selector(".chart canvas, .figure", timeout=DRAWN)


@needs_built_frontend
def test_a_pasted_spec_draws_a_chart(page):
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)

    assert page.locator(".strip__badge--good").is_visible()
    assert "10 rows" in page.locator(".pages__meta").inner_text()


@needs_built_frontend
def test_a_spec_the_validator_rejects_says_so_and_draws_nothing(page):
    run_spec(page, MISSING_LIMIT)
    page.wait_for_selector(".refusal", timeout=DRAWN)

    # Upper cased, because the stylesheet does that to a refusal heading and inner_text
    # reports what is on screen rather than what is in the markup.
    assert "WHAT THE VALIDATOR SAID" in page.locator(".refusal__head").first.inner_text()
    assert "limit" in page.locator(".refusal__lines").inner_text()
    assert page.locator(".chart canvas").count() == 0


@needs_built_frontend
def test_the_table_view_shows_the_rows_the_chart_was_drawn_from(page):
    """The three low contrast series colours are legal because every row is readable
    somewhere. This is that somewhere."""
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.get_by_role("button", name="Table").click()

    assert "Netherlands" in page.locator("table").inner_text()


@needs_built_frontend
def test_a_dashboard_survives_leaving_the_view_and_a_reload(page):
    """The flow #59 was built for, and the one its first version broke: a tile is added,
    the next chart is built in another view, and coming back has to find the first tile
    still there. Then the arrangement, the save, a reload, and opening it again."""
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Add the chart on screen").click()
    page.wait_for_selector(".grid__cell canvas", timeout=DRAWN)

    page.get_by_role("button", name="Chart", exact=True).first.click()
    run_spec(page, ORDERS_PER_MONTH)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()

    # The tile built before the detour. A view that owned the arrangement lost this.
    assert page.locator(".grid__cell").count() == 1

    page.get_by_role("button", name="Add the chart on screen").click()
    page.wait_for_selector(".grid__cell:nth-child(2) canvas", timeout=DRAWN)
    page.locator(".grid__cell").first.get_by_title("Half width or full width").click()
    page.locator(".grid__cell").first.get_by_title("Move later").click()

    page.get_by_label("Dashboard name").fill("Trade, 2026")
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector("text=Saved as Trade, 2026", timeout=DRAWN)

    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Trade, 2026").click()
    page.wait_for_selector(".grid__cell canvas", timeout=DRAWN)

    titles = page.locator(".grid__title").all_inner_texts()
    widths = page.eval_on_selector_all(
        ".grid__cell", "cells => cells.map(cell => getComputedStyle(cell).gridColumn)"
    )
    assert titles == ["Orders per month", "Revenue by country, 2025"]
    assert widths == ["span 1", "span 2"]


@needs_built_frontend
def test_a_dashboard_that_is_deleted_is_gone_from_the_list(page):
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Add the chart on screen").click()
    page.get_by_label("Dashboard name").fill("Trade, 2026")
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector("text=Saved as Trade, 2026", timeout=DRAWN)

    page.get_by_role("button", name="Delete").click()
    page.wait_for_selector("text=Deleted Trade, 2026", timeout=DRAWN)

    assert page.locator(".dash__list").count() == 0
    assert "Nothing is saved yet" in page.locator(".dash__saved").inner_text()


@needs_built_frontend
def test_a_tile_is_corrected_where_it_was_made_and_goes_back_where_it_came_from(page):
    """A tile opens into the Chart view, the correction is made there, and it lands in the
    tile it came from rather than as a new one at the end."""
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Add the chart on screen").click()
    page.get_by_role("button", name="Chart", exact=True).first.click()
    run_spec(page, ORDERS_PER_MONTH)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Add the chart on screen").click()
    page.wait_for_selector(".grid__cell:nth-child(2) canvas", timeout=DRAWN)

    page.locator(".grid__cell").first.get_by_title("Correct this chart").click()
    chart_drawn(page)
    corrected = json.loads(spec(REVENUE_BY_COUNTRY)) | {"title": "Revenue, corrected"}
    page.locator("textarea.spec__text").fill(json.dumps(corrected))
    page.get_by_role("button", name="Run spec").click()
    chart_drawn(page)
    page.get_by_role("button", name="Put it back").click()
    page.wait_for_selector(".grid__cell canvas", timeout=DRAWN)

    # In place, not appended, and the second tile is untouched.
    assert page.locator(".grid__title").all_inner_texts() == [
        "Revenue, corrected",
        "Orders per month",
    ]


@needs_built_frontend
def test_a_correction_can_be_abandoned(page):
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Add the chart on screen").click()
    page.wait_for_selector(".grid__cell canvas", timeout=DRAWN)

    page.locator(".grid__cell").first.get_by_title("Correct this chart").click()
    chart_drawn(page)
    page.locator("textarea.spec__text").fill(spec(ORDERS_PER_MONTH))
    page.get_by_role("button", name="Never mind").click()
    page.get_by_role("button", name="Dashboards").click()

    assert page.locator(".grid__title").all_inner_texts() == ["Revenue by country, 2025"]
    assert page.locator(".grid__cell--editing").count() == 0


@needs_built_frontend
def test_a_dashboard_is_renamed_in_one_gesture(page):
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Add the chart on screen").click()
    page.get_by_label("Dashboard name").fill("Trade")
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector("text=Saved as Trade", timeout=DRAWN)

    page.get_by_label("Dashboard name").fill("Trade, 2026")
    page.get_by_role("button", name="Rename Trade").click()
    page.wait_for_selector("text=Renamed Trade to Trade, 2026", timeout=DRAWN)
    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Dashboards").click()

    names = page.locator(".dash__name").all_inner_texts()
    assert names == ["Trade, 2026"]


@needs_built_frontend
def test_arranging_a_dashboard_runs_no_query(page):
    """The bug this exists for: tiles keyed by position meant swapping two of them handed
    each component the other's spec, and both ran their query again. Arranging a dashboard
    was more expensive than opening it, for a gesture that changed no data."""
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Add the chart on screen").click()
    page.get_by_role("button", name="Chart", exact=True).first.click()
    run_spec(page, ORDERS_PER_MONTH)
    chart_drawn(page)
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Add the chart on screen").click()
    page.wait_for_selector(".grid__cell:nth-child(2) canvas", timeout=DRAWN)

    ran: list[str] = []
    page.on("request", lambda request: ran.append(request.url) if "/api/execute" in request.url else None)

    page.locator(".grid__cell").first.get_by_title("Move later").click()
    page.locator(".grid__cell").first.get_by_title("Half width or full width").click()
    page.locator(".grid__cell").last.get_by_title("Remove").click()
    # The order changed on screen, so the moves landed, and the canvas that was already
    # drawn is still the one drawn: nothing was thrown away and re-run.
    page.wait_for_timeout(500)

    assert page.locator(".grid__title").all_inner_texts() == ["Orders per month"]
    assert ran == []


@needs_built_frontend
def test_a_column_row_opens_from_the_keyboard_the_way_it_says_it_does(page):
    """The row carries role="button", and a button answers Space as well as Enter. The
    difference is invisible until somebody uses the keyboard, which is why it is asserted
    here rather than in a static render."""
    page.locator(".tree__row--table", has_text="customers").click()
    row = page.locator(".tree__row--column").first
    row.focus()

    row.press(" ")
    page.wait_for_selector(".tree__row--column[aria-expanded=true]", timeout=DRAWN)
    assert page.locator(".profile").count() == 1

    row.press(" ")
    page.wait_for_selector(".tree__row--column[aria-expanded=false]", timeout=DRAWN)
    assert page.locator(".profile").count() == 0

    row.press("Enter")
    page.wait_for_selector(".tree__row--column[aria-expanded=true]", timeout=DRAWN)

    # Space on a control that says it is a button opens it rather than scrolling the page.
    assert page.evaluate("window.scrollY") == 0


@needs_built_frontend
def test_the_spec_editor_has_a_name_that_survives_being_typed_in(page):
    """A placeholder is not a name: it is gone the moment there is text in the field."""
    page.get_by_role("button", name="{ } JSON").click()
    editor = page.get_by_label("Chart specification, as JSON")
    editor.fill(spec(REVENUE_BY_COUNTRY))

    assert editor.input_value() != ""
