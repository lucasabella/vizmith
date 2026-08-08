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

It also times the relationship graph, which is what a drag of a column onto another table
waits for, in both the shape that ships and the sequential one it replaced. A pool is only
worth what the source will answer at once, and only a workspace can say what that is.

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

from vizmith.api import PROFILE_WORKERS, relationship_graph
from vizmith.catalog import FRESHNESS_CEILING, FRESHNESS_HOLD, Held
from vizmith.profiler import Profiles
from vizmith.relationships import graph, suggest

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
        self.scope = catalog.scope
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


def sequentially(catalog):
    """The relationship graph built the way it was before the descriptions overlapped: one
    description after another, and the declared keys asked for separately rather than read
    off the descriptions, which is the schema read twice.

    Kept here rather than in the application, because what it is for is the comparison: a
    number that says the pool helped on this schema, against this workspace, rather than on
    the model in an issue. It is the only copy of the old shape and nothing imports it."""
    columns = {
        described.name: {column.name: column.type for column in described.columns}
        for described in (catalog.describe(name) for name in catalog.tables())
    }
    return graph(catalog.relationships(), suggest(columns))


@needs_warehouse
def test_what_building_the_relationship_graph_costs(live_catalog):
    """What a drag of a column onto another table waits for.

    `/api/join-path` is what a drop calls, so this is paid per gesture rather than once. It
    was one round trip per table for the columns and, inside the catalog, one more per table
    for the declared constraints. It is one per table now — a description carries the
    constraints the same response held — and none at all on the drag after the first, since
    the configured source holds a description for a window of its own.

    Three shapes are timed so the comparison is against this workspace rather than against a
    model: the pool is only worth what the source will actually answer at once, and a
    control plane that serialises them would show up here as an overlapped number that
    matches the sequential one.

    Nothing is asserted about the clock. What is asserted is that the concurrent shape asks
    for each table exactly once, that the warm one asks for none, and that all three agree,
    because a faster answer that differs is not the same answer."""
    concurrent = Timed(live_catalog)
    started = time.monotonic()
    overlapped = relationship_graph(concurrent)
    with_pool = time.monotonic() - started

    one_at_a_time = Timed(live_catalog)
    started = time.monotonic()
    walked = sequentially(one_at_a_time)
    without_pool = time.monotonic() - started

    # Counted inside the hold rather than outside it, because what is being measured is
    # what reaches the source and not what the application asked for.
    counted = Timed(live_catalog)
    source = Held(counted)
    relationship_graph(source)
    started = time.monotonic()
    warm = relationship_graph(source)
    held = time.monotonic() - started

    tables = len(live_catalog.tables())
    print(
        f"\nrelationship graph over {tables} tables: "
        f"{without_pool:.1f}s one at a time, {with_pool:.1f}s overlapped, {held:.1f}s held"
        f"\n  describe {concurrent.taken['describe']:.1f}s over {concurrent.calls['describe']} calls "
        f"cold, {counted.calls['describe'] - tables} calls on the second graph"
    )

    assert overlapped == walked, "the pool changed the graph, not only how long it took"
    assert warm == overlapped, "a held description changed the graph"
    assert concurrent.calls["describe"] == tables, "a table was described twice or not at all"
    assert concurrent.calls["relationships"] == 0, "the keys were asked for a second time"
    assert counted.calls["describe"] == tables, "the second graph described the schema again"


@needs_warehouse
def test_what_the_freshness_window_has_to_cover(live_catalog, tmp_path):
    """The measurement #103 asked for, which is the one this file was written to make
    possible and the one the constants are still short of.

    `FRESHNESS_HOLD` and `FRESHNESS_CEILING` govern something proportional to the schema: a
    cold read pays a freshness statement per table, and the window has to outlive that read
    or the request after it re-reads the front of the schema while the back is still warm.
    Held per answer that failed at 150 tables. Held per burst it holds — up to the ceiling,
    which is the number this prints the evidence for.

    Nothing is asserted about the clock beyond the one thing that is a real bound: a cold
    read longer than the ceiling is a schema this design does not cover, and that is worth
    failing rather than printing."""
    counted = Timed(live_catalog)
    source = Held(counted)
    kept = Profiles(tmp_path / "profiles.json")
    names = live_catalog.tables()

    def sweep() -> float:
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=PROFILE_WORKERS) as pool:
            list(pool.map(lambda name: kept.read(source, name), names))
        return time.monotonic() - started

    cold = sweep()
    asked = counted.calls[FRESHNESS]
    warm = sweep()
    again = counted.calls[FRESHNESS] - asked

    print(
        f"\nfreshness window over {len(names)} tables: {cold:.1f}s cold, {warm:.1f}s warm"
        f"\n  {asked} freshness statements cold, {again} on the read after it"
        f"\n  hold {FRESHNESS_HOLD:.0f}s, ceiling {FRESHNESS_CEILING:.0f}s"
    )

    assert asked == len(names), "a cold read did not ask about every table exactly once"
    assert again == 0, "the burst did not outlive the read it is there to protect"
    assert cold < FRESHNESS_CEILING, (
        f"a cold read of {cold:.1f}s does not fit inside a ceiling of {FRESHNESS_CEILING:.0f}s, "
        "so the burst rolls while it is still running and this schema is not covered"
    )
