import json
from pathlib import Path

import pytest
from generate_data import COLUMNS, DATA_DIR, write

from vizmith.spec.validate import conditions

VALID_SPECS = sorted((Path(__file__).parent / "fixtures" / "specs" / "valid").glob("*.json"))


def test_regenerating_changes_nothing(tmp_path):
    write(tmp_path)
    for name in COLUMNS:
        assert (tmp_path / f"{name}.parquet").read_bytes() == (DATA_DIR / f"{name}.parquet").read_bytes()


def test_three_segment_reference_resolves(fixture_db):
    assert fixture_db.execute("SELECT count(*) FROM vizmith.shop.orders").fetchone()[0] > 0


@pytest.mark.parametrize("path", VALID_SPECS, ids=lambda path: path.stem)
def test_every_column_a_valid_spec_names_exists(fixture_db, path):
    """The spec fixtures are the contract. A column one of them charts and the data
    does not have is a query that fails once the builder can execute it."""
    spec = json.loads(path.read_text())
    for reference in _column_references(spec["query"]):
        table, column = reference.rsplit(".", 1)
        table = table.rsplit(".", 1)[-1]
        assert column in [name for name, _ in COLUMNS[table]], f"{table}.{column}"


def _column_references(query):
    default_table = query["from"].rsplit(".", 1)[-1]
    tables = {default_table} | {join["table"].rsplit(".", 1)[-1] for join in query.get("joins", [])}

    def qualify(column):
        head = column.rsplit(".", 1)[0]
        if head.rsplit(".", 1)[-1] in tables and "." in column:
            return column
        return f"{default_table}.{column}"

    for item in query.get("select", []) + query.get("group_by", []) + [*conditions(query)]:
        yield qualify(item["column"])
    for aggregate in query.get("aggregates", []):
        if "column" in aggregate:
            yield qualify(aggregate["column"])
    for join in query.get("joins", []):
        for pair in join["on"]:
            yield qualify(pair["left"])
            yield qualify(pair["right"])


def test_one_column_has_nulls_and_another_has_none(fixture_db):
    missing_dates = fixture_db.execute(
        "SELECT count(*) FILTER (WHERE order_date IS NULL), count(*) FROM vizmith.shop.orders"
    ).fetchone()
    assert 0.01 < missing_dates[0] / missing_dates[1] < 0.10
    assert (
        fixture_db.execute(
            "SELECT count(*) FROM vizmith.shop.customers WHERE country IS NULL"
        ).fetchone()[0]
        == 0
    )


def test_cardinality_spans_both_ends(fixture_db):
    low = fixture_db.execute("SELECT count(DISTINCT status) FROM vizmith.shop.orders").fetchone()[0]
    high = fixture_db.execute(
        "SELECT count(DISTINCT location_code), count(DISTINCT shipment_id) FROM vizmith.shop.shipment_scans"
    ).fetchone()
    assert low <= 10
    assert high[0] >= 100
    assert high[1] >= 1000


def test_a_top_n_over_location_code_is_not_flat(fixture_db):
    top = fixture_db.execute(
        "SELECT count(*) AS scans FROM vizmith.shop.shipment_scans "
        "GROUP BY location_code ORDER BY scans DESC LIMIT 10"
    ).fetchall()
    assert top[0][0] > top[-1][0] * 2


def test_left_join_on_carriers_differs_from_inner(fixture_db):
    counts = fixture_db.execute(
        "SELECT (SELECT count(*) FROM vizmith.shop.shipments s "
        "LEFT JOIN vizmith.shop.carriers c ON s.carrier_id = c.id), "
        "(SELECT count(*) FROM vizmith.shop.shipments s "
        "JOIN vizmith.shop.carriers c ON s.carrier_id = c.id)"
    ).fetchone()
    assert counts[0] > counts[1]


def test_the_null_carrier_group_is_visible_and_does_not_rank_first(fixture_db):
    """The orphan shipments rank like any other value, per the result set contract in
    DESIGN.md. Sized so a reader sees the group without it winning the chart."""
    ranking = fixture_db.execute(
        "SELECT c.name, count(*) AS shipment_count FROM vizmith.shop.shipments s "
        "LEFT JOIN vizmith.shop.carriers c ON s.carrier_id = c.id "
        "GROUP BY c.name ORDER BY shipment_count DESC"
    ).fetchall()
    null_group = [count for name, count in ranking if name is None]
    assert len(null_group) == 1
    assert ranking[0][0] is not None
    assert null_group[0] > ranking[0][1] * 0.1


def test_limits_in_the_spec_fixtures_actually_truncate(fixture_db):
    countries = fixture_db.execute(
        "SELECT count(DISTINCT country) FROM vizmith.shop.customers"
    ).fetchone()[0]
    order_items = fixture_db.execute("SELECT count(*) FROM vizmith.shop.order_items").fetchone()[0]
    assert countries > 10
    assert order_items > 10000


def test_the_event_table_is_an_order_of_magnitude_larger(fixture_db):
    scans = fixture_db.execute("SELECT count(*) FROM vizmith.shop.shipment_scans").fetchone()[0]
    others = [
        fixture_db.execute(f"SELECT count(*) FROM vizmith.shop.{name}").fetchone()[0]
        for name in COLUMNS
        if name != "shipment_scans"
    ]
    assert scans > max(others) * 10
    assert (DATA_DIR / "shipment_scans.parquet").stat().st_size < 8_000_000


def test_scans_are_timestamped_finer_than_a_day(fixture_db):
    within_a_day = fixture_db.execute(
        "SELECT count(DISTINCT scanned_at) FROM vizmith.shop.shipment_scans "
        "WHERE scanned_at::DATE = (SELECT min(scanned_at)::DATE FROM vizmith.shop.shipment_scans)"
    ).fetchone()[0]
    assert within_a_day > 1


def test_two_catalogs_over_one_connection_do_not_read_each_others_rows(fixture_db):
    """#166, which is a defect in this harness rather than in Vizmith.

    A DuckDB connection is a single cursor, and the whole suite shares one. `FixtureCatalog`
    held its lock on the instance, which serialises nothing the moment two of them exist
    over that connection — and the interface fixture built one per request, so two tiles of
    a dashboard fetching at once interleaved `execute` and `fetchall` on one cursor. One of
    them came back with the other's rows or with none, which is a tile drawing "No rows to
    draw" for a spec that returns thirty, about once in four runs of the browser suite.

    A statement per thread against four catalogs, each asking a question with a different
    answer, and every answer has to be its own. Serialised, this is deterministic; it fails
    within a few rounds without."""
    from concurrent.futures import ThreadPoolExecutor

    from conftest import FixtureCatalog

    asking = {
        "orders": "SELECT count(*) FROM vizmith.shop.orders",
        "customers": "SELECT count(*) FROM vizmith.shop.customers",
        "products": "SELECT count(*) FROM vizmith.shop.products",
        "returns": "SELECT count(*) FROM vizmith.shop.returns",
    }
    catalogs = {name: FixtureCatalog(fixture_db) for name in asking}
    alone = {name: catalogs[name].run(sql) for name, sql in asking.items()}
    assert len({rows[0][0] for rows in alone.values()}) == len(asking), "the questions share an answer"

    rounds = [name for _ in range(30) for name in asking]
    with ThreadPoolExecutor(max_workers=len(asking)) as pool:
        together = list(pool.map(lambda name: (name, catalogs[name].run(asking[name])), rounds))

    wrong = [(name, rows) for name, rows in together if rows != alone[name]]
    assert wrong == [], "a statement came back with another statement's rows"
