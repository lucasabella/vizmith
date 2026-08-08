"""The BigQuery connector, against a fake client and — where somebody has one — a project.

Vizmith has never been run against a real project, so what these tests hold is the half a
project is not needed for: the SQL this connector sends, the shapes it turns responses
into, and the three places BigQuery differs from a warehouse. The client library is real,
so a parameter is the client's own object and a type name that does not exist would fail
here rather than in front of somebody.

What no fake can answer is at the bottom, gated on `VIZMITH_BIGQUERY_PROJECT`: whether the
fixture specs run, and whether the freshness candidate moves when the data does. Until
those are run, `modified` reports None and the connector's row in `docs/compatibility.md`
says it was read rather than measured.
"""

import datetime as dt
import json
import os
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest

from vizmith.catalog import DATE_TRUNC, SHAPES, TYPES, UNSUPPORTED, Dialect
from vizmith.profiler import profile_table
from vizmith.query import build, execute
from vizmith.sources.bigquery import TRUNCATE, BigQueryCatalog, _parameter_type, _type
from vizmith.sources.bigquery import TYPES as BIGQUERY_TYPES

PROJECT, DATASET = "acme", "shop"
ORDERS = f"{PROJECT}.{DATASET}.orders"

FIXTURES = Path(__file__).parent / "fixtures" / "specs" / "valid"
ORDERS_PER_MONTH = FIXTURES / "orders_per_month.json"

# The live half runs against a real project, which nobody has run this against yet. It
# takes its project and dataset from the environment, the way the warehouse tests take
# theirs, so a checkout with no credentials runs the deterministic half and skips these.
LIVE_PROJECT = os.environ.get("VIZMITH_BIGQUERY_PROJECT")
LIVE_DATASET = os.environ.get("VIZMITH_BIGQUERY_DATASET")
LIVE_LOCATION = os.environ.get("VIZMITH_BIGQUERY_LOCATION")

needs_project = pytest.mark.skipif(
    not LIVE_PROJECT or not LIVE_DATASET,
    reason="set VIZMITH_BIGQUERY_PROJECT and VIZMITH_BIGQUERY_DATASET to use a project",
)

# How long the metadata is given to publish a write before a candidate that has not moved
# is called one that does not move. Generous, because a slow publish is not a wrong key.
MODIFIED_WAIT = 60


class Row:
    """A row as the client hands one over: values in order, addressed by `values()`."""

    def __init__(self, values):
        self._values = values

    def values(self):
        return self._values


class Job:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return [Row(row) for row in self._rows]


class FakeClient:
    """A BigQuery client that answers from a script and records what it was asked.

    It records the job configuration rather than only the SQL, because how a value is bound
    is half of what this connector is: a parameter written into the statement instead would
    look identical in the SQL and be a different program."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked = []
        self.threads = set()

    def query(self, sql, job_config=None, location=None):
        self.threads.add(threading.get_ident())
        self.asked.append((sql, job_config, location))
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return Job(rows)
        return Job([])


def catalog_with(answers=None, location=None):
    """The connector with a client already in place, so nothing reaches Google."""
    catalog = BigQueryCatalog(project=PROJECT, dataset=DATASET, location=location)
    client = FakeClient(answers)
    catalog._local.client = client
    return catalog, client


COLUMNS = [
    ("id", "INT64", "NO"),
    ("customer_id", "INT64", "NO"),
    ("order_date", "DATE", "YES"),
    ("status", "STRING", "NO"),
    ("total", "NUMERIC(10, 2)", "NO"),
]


def test_the_listing_is_the_configured_dataset_in_qualified_names():
    catalog, client = catalog_with({"TABLES": [("orders",), ("customers",)]})

    assert catalog.tables() == [ORDERS, f"{PROJECT}.{DATASET}.customers"]
    sql, config, _ = client.asked[0]
    assert "INFORMATION_SCHEMA" in sql
    assert [(p.name, p.value) for p in config.query_parameters] == [("dataset", DATASET)]


def test_a_description_carries_types_nullability_and_declared_keys():
    catalog, _ = catalog_with(
        {
            "COLUMNS": COLUMNS,
            "TABLE_CONSTRAINTS": [("customer_id", "customers", "id")],
        }
    )

    described = catalog.describe("orders")

    assert described.name == ORDERS
    assert {c.name: c.type for c in described.columns} == {
        "id": "integer",
        "customer_id": "integer",
        "order_date": "date",
        "status": "string",
        "total": "decimal",
    }
    assert [c.nullable for c in described.columns] == [False, False, True, False, False]
    assert [(r.left_column, r.right_table, r.right_column) for r in described.relationships] == [
        ("customer_id", f"{PROJECT}.{DATASET}.customers", "id")
    ]
    assert described.relationships[0].kind == "declared"


def test_a_table_the_dataset_does_not_hold_is_a_failure_naming_it():
    catalog, _ = catalog_with()

    with pytest.raises(RuntimeError, match=f"{PROJECT}.{DATASET}.nowhere"):
        catalog.describe("nowhere")


def test_a_type_is_read_without_its_parameters():
    assert _type("NUMERIC(10, 2)") == "decimal"
    assert _type("STRING(64)") == "string"
    assert _type("int64") == "integer"
    assert _type("ARRAY<INT64>") == UNSUPPORTED
    assert _type("STRUCT<a INT64>") == UNSUPPORTED
    assert _type("GEOGRAPHY") == UNSUPPORTED
    assert set(BIGQUERY_TYPES.values()) <= set(SHAPES) <= set(TYPES.values())


def test_a_name_outside_the_configured_dataset_is_refused():
    """The scope, in this source's own words: a project and a dataset rather than a
    catalog and a schema."""
    catalog, client = catalog_with()

    with pytest.raises(ValueError, match="outside the configured dataset"):
        catalog.describe("other.shop.orders")
    assert client.asked == [], "an out of scope name reached the client"


def test_a_bound_value_is_the_clients_own_parameter_with_a_declared_type():
    """Every value in a spec is bound, including the row limits. A parameter written into
    the statement instead would look the same in the SQL and be a different program."""
    catalog, client = catalog_with({"COLUMNS": COLUMNS})

    catalog.run("SELECT 1 WHERE x = @a AND y = @b AND z = @c", {"a": 10, "b": 1.5, "c": "shipped"})

    _, config, _ = client.asked[0]
    assert [(p.name, p.type_, p.value) for p in config.query_parameters] == [
        ("a", "INT64", 10),
        ("b", "FLOAT64", 1.5),
        ("c", "STRING", "shipped"),
    ]
    assert _parameter_type(True) == "BOOL"


def test_a_row_arrives_in_the_shapes_the_contract_fixes():
    """The client answers in Python objects, so `conform` is the whole conversion. Every
    TIMESTAMP comes back in UTC, which is what makes the zone-stripping real work here
    rather than defensive."""
    utc = dt.datetime(2024, 1, 1, 9, 30, tzinfo=dt.UTC)
    # Combined rather than written, because the linter reads a bare datetime(...) as a
    # forgotten zone and here the absent zone is the thing being asserted.
    naive = dt.datetime.combine(dt.date(2024, 1, 1), dt.time(9, 30))
    catalog, _ = catalog_with({"SELECT": [(Decimal("18.08"), utc, dt.date(2024, 1, 1), "shipped", 3)]})

    (row,) = catalog.run("SELECT everything")

    assert row == (18.08, naive, dt.date(2024, 1, 1), "shipped", 3)
    assert row[1].tzinfo is None
    assert [type(value) for value in row] == [float, dt.datetime, dt.date, str, int]


def test_the_location_a_job_runs_in_is_passed_to_the_client():
    """A dataset in the EU cannot be queried from a job in the US, and the error for that
    reads as a missing table."""
    catalog, client = catalog_with({"TABLES": []}, location="EU")
    catalog.tables()

    assert client.asked[0][2] == "EU"
    assert catalog_with({"TABLES": []})[0]._location is None, "an empty setting is not a location"


def test_a_thread_gets_its_own_client():
    """The profiler runs eight statements at once and the metadata pool sixteen
    descriptions. Serialising them behind a lock would put a network round trip back into a
    queue the pools exist to empty, and sharing a client is documented as safe rather than
    measured, so a thread builds its own."""
    catalog, first = catalog_with({"TABLES": []})
    built = []

    def elsewhere():
        catalog._local.client = FakeClient({"TABLES": []})
        built.append(catalog._client())

    thread = threading.Thread(target=elsewhere)
    thread.start()
    thread.join()

    assert built and built[0] is not first
    assert catalog._client() is first, "another thread's client replaced this one's"


def test_there_is_no_freshness_token_until_somebody_measures_one():
    """`last_modified_time` moves on DML and may lag for the streaming buffer, and "mostly
    moves" is not something a cache key can be. So the cache is off and every read
    profiles, which is the expensive answer on a source billed by bytes and the one that
    cannot serve figures that are quietly out of date."""
    catalog, client = catalog_with()

    assert catalog.modified(ORDERS) is None
    assert client.asked == [], "the freshness answer cost a query"


def test_the_sample_statement_ignores_nulls():
    """BigQuery's ARRAY_AGG raises on a null element rather than skipping it, so without
    IGNORE NULLS every nullable column below the sample threshold fails the profiler's
    second statement — which is most columns. The templates being templates rather than
    function names is what makes this a string in the connector."""
    catalog, client = catalog_with(
        {
            "COLUMNS": [("status", "STRING", "YES")],
            "count(*)": [(4, 4, 2)],
            "ARRAY_AGG": [(["shipped", "held"],)],
        }
    )

    profile = profile_table(catalog, "orders")

    assert profile.columns[0].samples == ("held", "shipped")
    assert "ARRAY_AGG(DISTINCT `status` IGNORE NULLS)" in client.asked[-1][0]
    assert "APPROX_COUNT_DISTINCT(`status`)" in client.asked[-2][0]


def test_truncation_is_one_form_for_every_temporal_column():
    """BigQuery names three truncation functions and picks by the column's type, which the
    builder cannot do: it knows a column's name and not its type. It does not have to. The
    result set contract already fixes what a truncated value is — both shipping sources
    answer a truncated date with a timestamp — so the function follows from the shape the
    value has to come back in, and that is one form for every column."""
    catalog, _ = catalog_with({"COLUMNS": COLUMNS})

    sql, parameters = build(json.loads(ORDERS_PER_MONTH.read_text()), catalog)

    assert "TIMESTAMP_TRUNC(CAST(`acme`.`shop`.`orders`.`order_date` AS TIMESTAMP), month)" in sql
    assert "date_trunc(" not in sql, "the builder wrote another source's spelling"
    assert "@p0" in sql and parameters


def test_the_truncation_template_is_the_dialects_and_the_default_is_unchanged():
    """The field is what made the connector possible, and its default is what the two
    sources that came before it were already emitting."""
    assert Dialect(quote='"', approx_distinct=None, distinct_values="x", parameter=":{name}").truncate == (
        DATE_TRUNC
    )
    assert BigQueryCatalog.dialect.truncate == TRUNCATE
    assert DATE_TRUNC.format(unit="month", column="c") == "date_trunc('month', c)"


def test_the_client_is_built_on_first_use_and_not_before(monkeypatch):
    """Constructing a catalog reaches no credentials, which is what lets `/api/health`
    answer on a server whose source is configured wrongly."""
    catalog = BigQueryCatalog(project=PROJECT, dataset=DATASET)

    assert getattr(catalog._local, "client", None) is None
    assert catalog.scope.values == (PROJECT, DATASET)


def live() -> BigQueryCatalog:
    return BigQueryCatalog(project=LIVE_PROJECT, dataset=LIVE_DATASET, location=LIVE_LOCATION)


@needs_project
@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
def test_every_spec_that_ships_runs_against_a_real_project(path):
    """What no fake can answer. The bar DESIGN.md sets for a dialect is that the one that
    ships is the only one a user's chart depends on.

    It needs the fixture dataset loaded into the project — the same eight tables the
    Parquet files hold — which is what `VIZMITH_BIGQUERY_DATASET` should name. Until
    somebody has run this, the BigQuery row in docs/compatibility.md says the connector was
    read rather than measured, and it should keep saying so."""
    rows = execute(json.loads(path.read_text()), live())

    assert rows, f"{path.stem} drew nothing"
    assert all(isinstance(row, dict) for row in rows)


@needs_project
def test_whether_the_modified_time_moves_when_the_data_changes():
    """The measurement `modified` is waiting on, in the shape the Databricks one takes:
    write a row, re-read the candidate, see whether it moved.

    `last_modified_time` is the candidate and the streaming buffer is the documented
    caveat, so this writes through a plain DML insert — the case that is supposed to work —
    and a failure here settles it as None for good. What it cannot settle from a test
    without the streaming API is the buffered case; somebody with one should extend this
    before the candidate is trusted.

    The table is created and dropped here, so nothing in the dataset is written to. A
    project that will not take a temporary table skips rather than fails: the point is the
    timestamp, not the permission."""
    catalog = live()
    name = "vizmith_modified_probe"
    quoted = catalog.dialect.qualified(catalog.scope.qualify(name))
    candidate = (
        f"SELECT last_modified_time FROM `{LIVE_PROJECT}`.`{LIVE_DATASET}`.__TABLES__ "
        f"WHERE table_id = '{name}'"
    )
    try:
        catalog.run(f"CREATE OR REPLACE TABLE {quoted} (n INT64)")
    except Exception as refusal:  # noqa: BLE001 — the client's own type, and this is a skip
        pytest.skip(f"the project would not take a temporary table: {refusal}")

    try:
        catalog.run(f"INSERT INTO {quoted} (n) VALUES (1)")
        (before,) = catalog.run(candidate)[0]
        catalog.run(f"INSERT INTO {quoted} (n) VALUES (2)")
        deadline = time.monotonic() + MODIFIED_WAIT
        while (after := catalog.run(candidate)[0][0]) == before and time.monotonic() < deadline:
            time.sleep(2)
    finally:
        catalog.run(f"DROP TABLE IF EXISTS {quoted}")

    assert after != before, (
        f"last_modified_time did not move in {MODIFIED_WAIT} seconds after an insert, so it "
        f"cannot key the profile cache and None is the connector's answer for good"
    )
    print(f"\nlast_modified_time moved from {before} to {after}: it is a candidate")
