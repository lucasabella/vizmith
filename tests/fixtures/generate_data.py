"""Generates the synthetic fixture dataset as Parquet.

Run with `python tests/fixtures/generate_data.py`. Output is committed and
`test_fixture_data.py` asserts that regenerating it changes nothing.

Everything here is invented. No organisation, brand or person is real.
"""

import csv
import datetime as dt
import random
import tempfile
from pathlib import Path

import duckdb

SEED = 20260730
DATA_DIR = Path(__file__).parent / "data"

# More than the limit of 10 that the revenue fixtures ask for, so the limit truncates,
# and skewed so a top-N is not a flat line.
COUNTRIES = [
    ("Netherlands", 30),
    ("Germany", 24),
    ("Belgium", 14),
    ("France", 11),
    ("Spain", 8),
    ("Italy", 7),
    ("Poland", 6),
    ("Sweden", 5),
    ("Austria", 4),
    ("Denmark", 3),
    ("Portugal", 3),
    ("Ireland", 2),
    ("Finland", 2),
    ("Norway", 1),
]
CATEGORIES = [
    ("Apparel", 26),
    ("Electronics", 22),
    ("Home", 18),
    ("Garden", 14),
    ("Books", 12),
    ("Toys", 8),
]
ORDER_STATUS = [
    ("delivered", 52),
    ("shipped", 21),
    ("pending", 14),
    ("cancelled", 8),
    ("refunded", 5),
]
RETURN_REASONS = [
    ("damaged", 31),
    ("wrong_item", 24),
    ("changed_mind", 20),
    ("quality", 15),
    ("late_delivery", 10),
]
SCAN_STATUS = [
    ("in_transit", 58),
    ("received", 20),
    ("out_for_delivery", 12),
    ("delivered", 8),
    ("exception", 2),
]

N_CUSTOMERS = 2000
N_PRODUCTS = 400
N_ORDERS = 6000
N_CARRIERS = 8
# Shipments referencing these carrier ids find nothing, so a left join differs from an
# inner one and produces a null carrier group that ranks like any other value.
ORPHAN_CARRIER_IDS = [101, 102, 103]

FIRST_DAY = dt.date(2024, 1, 1)
LAST_DAY = dt.date(2026, 6, 30)
SIGNUP_FIRST_DAY = dt.date(2023, 1, 1)


def _weighted(rng, pairs, k=1):
    values = [value for value, _ in pairs]
    weights = [weight for _, weight in pairs]
    return rng.choices(values, weights=weights, k=k)


def _random_date(rng, first, last):
    return first + dt.timedelta(days=rng.randrange((last - first).days + 1))


def customers(rng):
    countries = _weighted(rng, COUNTRIES, k=N_CUSTOMERS)
    return [
        (i, countries[i - 1], _random_date(rng, SIGNUP_FIRST_DAY, LAST_DAY))
        for i in range(1, N_CUSTOMERS + 1)
    ]


def products(rng):
    categories = _weighted(rng, CATEGORIES, k=N_PRODUCTS)
    return [
        (
            i,
            categories[i - 1],
            f"Brand {rng.randrange(1, 21):02d}",
            round(rng.uniform(4.5, 240.0), 2),
        )
        for i in range(1, N_PRODUCTS + 1)
    ]


def orders_and_items(rng, product_prices):
    """Orders carry no measure of their own: total and item_count come from the items,
    so the scatter fixture over the two shows a relationship rather than noise."""
    orders = []
    items = []
    statuses = _weighted(rng, ORDER_STATUS, k=N_ORDERS)
    item_id = 1
    for order_id in range(1, N_ORDERS + 1):
        total = 0.0
        item_count = 0
        for _ in range(rng.randrange(1, 6)):
            product_id = rng.randrange(1, N_PRODUCTS + 1)
            quantity = rng.randrange(1, 5)
            line_total = round(product_prices[product_id] * quantity, 2)
            items.append((item_id, order_id, product_id, quantity, line_total))
            item_id += 1
            total += line_total
            item_count += quantity
        # A meaningful null rate on a column two fixtures filter on. customers.country
        # and every id column stay complete, so both cases are covered.
        order_date = None if rng.random() < 0.03 else _random_date(rng, FIRST_DAY, LAST_DAY)
        orders.append(
            (
                order_id,
                rng.randrange(1, N_CUSTOMERS + 1),
                order_date,
                statuses[order_id - 1],
                round(total, 2),
                item_count,
            )
        )
    return orders, items


def returns(rng, orders):
    rows = []
    return_id = 1
    for order_id, _, order_date, status, _, _ in orders:
        if status == "cancelled" or rng.random() >= 0.09:
            continue
        base = order_date or FIRST_DAY
        returned_at = dt.datetime.combine(
            base + dt.timedelta(days=rng.randrange(2, 30)),
            dt.time(rng.randrange(8, 20), rng.randrange(0, 60)),
        )
        rows.append((return_id, order_id, _weighted(rng, RETURN_REASONS)[0], returned_at))
        return_id += 1
    return rows


def carriers():
    return [(i, f"Carrier {chr(64 + i)}") for i in range(1, N_CARRIERS + 1)]


def shipments(rng, orders):
    rows = []
    shipment_id = 1
    for order_id, _, order_date, status, _, _ in orders:
        if status not in ("shipped", "delivered"):
            continue
        # Small enough that the null carrier group never wins the ranking, large enough
        # to be visible next to the eight real carriers.
        if rng.random() < 0.06:
            carrier_id = rng.choice(ORPHAN_CARRIER_IDS)
        else:
            carrier_id = rng.randrange(1, N_CARRIERS + 1)
        if order_date is None or rng.random() < 0.04:
            shipped_at = None
        else:
            shipped_at = dt.datetime.combine(
                order_date + dt.timedelta(days=rng.randrange(0, 5)),
                dt.time(rng.randrange(6, 22), rng.randrange(0, 60)),
            )
        rows.append((shipment_id, order_id, carrier_id, shipped_at, round(rng.uniform(2.5, 34.0), 2)))
        shipment_id += 1
    return rows


def shipment_scans(rng, shipment_rows):
    """The event table. Larger by an order of magnitude, high cardinality on
    shipment_id and location_code, skewed over location_code, and timestamped
    finer than a day, so a profiler that scans every column is visibly expensive."""
    locations = [f"LOC-{i:04d}" for i in range(1, 501)]
    weights = [1000 / (i + 4) ** 1.1 for i in range(1, 501)]
    rows = []
    scan_id = 1
    for shipment_id, _, _, shipped_at, _ in shipment_rows:
        count = rng.randrange(35, 71)
        moment = shipped_at or dt.datetime.combine(FIRST_DAY, dt.time(6, 0))
        codes = rng.choices(locations, weights=weights, k=count)
        statuses = _weighted(rng, SCAN_STATUS, k=count)
        for i in range(count):
            moment += dt.timedelta(minutes=rng.randrange(7, 240), seconds=rng.randrange(0, 60))
            rows.append((scan_id, shipment_id, moment, codes[i], statuses[i]))
            scan_id += 1
    return rows


COLUMNS = {
    "customers": [("id", "INTEGER"), ("country", "VARCHAR"), ("signup_date", "DATE")],
    "products": [
        ("id", "INTEGER"),
        ("category", "VARCHAR"),
        ("brand", "VARCHAR"),
        ("price", "DECIMAL(10,2)"),
    ],
    "orders": [
        ("id", "INTEGER"),
        ("customer_id", "INTEGER"),
        ("order_date", "DATE"),
        ("status", "VARCHAR"),
        ("total", "DECIMAL(10,2)"),
        ("item_count", "INTEGER"),
    ],
    "order_items": [
        ("id", "INTEGER"),
        ("order_id", "INTEGER"),
        ("product_id", "INTEGER"),
        ("quantity", "INTEGER"),
        ("line_total", "DECIMAL(10,2)"),
    ],
    "returns": [
        ("id", "INTEGER"),
        ("order_id", "INTEGER"),
        ("reason", "VARCHAR"),
        ("returned_at", "TIMESTAMP"),
    ],
    "carriers": [("id", "INTEGER"), ("name", "VARCHAR")],
    "shipments": [
        ("id", "INTEGER"),
        ("order_id", "INTEGER"),
        ("carrier_id", "INTEGER"),
        ("shipped_at", "TIMESTAMP"),
        ("cost", "DECIMAL(10,2)"),
    ],
    "shipment_scans": [
        ("id", "INTEGER"),
        ("shipment_id", "INTEGER"),
        ("scanned_at", "TIMESTAMP"),
        ("location_code", "VARCHAR"),
        ("status", "VARCHAR"),
    ],
}


def generate():
    rng = random.Random(SEED)
    customer_rows = customers(rng)
    product_rows = products(rng)
    prices = {row[0]: row[3] for row in product_rows}
    order_rows, item_rows = orders_and_items(rng, prices)
    shipment_rows = shipments(rng, order_rows)
    return {
        "customers": customer_rows,
        "products": product_rows,
        "orders": order_rows,
        "order_items": item_rows,
        "returns": returns(rng, order_rows),
        "carriers": carriers(),
        "shipments": shipment_rows,
        "shipment_scans": shipment_scans(rng, shipment_rows),
    }


def write(target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    # One thread, so row order in the written file is the order the rows were generated.
    con = duckdb.connect(config={"threads": 1})
    with tempfile.TemporaryDirectory() as staging:
        for name, rows in generate().items():
            # Binding the event table row by row costs a minute. A CSV that DuckDB
            # parses itself costs a fraction of a second.
            csv_path = Path(staging) / f"{name}.csv"
            with csv_path.open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)
            columns = ", ".join(f"'{column}': '{type_}'" for column, type_ in COLUMNS[name])
            con.execute(
                f"COPY (SELECT * FROM read_csv('{csv_path}', header = false, columns = {{{columns}}})) "
                f"TO '{target_dir / f'{name}.parquet'}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
    con.close()


if __name__ == "__main__":
    write(DATA_DIR)
