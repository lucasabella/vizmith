"""Take the screenshot the README shows, against the committed fixture data.

A picture of a chart is the one thing a repository about charts has to have, and it is also
the thing that goes stale silently: the interface moves, the image does not, and nobody
notices until somebody arrives and finds a screenshot of a product that no longer looks
like that. So the image is committed, because a README cannot generate one, and this is
committed beside it so that regenerating is a command rather than an afternoon.

Nothing here reaches a warehouse, an endpoint or a network. It is the real server over the
committed Parquet through the same `FixtureCatalog` the offline tests use, and the real
built interface out of `web/dist`. What it draws is a real query against real rows; they
are just invented rows.

    cd web && npm run build && cd ..
    .venv/bin/playwright install chromium
    .venv/bin/python docs/screenshot.py

`VIZMITH_CHROMIUM` names a browser where Playwright's own copy is not the one installed,
the same variable `tests/test_interface.py` reads.
"""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
# The same three the pytest configuration puts on the path, because this borrows the test
# harness's fixture catalog rather than keeping a second copy of one.
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "tests" / "fixtures")]

from conftest import FixtureCatalog, load_fixture_db

from vizmith.api import CONFIGURATION, MODEL_CONFIGURATION, WEB_DIST, app, source

# Four joined tables, a stacked bar and a colour channel, which is the most the chart
# grammar says in one picture. A screenshot of a single bar chart would be honest and would
# undersell what a spec can describe.
SPEC = ROOT / "tests" / "fixtures" / "specs" / "valid" / "revenue_by_category_stacked.json"
INTO = ROOT / "docs" / "vizmith.png"

# Wide enough that the Fields panel and the chart are both readable, and a 16:10 that a
# README scales down without the axis labels turning to mush.
VIEWPORT = {"width": 1440, "height": 900}
DRAWN = 30_000


def main() -> int:
    if not WEB_DIST.is_dir():
        print(f"no interface at {WEB_DIST}. Run npm run build in web/ first.", file=sys.stderr)
        return 1

    # The interface reads /api/health to decide whether the controls are live, and that
    # reports what is configured rather than what is injected. Without these the screenshot
    # would be of the Setup screen. The source below is still the fixture one.
    for name in (*CONFIGURATION, *MODEL_CONFIGURATION):
        os.environ.setdefault(name, "fixture")

    connection = load_fixture_db()
    app.dependency_overrides[source] = lambda: FixtureCatalog(connection)

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
        print("the server did not start", file=sys.stderr)
        return 1

    try:
        _shoot(f"http://127.0.0.1:{port}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        app.dependency_overrides.clear()
        connection.close()

    print(f"wrote {INTO.relative_to(ROOT)}")
    return 0


def _shoot(url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("VIZMITH_CHROMIUM"))
        # Fixed rather than the machine's, so the image is the same size whoever regenerates
        # it and a re-run is a diff of the interface rather than of somebody's monitor.
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        page.goto(url, wait_until="networkidle")

        editor = page.get_by_role("button", name="{ } JSON")
        if editor.get_attribute("aria-pressed") != "true":
            editor.click()
        page.locator("textarea.spec__text").fill(json.dumps(json.loads(SPEC.read_text())))
        page.get_by_role("button", name="Run spec").click()
        page.wait_for_selector(".chart canvas, .figure", timeout=DRAWN)

        # The JSON panel is how the spec got here and not what the picture is about, so it
        # is closed again before the shot. The chart, the wells and the fields are.
        editor.click()
        # ECharts draws onto a canvas on its own frame, and networkidle says nothing about
        # a paint. Short, and a settle rather than a wait for anything in particular.
        page.wait_for_timeout(1200)

        INTO.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(INTO))
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
