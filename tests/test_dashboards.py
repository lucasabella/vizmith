"""The dashboard store: what it keeps, what it refuses, and what survives a restart.

Every test here is offline. A dashboard is specs and an order over them, so nothing in it
needs a source, and the one thing it does need — a judgement about whether a spec is legal
— is the validator, which reaches no source either.
"""

import json
from pathlib import Path

import pytest

from vizmith.dashboards import (
    NAME_LIMIT,
    TILE_LIMIT,
    Dashboards,
    Refused,
    review,
)
from vizmith.spec import validate_spec

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
REVENUE_BY_COUNTRY = FIXTURES / "valid" / "revenue_by_country.json"
ORDERS_PER_MONTH = FIXTURES / "valid" / "orders_per_month.json"
MISSING_LIMIT = FIXTURES / "invalid" / "missing_limit.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def tile(path: Path = REVENUE_BY_COUNTRY, width: int = 1) -> dict:
    return {"spec": load(path), "width": width}


@pytest.fixture
def store(tmp_path) -> Dashboards:
    return Dashboards(tmp_path / "dashboards.json")


def test_a_saved_dashboard_reads_back_as_what_was_saved(store):
    store.save("Revenue", [tile(REVENUE_BY_COUNTRY, width=2), tile(ORDERS_PER_MONTH)])

    read = store.read("Revenue")

    assert read.name == "Revenue"
    assert [t.spec for t in read.tiles] == [load(REVENUE_BY_COUNTRY), load(ORDERS_PER_MONTH)]
    assert [t.width for t in read.tiles] == [2, 1]


def test_the_order_of_the_tiles_is_the_arrangement(store):
    """The only thing that says which tile is drawn first is where it sits in the list, so
    saving them the other way round has to come back the other way round."""
    store.save("Revenue", [tile(REVENUE_BY_COUNTRY), tile(ORDERS_PER_MONTH)])
    store.save("Revenue", [tile(ORDERS_PER_MONTH), tile(REVENUE_BY_COUNTRY)])

    assert [t.spec for t in store.read("Revenue").tiles] == [
        load(ORDERS_PER_MONTH),
        load(REVENUE_BY_COUNTRY),
    ]


def test_a_dashboard_survives_a_restart(tmp_path):
    """Two stores over one file, which is what a restart is from here."""
    path = tmp_path / "dashboards.json"
    Dashboards(path).save("Revenue", [tile()])

    assert Dashboards(path).read("Revenue").tiles[0].spec == load(REVENUE_BY_COUNTRY)


def test_nothing_is_saved_under_an_unused_name(store):
    assert store.read("Revenue") is None
    assert store.names() == []


def test_names_come_back_in_one_order_whatever_order_they_were_saved_in(store):
    store.save("Shipping", [tile()])
    store.save("Revenue", [tile()])

    assert store.names() == ["Revenue", "Shipping"]


def test_saving_under_a_name_replaces_what_that_name_held(store):
    store.save("Revenue", [tile(REVENUE_BY_COUNTRY), tile(ORDERS_PER_MONTH)])
    store.save("Revenue", [tile(ORDERS_PER_MONTH)])

    assert len(store.read("Revenue").tiles) == 1
    assert store.names() == ["Revenue"]


def test_a_deleted_dashboard_is_gone_and_says_it_was_there(store):
    store.save("Revenue", [tile()])

    assert store.delete("Revenue") is True
    assert store.read("Revenue") is None
    assert store.delete("Revenue") is False


def test_a_tile_the_validator_rejects_is_refused_in_its_own_words_and_named_by_position(store):
    """The validator's messages are written to be fed back to a model on retry, so a
    dashboard that reworded them would make one spec fail differently depending on which
    endpoint refused it. The only thing added is where the tile sits."""
    with pytest.raises(Refused) as refusal:
        store.save("Revenue", [tile(REVENUE_BY_COUNTRY), tile(MISSING_LIMIT)])

    assert refusal.value.errors == [
        f"tile 2: {error}" for error in validate_spec(load(MISSING_LIMIT))
    ]


def test_a_refused_save_leaves_what_was_saved_before(store):
    store.save("Revenue", [tile(REVENUE_BY_COUNTRY)])

    with pytest.raises(Refused):
        store.save("Revenue", [tile(MISSING_LIMIT)])

    assert store.read("Revenue").tiles[0].spec == load(REVENUE_BY_COUNTRY)


def test_a_refused_save_writes_no_file(tmp_path):
    path = tmp_path / "dashboards.json"

    with pytest.raises(Refused):
        Dashboards(path).save("Revenue", [tile(MISSING_LIMIT)])

    assert not path.exists()


@pytest.mark.parametrize(
    "name",
    ["", "   ", " Revenue", "Revenue ", "a" * (NAME_LIMIT + 1), "Revenue/2026", "Revenue\nby country"],
)
def test_a_name_that_cannot_address_a_dashboard_is_refused(store, name):
    with pytest.raises(Refused):
        store.save(name, [tile()])


def test_a_dashboard_holds_at_least_one_tile(store):
    with pytest.raises(Refused) as refusal:
        store.save("Revenue", [])

    assert "at least one tile" in refusal.value.errors[0]


def test_more_tiles_than_the_cap_are_refused_saying_what_they_cost(store):
    with pytest.raises(Refused) as refusal:
        store.save("Revenue", [tile()] * (TILE_LIMIT + 1))

    assert str(TILE_LIMIT) in refusal.value.errors[0]
    assert "statement per tile" in refusal.value.errors[0]


@pytest.mark.parametrize("width", [0, 3, "1", 1.5, True, None])
def test_a_tile_spanning_something_the_grid_does_not_have_is_refused(store, width):
    with pytest.raises(Refused) as refusal:
        store.save("Revenue", [{"spec": load(REVENUE_BY_COUNTRY), "width": width}])

    assert any("columns" in error for error in refusal.value.errors)


def test_a_tile_that_is_not_an_object_or_carries_no_spec_is_refused(store):
    assert review("Revenue", ["a spec"]) == ["tile 1: a tile is an object holding a spec and a width"]
    assert review("Revenue", [{"width": 1}]) == ["tile 1: a tile holds a spec, which is what draws it"]


def test_a_width_the_tile_leaves_out_is_one_column(store):
    store.save("Revenue", [{"spec": load(REVENUE_BY_COUNTRY)}])

    assert store.read("Revenue").tiles[0].width == 1


def test_a_store_holds_no_rows(store):
    """A dashboard is specs. A stored result set would be a copy of the source that stops
    being true without saying so, and the file is where that would show up."""
    store.save("Revenue", [tile()])

    written = json.loads((store._path).read_text())
    assert set(written["dashboards"]["Revenue"]["tiles"][0]) == {"spec", "width"}


def test_a_file_that_cannot_be_read_raises_rather_than_reading_as_empty(tmp_path):
    """The profile cache drops a file it cannot read, because that costs one more profile.
    This one is work a person did, and starting empty would mean the next save overwrites
    what was in it."""
    path = tmp_path / "dashboards.json"
    path.write_text("{ not json")

    with pytest.raises(ValueError):
        Dashboards(path)
