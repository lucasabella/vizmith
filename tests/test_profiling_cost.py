"""What profiling a schema costs, measured against a real warehouse.

`PROFILE_WORKERS` is the width a first question profiles a schema at, and until this
existed the number had reasoning behind it and no measurement. This is the harness that
produces one: wall clock for a cold profile and for a warm one, at several widths, with the
three statements a table costs broken out so it is clear which of them dominates each case.

It needs a warehouse and skips without one, so nothing here has to pass in CI. Run it with
the source configured and `-s`, which is what prints the table:

    VIZMITH_DATABRICKS_PROFILE=work VIZMITH_DATABRICKS_CATALOG=... \\
    VIZMITH_DATABRICKS_SCHEMA=... VIZMITH_DATABRICKS_WAREHOUSE=... \\
    pytest -s tests/test_profiling_cost.py

Two things it answers beyond the number. Whether the width scales at all or only moves the
queue, which is what the comment on `PROFILE_WORKERS` guesses at; and what a warm load is
made of, which is the freshness pass alone and is the floor the request-count work leaves
behind.

The cache lives in a directory of its own per run, so a cold measurement is cold rather
than however the machine happened to be left.
"""

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import needs_warehouse

from vizmith.api import PROFILE_WORKERS
from vizmith.profiler import Profiles

# The widths the comment on PROFILE_WORKERS is a guess between. Eight is what ships, so it
# is in the middle of what is measured rather than at one end of it.
WIDTHS = (4, 8, 16, 24)

# Which of the three statements a line is. The freshness token is a DESCRIBE DETAIL, the
# samples read distinct values, and everything else over a table is the statistics pass.
FRESHNESS = "freshness"
STATISTICS = "statistics"
SAMPLES = "samples"


class Timed:
    """A catalog that records what each call to the source took, by kind.

    It wraps rather than subclasses, because what is being measured is the catalog the
    application builds and not a copy of it that might behave differently."""

    def __init__(self, catalog):
        self._catalog = catalog
        self.dialect = catalog.dialect
        self.taken: Counter[str] = Counter()
        self.calls: Counter[str] = Counter()

    def tables(self):
        with self._record("listing"):
            return self._catalog.tables()

    def describe(self, name):
        with self._record("describe"):
            return self._catalog.describe(name)

    def relationships(self):
        with self._record("relationships"):
            return self._catalog.relationships()

    def modified(self, name):
        with self._record(FRESHNESS):
            return self._catalog.modified(name)

    def run(self, sql, parameters=None):
        kind = SAMPLES if "DISTINCT" in sql.upper() and "count(" not in sql else STATISTICS
        with self._record(kind):
            return self._catalog.run(sql, parameters)

    @contextmanager
    def _record(self, kind: str):
        started = time.monotonic()
        try:
            yield
        finally:
            self.taken[kind] += time.monotonic() - started
            self.calls[kind] += 1


def measure(catalog, path: Path, width: int) -> tuple[float, Timed]:
    """One profile of the whole schema at one width, as the application does it."""
    timed = Timed(catalog)
    names = timed.tables()
    kept = Profiles(path)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=width) as pool:
        list(pool.map(lambda name: kept.read(timed, name), names))
    return time.monotonic() - started, timed


def report(title: str, width: int, wall: float, timed: Timed) -> None:
    parts = " | ".join(
        f"{kind} {timed.taken[kind]:.1f}s over {timed.calls[kind]}"
        for kind in (FRESHNESS, STATISTICS, SAMPLES)
        if timed.calls[kind]
    )
    print(f"{title:>5} width {width:>2}: {wall:6.1f}s wall  [{parts}]")


@needs_warehouse
@pytest.mark.parametrize("width", WIDTHS)
def test_what_profiling_a_schema_costs(live_catalog, tmp_path, width):
    """One line per width, cold and warm. Nothing is asserted about the clock: a timing
    that failed a build would fail it on whatever else the warehouse was doing."""
    cold_path = tmp_path / f"cold-{width}.json"
    cold, cold_timed = measure(live_catalog, cold_path, width)
    warm, warm_timed = measure(live_catalog, cold_path, width)

    report("cold", width, cold, cold_timed)
    report("warm", width, warm, warm_timed)

    assert warm_timed.calls[STATISTICS] == 0, "a warm profile ran a statistics pass"
    assert warm_timed.calls[SAMPLES] == 0, "a warm profile read samples again"
    assert cold_timed.calls[FRESHNESS] == warm_timed.calls[FRESHNESS] > 0


@needs_warehouse
def test_the_width_that_ships_is_one_of_the_widths_measured(live_catalog):
    """So the number in the comment is a row of the table above rather than a fourth
    number nobody timed."""
    assert PROFILE_WORKERS in WIDTHS
