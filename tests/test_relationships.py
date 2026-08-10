"""The relationship graph: what is declared, what is inferred, and what resolves.

Every test here is offline. Inference and resolution are pure functions over names and
types, which is the point of putting them outside the catalog.
"""

import os

import pytest
from generate_data import COLUMNS, FOREIGN_KEYS

from vizmith.catalog import DECLARED, SUGGESTED, Relationship
from vizmith.relationships import (
    CONFIRMED,
    OPEN,
    REJECTED,
    Confirmations,
    graph,
    resolve,
    suggest,
)
from vizmith.state import Damaged

TYPES = {"INTEGER": "integer", "VARCHAR": "string", "DATE": "date", "TIMESTAMP": "timestamp"}


def shop() -> dict[str, dict[str, str]]:
    """The fixture schema as the profiles report it: qualified names, and the closed set
    of types rather than the source's own."""
    return {
        f"vizmith.shop.{table}": {
            column: TYPES.get(type_, "decimal") for column, type_ in columns
        }
        for table, columns in COLUMNS.items()
    }


def declared() -> list[Relationship]:
    return [
        Relationship(f"vizmith.shop.{left}", column, f"vizmith.shop.{right}", key, kind=DECLARED)
        for left, column, right, key in FOREIGN_KEYS
    ]


def named(relationships) -> set[str]:
    return {relationship.key for relationship in relationships}


def test_a_key_named_after_a_table_is_suggested():
    """`shipments.carrier_id` names `carriers`, whose `id` is the same type. Nothing in
    the fixture declares it, so it is a question rather than a fact."""
    suggested = suggest(shop())

    assert "vizmith.shop.shipments.carrier_id>vizmith.shop.carriers.id" in named(suggested)
    assert all(relationship.kind == SUGGESTED for relationship in suggested)


def test_every_suggestion_is_a_key_pointing_at_an_id():
    """The inference is conservative by requirement. Anything it offers has to read as a
    key on one side and be the key column on the other."""
    for relationship in suggest(shop()):
        assert relationship.left_column.endswith("_id")
        assert relationship.right_column == "id"


def test_a_name_match_with_a_different_type_is_not_offered():
    """A column that happens to be called the same thing is not a relationship, and
    offering it teaches a person to confirm without reading."""
    columns = {
        "shop.orders": {"id": "integer", "customer_id": "string"},
        "shop.customers": {"id": "integer"},
    }

    assert suggest(columns) == []


def test_a_table_with_no_id_column_is_not_pointed_at():
    columns = {"shop.orders": {"customer_id": "integer"}, "shop.customers": {"code": "integer"}}

    assert suggest(columns) == []


def test_a_declared_relationship_wins_over_the_same_suggestion():
    """`orders.customer_id` is both declared and inferrable. A source that stated it has
    answered the question, so the suggestion is not offered next to the fact."""
    known = graph(declared(), suggest(shop()))
    orders = [r for r in known if r.key == "vizmith.shop.orders.customer_id>vizmith.shop.customers.id"]

    assert [relationship.kind for relationship in orders] == [DECLARED]


def test_the_graph_holds_the_declared_ones_and_the_rest_as_suggestions():
    known = graph(declared(), suggest(shop()))
    kinds = {relationship.key: relationship.kind for relationship in known}

    assert kinds["vizmith.shop.order_items.order_id>vizmith.shop.orders.id"] == DECLARED
    assert kinds["vizmith.shop.returns.order_id>vizmith.shop.orders.id"] == SUGGESTED


def test_a_direct_relationship_resolves_to_one_hop():
    known = graph(declared(), suggest(shop()))

    path = resolve(known, "vizmith.shop.orders", "vizmith.shop.customers")

    assert [relationship.key for relationship in path] == [
        "vizmith.shop.orders.customer_id>vizmith.shop.customers.id"
    ]


def test_resolving_across_an_intermediate_table_returns_the_full_path():
    """customers to order_items through orders, which is the case the whole graph exists
    for: a person drags a column from a table the query has no edge to."""
    known = graph(declared(), suggest(shop()))

    path = resolve(known, "vizmith.shop.customers", "vizmith.shop.order_items")

    assert [relationship.key for relationship in path] == [
        "vizmith.shop.orders.customer_id>vizmith.shop.customers.id",
        "vizmith.shop.order_items.order_id>vizmith.shop.orders.id",
    ]


def test_a_suggestion_does_not_resolve_until_it_is_confirmed(tmp_path):
    """The rule the rest of the design rests on. An unconfirmed suggestion is a guess,
    and a guess joined on produces a plausible number rather than an error."""
    known = graph(declared(), suggest(shop()))
    confirmations = Confirmations(tmp_path / "relationships.json")
    carriers = next(r for r in known if r.right_table == "vizmith.shop.carriers")

    with pytest.raises(ValueError, match="No confirmed relationship"):
        resolve(confirmations.usable(known), "vizmith.shop.shipments", "vizmith.shop.carriers")

    confirmations.record(carriers, CONFIRMED)
    path = resolve(confirmations.usable(known), "vizmith.shop.shipments", "vizmith.shop.carriers")

    assert path == [carriers]


def test_two_paths_of_the_same_length_report_ambiguity_rather_than_choosing():
    """A diamond. Picking one of the two answers a question nobody can see was asked, and
    the number it produces looks exactly like the number the other one produces."""
    known = [
        Relationship("a", "b_id", "b", "id"),
        Relationship("a", "c_id", "c", "id"),
        Relationship("d", "b_id", "b", "id"),
        Relationship("d", "c_id", "c", "id"),
    ]

    with pytest.raises(ValueError, match="2 confirmed paths of the same length"):
        resolve(known, "a", "d")


def test_no_path_names_both_tables_in_the_message():
    """This message ends up under a column somebody dragged, so it has to say what has no
    path to what rather than that resolution failed."""
    with pytest.raises(ValueError) as failure:
        resolve([Relationship("a", "b_id", "b", "id")], "a", "elsewhere")

    assert "'a'" in str(failure.value)
    assert "'elsewhere'" in str(failure.value)


def test_a_declared_relationship_needs_no_confirmation(tmp_path):
    confirmations = Confirmations(tmp_path / "relationships.json")

    assert confirmations.state(declared()[0]) == CONFIRMED


def test_an_answer_survives_a_re_profile(tmp_path):
    """A second Confirmations over the same file stands in for a restart, and a second
    graph built from a fresh profile stands in for the re-profile. Neither loses the
    answer, because nothing here is keyed by where a relationship sat in a list."""
    path = tmp_path / "relationships.json"
    known = graph(declared(), suggest(shop()))
    returns = next(r for r in known if r.left_table == "vizmith.shop.returns")
    scans = next(r for r in known if r.left_table == "vizmith.shop.shipment_scans")

    first = Confirmations(path)
    first.record(returns, CONFIRMED)
    first.record(scans, REJECTED)

    after = Confirmations(path)
    reprofiled = graph(declared(), suggest(shop()))

    assert after.state(returns) == CONFIRMED
    assert after.state(scans) == REJECTED
    assert returns in after.usable(reprofiled)
    assert scans not in after.usable(reprofiled)


def test_a_rejected_suggestion_is_not_offered_again(tmp_path):
    known = graph(declared(), suggest(shop()))
    scans = next(r for r in known if r.left_table == "vizmith.shop.shipment_scans")

    confirmations = Confirmations(tmp_path / "relationships.json")
    confirmations.record(scans, REJECTED)

    assert scans not in confirmations.offered(known)
    assert scans not in Confirmations(tmp_path / "relationships.json").offered(known)


def test_a_confirmation_can_be_taken_back(tmp_path):
    """One mis-click on Confirm is a wrong join, and a wrong join is a plausible number.
    So the undo is part of the feature rather than a convenience."""
    known = graph(declared(), suggest(shop()))
    carriers = next(r for r in known if r.right_table == "vizmith.shop.carriers")
    confirmations = Confirmations(tmp_path / "relationships.json")

    confirmations.record(carriers, CONFIRMED)
    confirmations.record(carriers, OPEN)

    assert confirmations.state(carriers) == OPEN
    assert carriers in confirmations.offered(known)
    assert carriers not in confirmations.usable(known)


def test_an_answer_that_is_not_one_is_refused(tmp_path):
    confirmations = Confirmations(tmp_path / "relationships.json")

    with pytest.raises(ValueError, match="is not an answer"):
        confirmations.record(declared()[0], "probably")


@pytest.mark.parametrize("damage", ["{ not js", '{"answers": []}', "7"])
def test_answers_that_cannot_be_read_are_refused_rather_than_started_empty(tmp_path, damage):
    """These are answers a person gave by hand. Reading a damaged file as no answers at all
    would ask them the same questions again and then write over what was there."""
    path = tmp_path / "relationships.json"
    path.write_text(damage)

    with pytest.raises(Damaged) as refusal:
        Confirmations(path)

    assert str(path) in str(refusal.value)
    assert path.read_text() == damage, "the file a person would look at was left alone"


def test_an_answer_is_written_beside_the_file_and_moved_onto_it(tmp_path, monkeypatch):
    """An interrupted write must leave the last whole file rather than half of the next
    one, which is what writing in place could not promise. What is asserted is that the
    path a person reads is only ever reached by a move, and under a unique name, so two
    writes cannot hand each other half a file."""
    path = tmp_path / "relationships.json"
    known = graph(declared(), suggest(shop()))
    carriers = next(r for r in known if r.right_table == "vizmith.shop.carriers")
    moved: list[tuple[str, str]] = []
    replace = os.replace

    def watched(beside, target):
        moved.append((str(beside), str(target)))
        replace(beside, target)

    monkeypatch.setattr(os, "replace", watched)
    confirmations = Confirmations(path)
    confirmations.record(carriers, CONFIRMED)
    confirmations.record(carriers, REJECTED)

    assert [target for _, target in moved] == [str(path), str(path)]
    assert moved[0][0] != moved[1][0], "a fixed temporary name is one two writes share"
    assert list(tmp_path.iterdir()) == [path], "nothing was left beside the file"
    assert Confirmations(path).state(carriers) == REJECTED


def test_a_key_naming_its_own_table_suggests_nothing():
    """`order_id` on `orders` names the table it is already on, which is a column called
    what a key is called and not a join. Offering it would be a relationship from a table
    to itself, which resolves to no joins and reads as noise in the list."""
    columns = {
        "vizmith.shop.orders": {"id": "integer", "order_id": "integer"},
        "vizmith.shop.order_items": {"id": "integer", "order_id": "integer"},
    }

    assert [r.left_table for r in suggest(columns)] == ["vizmith.shop.order_items"]


def test_a_path_from_a_table_to_itself_is_no_joins_rather_than_a_refusal():
    """A field dropped from the table the query already reads. Nothing has to be joined for
    it, so the answer is an empty path and not a message about there being no relationship."""
    assert resolve(graph(declared(), suggest(shop())), "vizmith.shop.orders", "vizmith.shop.orders") == []
