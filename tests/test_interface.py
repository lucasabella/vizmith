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

from vizmith.api import MODEL_CONFIGURATION, WEB_DIST, app, constrains, model, source
from vizmith.config import source_settings
from vizmith.model import Completion

pytest.importorskip("playwright", reason="pip install playwright to drive the interface")

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Page, sync_playwright

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
REVENUE_BY_COUNTRY = FIXTURES / "valid" / "revenue_by_country.json"
ORDERS_PER_MONTH = FIXTURES / "valid" / "orders_per_month.json"
MISSING_LIMIT = FIXTURES / "invalid" / "missing_limit.json"
RETURNS_BY_REASON = FIXTURES / "valid" / "returns_share_by_reason.json"

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

    # One catalog for the server, which is what `source()` in `api.py` is: an lru_cache, so
    # a running Vizmith holds one connector for the process. A factory here built a fresh
    # one per request, which is not what the interface is being driven against.
    catalog = FixtureCatalog(fixture_db)
    app.dependency_overrides[source] = lambda: catalog
    # A model that answers without a network, so the flows that need one are driven here
    # rather than skipped. It always answers the same spec: what these tests are about is
    # what the interface does with an answer, not which answer a model gives.
    app.dependency_overrides[model] = lambda: Answering(json.loads(RETURNS_BY_REASON.read_text()))
    # `/api/health` reports what is configured rather than what is injected, and the
    # interface reads it: without this the controls are disabled and every flow below
    # would be testing the Setup screen. The source and the model are still the overrides
    # above, so nothing here reaches a warehouse or an endpoint.
    patch = pytest.MonkeyPatch()
    for name in (*source_settings(), *MODEL_CONFIGURATION):
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
    constrains.cache_clear()
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


class Answering:
    """A model that answers with one spec, however it is asked. The adapter is covered by
    its own tests and a network is what this suite exists without, so what is behind the
    endpoint here is a constant."""

    def __init__(self, answer: dict):
        self._answer = json.dumps(answer)

    def complete(self, prompt: str, schema: dict | None = None) -> Completion:
        return Completion(text=self._answer, model="scripted", finish_reason="stop", usage={})

    def constrains_output(self, schema: dict) -> bool:
        return False


def run_spec(page: Page, path: Path) -> None:
    """Paste a spec and run it, which is the one way into a chart that needs no model and
    no dragging."""
    paste_spec(page, spec(path))


def paste_spec(page: Page, body: str) -> None:
    """`{ } JSON` is a toggle, so it is opened rather than clicked."""
    editor = page.get_by_role("button", name="{ } JSON")
    if editor.get_attribute("aria-pressed") != "true":
        editor.click()
    page.locator("textarea.spec__text").fill(body)
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

    # #166: a tile drew "No rows to draw" once, for a spec that returns thirty, and nobody
    # could reproduce it. This is the assertion rather than the hunt — the flow it was seen
    # in is this one, and both specs return rows, so an empty tile here is either that race
    # or a real regression.
    #
    # Waited on rather than sampled, and waited on the right thing: the selector above
    # matches as soon as one tile has drawn, and "no tile is still running" matches before
    # the renderer has made its canvas, because that happens in an effect after the paint.
    # What every tile has to reach is one of the three things it can end at.
    page.wait_for_function(
        """() => {
            const bodies = [...document.querySelectorAll('.grid__cell .grid__body')];
            return (
              bodies.length === 2 &&
              bodies.every((body) => body.querySelector('canvas, .figure, .empty, .grid__refusal'))
            );
        }""",
        timeout=DRAWN,
    )
    # What each tile settled on, so a failure says which one and on what rather than only
    # that a number was wrong. This is the report #166 did not have.
    settled = page.eval_on_selector_all(
        ".grid__cell",
        "cells => cells.map(cell => cell.querySelector('.grid__title').innerText + ': ' +"
        " (cell.querySelector('.grid__body').textContent || 'a canvas'))",
    )
    assert page.locator(".grid__cell canvas").count() == 2, settled
    assert page.locator(".grid__cell .empty").count() == 0, settled


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
def test_one_filter_narrows_every_tile_it_reaches_and_says_which_it_did_not(page):
    """#117, end to end: the control, the two answers it gives, and the round trip.

    It belongs in a browser rather than in jsdom because what is being asserted crosses a
    reload — a filter that has to be retyped after one is a control nobody keeps — and
    because the two tiles have to actually run against the source under it. One reads
    customers through a join and is narrowed; the other reads only orders, and is left
    exactly as it was with a sentence saying so. A join invented to make it reach would
    draw a plausible number, which is the failure this whole design exists to prevent.
    """
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

    page.get_by_label("Column to filter every tile by").select_option("customers.country")
    page.get_by_label("Value").fill("Netherlands")
    page.get_by_role("button", name="Add", exact=True).click()

    page.wait_for_selector(".grid__unnarrowed", timeout=DRAWN)
    assert page.locator(".grid__unnarrowed").count() == 1, "only the tile that cannot reach it"
    assert "Not narrowed by country = Netherlands" in page.locator(".grid__unnarrowed").inner_text()
    assert "1 tile" in page.locator(".across__chip").inner_text(), "and the chip says so too"

    page.get_by_label("Dashboard name").fill("Trade, 2026")
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector("text=Saved as Trade, 2026", timeout=DRAWN)

    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Trade, 2026").click()
    page.wait_for_selector(".grid__cell canvas", timeout=DRAWN)

    assert "country = Netherlands" in page.locator(".across__chip").inner_text()
    assert page.locator(".grid__unnarrowed").count() == 1


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
def test_a_chart_is_built_from_the_keyboard_alone(page):
    """The interaction the README puts second, reached without a mouse.

    Dragging a column into a well was HTML5 drag and drop and nothing else, so somebody
    driving this from a keyboard could read the Fields panel, reach every control around it
    and not build a chart at all. WCAG 2.1.1, level A. Driven with `press` throughout rather
    than with `click`, because a click on the same buttons proves nothing this file did not
    already prove.
    """
    page.locator(".tree__row--table", has_text="orders").click()
    take = page.get_by_role("button", name="Pick up total")
    take.focus()
    take.press("Enter")

    # Held, and said where a reader hears it rather than only where an eye sees it.
    assert page.get_by_role("button", name="Put down total").count() == 1
    assert "Holding total" in page.locator(".wells [role=status]").inner_text()

    place = page.get_by_role("button", name="Place total in Values")
    place.focus()
    place.press("Enter")

    chart_drawn(page)
    assert page.get_by_role("button", name="Pick up total").count() == 1


@needs_built_frontend
def test_a_field_picked_up_can_be_put_down_again(page):
    """A picked up field is a mode, and a mode with no way out is a trap. Two ways out, on
    purpose: the control that picked it up, and Escape from wherever the person got to."""
    page.locator(".tree__row--table", has_text="orders").click()
    held = page.locator(".wells [role=status]")

    take = page.get_by_role("button", name="Pick up status")
    take.focus()
    take.press("Enter")
    assert "Holding status" in held.inner_text()

    page.get_by_role("button", name="Put down status").press("Enter")
    assert held.inner_text().strip() == ""

    page.get_by_role("button", name="Pick up status").press("Enter")
    page.get_by_role("button", name="Place status in Axis").press("Escape")
    assert held.inner_text().strip() == ""


@needs_built_frontend
def test_the_spec_editor_has_a_name_that_survives_being_typed_in(page):
    """A placeholder is not a name: it is gone the moment there is text in the field."""
    page.get_by_role("button", name="{ } JSON").click()
    editor = page.get_by_label("Chart specification, as JSON")
    editor.fill(spec(REVENUE_BY_COUNTRY))

    assert editor.input_value() != ""


STACKED = FIXTURES / "valid" / "revenue_by_category_stacked.json"
TOTAL_REVENUE = FIXTURES / "valid" / "total_revenue.json"

# ECharts stamps the element it initialised into, and the stamp is per instance. A second
# stamp across one flow means the chart was disposed and built again.
INSTANCE = "() => document.querySelector('.chart')?.getAttribute('_echarts_instance_')"

# Whether the second series colour is anywhere on the canvas. `SERIES[1]` is #eb6834, which
# a stacked chart wears and a single series chart never does.
SECOND_COLOUR = """() => {
  const canvas = document.querySelector('.chart canvas');
  const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
  for (let at = 0; at < pixels.length; at += 4) {
    if (Math.abs(pixels[at] - 235) < 12 && Math.abs(pixels[at + 1] - 104) < 12 &&
        Math.abs(pixels[at + 2] - 52) < 12) return true;
  }
  return false;
}"""


def click_a_mark(page) -> None:
    """Click until a click lands on a mark. A bar's height is the data's business, so where
    one is depends on the spec, and the drill panel is what says a mark was hit."""
    box = page.locator(".chart canvas").bounding_box()
    for across in (0.2, 0.12, 0.3, 0.45):
        for down in (0.3, 0.5, 0.7, 0.85, 0.93):
            page.mouse.click(box["x"] + box["width"] * across, box["y"] + box["height"] * down)
            page.wait_for_timeout(250)
            if page.locator(".drill").count() > 0:
                return
    raise AssertionError("no click landed on a mark")


def drill_into(page, column: str) -> None:
    """Click a mark and ask the same question per another column, which is the one flow that
    replaces what a mounted chart draws without the canvas leaving the screen."""
    click_a_mark(page)
    page.wait_for_selector(".drill__list", timeout=DRAWN)
    page.locator(".drill__pick", has_text=column).first.click()


@needs_built_frontend
def test_a_new_result_set_draws_into_the_chart_that_is_already_there(page):
    """Initialising per option threw the canvas, the renderer, the click handler and the
    resize observer away for what is a data change. Going back to the chart a drill came
    from is that change with the canvas never leaving the screen, so it is where the
    instance either survives or does not."""
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.wait_for_timeout(700)
    drill_into(page, "status")
    chart_drawn(page)
    page.wait_for_timeout(700)
    drilled = page.evaluate(INSTANCE)

    page.get_by_role("button", name="the chart this came from").click()
    page.wait_for_timeout(700)

    assert drilled is not None
    assert page.evaluate(INSTANCE) == drilled, "the chart was disposed and built again"
    assert page.locator(".chart canvas").count() == 1


@needs_built_frontend
def test_a_chart_that_loses_a_series_loses_what_that_series_drew(page):
    """The option is applied without merging, because what the renderer builds is complete
    every time and a merge keeps what the previous option had. What this asserts is the
    visible half of that: after a stacked chart is replaced by a single series one, no
    colour the stack wore is left on the canvas."""
    run_spec(page, STACKED)
    chart_drawn(page)
    page.wait_for_timeout(900)
    assert page.evaluate(SECOND_COLOUR), "a stacked chart wears more than one colour"

    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.wait_for_timeout(900)

    assert not page.evaluate(SECOND_COLOUR), "a series from the previous spec is still drawn"


@needs_built_frontend
def test_going_to_a_single_figure_leaves_no_chart_behind_it(page):
    """A question with no dimension draws a figure rather than a chart, so the instance
    that drew the chart before it goes with the canvas it drew on."""
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)

    run_spec(page, TOTAL_REVENUE)
    page.wait_for_selector(".figure", timeout=DRAWN)

    assert page.locator(".chart canvas").count() == 0, "a chart stayed behind the figure"
    assert page.locator(".figure__value").inner_text() != ""
    # The one result shape that ever shows a count of one, and the one a person reads most
    # closely because there is nothing else on the screen to read. It said "1 rows".
    assert page.locator(".pages__meta").inner_text() == "1 row"


@needs_built_frontend
def test_a_click_reads_the_chart_that_is_on_screen_rather_than_the_one_before_it(page):
    """The handler is registered once now, so what it reads has to be the spec and the rows
    that are drawn rather than the ones the instance was mounted with. Going back to the
    chart a drill came from swaps both under a chart that never left the screen, so a click
    after it is what tells a live handler from a stale one."""
    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    page.wait_for_timeout(700)
    drill_into(page, "status")
    chart_drawn(page)
    page.wait_for_timeout(700)

    page.get_by_role("button", name="the chart this came from").click()
    page.wait_for_timeout(700)
    click_a_mark(page)

    # A country, which is what the restored chart groups by. The drilled chart it was
    # mounted with grouped by status, and its categories are words like "delivered".
    asked = page.locator(".drill__head").inner_text()
    assert asked.startswith("Ask about")
    assert "Netherlands" in asked, asked


@needs_built_frontend
def test_a_suggestion_changes_nothing_until_it_is_taken(page):
    """The whole of what a critique is allowed to do, driven. A rule refuses the mark, the
    finding says so in the rule's own words, Never mind leaves the spec exactly as it was,
    and taking it replaces the chart while leaving the one it replaced a control away."""
    drawn = json.loads(spec(RETURNS_BY_REASON))
    drawn["chart"]["mark"] = "line"
    paste_spec(page, json.dumps(drawn))
    chart_drawn(page)

    page.get_by_role("button", name="Suggest an improvement").click()
    page.wait_for_selector(".pages__note--said", timeout=DRAWN)
    said = page.locator(".pages__note--said").inner_text()

    assert "gaps between its values" in said
    assert json.loads(page.locator("textarea.spec__text").input_value()) == drawn

    page.get_by_role("button", name="Never mind").click()
    assert page.locator(".pages__note--said").count() == 0
    assert json.loads(page.locator("textarea.spec__text").input_value()) == drawn
    assert page.locator(".pages__back").count() == 0, "nothing was replaced, so there is no way back"

    page.get_by_role("button", name="Suggest an improvement").click()
    page.wait_for_selector(".pages__note--said", timeout=DRAWN)
    page.get_by_role("button", name="Use it").click()
    chart_drawn(page)

    assert json.loads(page.locator("textarea.spec__text").input_value())["chart"]["mark"] == "arc"
    assert page.locator(".pages__back").is_visible(), "the chart it replaced is one control away"

    page.locator(".pages__back").click()
    assert json.loads(page.locator("textarea.spec__text").input_value()) == drawn


@needs_built_frontend
def test_a_chart_no_rule_refuses_is_told_so_rather_than_improved(page):
    """The common answer, and the line the whole feature sits on: what a critique may say
    is what is refusable, so a chart nothing refuses gets nothing said about it."""
    run_spec(page, RETURNS_BY_REASON)
    chart_drawn(page)

    page.get_by_role("button", name="Suggest an improvement").click()
    page.wait_for_selector(".pages__note--said", timeout=DRAWN)

    assert "Nothing to suggest" in page.locator(".pages__note--said").inner_text()
    assert page.get_by_role("button", name="Use it").count() == 0


@needs_built_frontend
def test_json_that_is_not_a_spec_leaves_the_interface_standing(page):
    """`{ } JSON` is one of the five ways a spec gets made, and the one that says "adjust
    the spec by hand". Editing JSON by hand means passing through states that are not a
    spec, and `{"a":1}` is one of them: it parses, it is an object, and it has no `chart`.

    Every panel read `draft.chart.encoding` without asking, so it threw during render — and
    a throw during render unmounts the tree in React 19, which took the whole application
    down and lost the editor's contents with it. Both ways out of the panel are driven,
    because both of them used to end in a blank page."""
    paste_spec(page, '{"a":1}')
    page.wait_for_selector(".refusal", timeout=DRAWN)

    page.get_by_role("button", name="{ } JSON").click()
    assert page.locator(".wells").is_visible(), "the wells went with the tab"
    assert "Drag a column from Fields" in page.locator(".wells__note").inner_text()

    page.get_by_role("button", name="Dashboards").click()
    assert page.locator(".dash__title").is_visible(), "the Dashboards view went with the tab"
    # Nothing to add, because there is no spec on screen. That is the state this view has
    # always drawn for an empty editor, and reaching it is the whole point: the value used
    # to be handed over as a draft and read for a `chart` it does not have.
    assert page.get_by_role("button", name="Add the chart on screen").is_disabled()

    page.get_by_role("button", name="Chart", exact=True).first.click()
    page.get_by_role("button", name="{ } JSON").click()
    assert page.locator("textarea.spec__text").input_value() == '{"a":1}', "the editor was lost"


@needs_built_frontend
def test_a_line_deleted_out_of_a_spec_leaves_the_interface_standing(page):
    """The same failure, at the shape it is actually likely to arrive in. `{"a":1}` is the
    example the report started from; what somebody editing by hand really does is delete a
    line out of a spec that works, and a filter with no `column` still parses, still has a
    chart, and still throws on `filter.column.split`.

    Driven in the wells rather than in the editor, because the wells are what read those
    fields: the panel is switched to after the paste, which is the gesture that used to end
    in a blank page."""
    hand_edited = json.loads(spec(REVENUE_BY_COUNTRY))
    del hand_edited["query"]["filters"][0]["column"]

    paste_spec(page, json.dumps(hand_edited))
    page.wait_for_selector(".refusal", timeout=DRAWN)
    page.get_by_role("button", name="{ } JSON").click()

    assert page.locator(".wells").is_visible(), "the wells went with the tab"
    assert "Drag a column from Fields" in page.locator(".wells__note").inner_text()


@needs_built_frontend
def test_what_the_canvas_shows_is_announced_rather_than_only_drawn(page):
    """WCAG 4.1.3. Every message this interface produces used to appear by being swapped
    into the tree, which a screen reader does not report — including the wait, which is the
    only signal during a first question, and the refusal, which is the answer.

    One region, polite, saying what the canvas now shows rather than narrating the way
    there. Driven here because a static render reaches the first frame and this is about
    what the frame after it says."""
    said = page.locator("[role='status'][aria-live='polite']").first

    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)
    assert said.inner_text() == "A chart of 10 rows."

    run_spec(page, MISSING_LIMIT)
    page.wait_for_selector(".refusal", timeout=DRAWN)
    announced = said.inner_text()
    assert announced.startswith("What the validator said:"), announced
    assert "limit" in announced
    # The heading and the first line, not the list. What is on screen is the whole of it.
    assert announced.count("\n") == 0


@needs_built_frontend
def test_the_rail_says_which_view_is_on_screen_as_navigation(page):
    """Three buttons that change which view is drawn are navigation rather than toggles,
    and `aria-pressed` said they were three independent switches."""
    page.get_by_role("button", name="Dashboards").click()

    assert page.get_by_role("button", name="Dashboards").get_attribute("aria-current") == "page"
    assert page.get_by_role("button", name="Chart", exact=True).first.get_attribute("aria-current") is None


@needs_built_frontend
def test_the_fields_panel_fills_before_anything_has_been_profiled(page):
    """#122. `/api/tables` answers only once every table has been profiled, so a schema
    nobody has profiled put its whole cold read — modelled at 25 seconds and 456 billed
    statements over 152 tables — in front of the first table name on screen.

    The panel is drawn from the shape first, which costs no statement, and the profiles fill
    in the figures behind it. Both halves are driven here because both are the point: the
    tree is usable early, and it does not claim a figure it has not read."""
    page.wait_for_selector(".tree__table", timeout=DRAWN)
    names = page.locator(".tree__table .tree__name").all_inner_texts()

    assert "orders" in names
    assert len(names) == 8, names

    # And the profiles land behind it, which is what a row count is.
    page.wait_for_function(
        "document.querySelectorAll('.tree__count').length > 0 &&"
        " [...document.querySelectorAll('.tree__count')].every(c => c.innerText.includes('rows'))",
        timeout=DRAWN,
    )
    counts = page.locator(".tree__count").all_inner_texts()
    assert all("rows" in count for count in counts), counts
    assert not any(count.startswith("0 rows") for count in counts), counts


@needs_built_frontend
def test_the_shape_request_costs_the_source_no_statement(page):
    """The half of #122 that is a bill rather than a wait. A person who opens the interface
    and closes it again should have paid for nothing: the tree is metadata, and the profiles
    are what cost, so the shape has to reach the source without a statement.

    Asserted against the API rather than the DOM, because what is being tested is what the
    server was asked for."""
    answered = []
    page.on("response", lambda response: answered.append(response.url))
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".tree__table", timeout=DRAWN)

    assert any(url.endswith("/api/shape") for url in answered), answered
    assert any(url.endswith("/api/tables") for url in answered), answered
    # One request for the schema's shape and one for its profiles, not a request per table.
    assert len([url for url in answered if "/api/tables/" in url]) == 0, answered


@needs_built_frontend
def test_the_renderer_is_not_fetched_until_something_needs_a_chart(page):
    """#104. ECharts is 70% of what this interface ships and nothing on the first paint
    draws a chart: the shell, the Fields panel and the empty state need none of it, and
    somebody who opens the app to paste a spec has not drawn one yet either.

    Watched at the network rather than measured, because what the issue is about is when the
    chunk arrives and not how long anything took. The Fields panel filling is the first
    paint being usable, which is the moment the renderer must still be absent."""
    fetched: list[str] = []
    page.on("request", lambda request: fetched.append(request.url))
    page.goto(page.url, wait_until="networkidle")
    page.wait_for_selector(".tree__table", timeout=DRAWN)

    def renderer() -> list[str]:
        return [url for url in fetched if "/assets/Chart-" in url]

    assert renderer() == [], "the renderer arrived before anything asked for a chart"

    run_spec(page, REVENUE_BY_COUNTRY)
    chart_drawn(page)

    assert renderer(), "a chart was drawn without the renderer being fetched"
