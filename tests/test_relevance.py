"""Which tables a question is about, and what the prompt is allowed to cost.

Every test here is offline: selection reads names, column names and row counts, which the
profiles already hold, and asks nothing of a model or a source.
"""

import json
from pathlib import Path

import pytest
from conftest import FixtureCatalog

from vizmith.ask import prompt
from vizmith.catalog import DECLARED, Relationship
from vizmith.profiler import ColumnProfile, TableProfile, profile_table
from vizmith.relevance import BUDGET, FLOOR, select

QUESTIONS = Path(__file__).parent / "fixtures" / "evals" / "questions.json"


@pytest.fixture(scope="module")
def schema(fixture_db):
    """Every fixture table, profiled once, which is what the API hands `ask`."""
    catalog = FixtureCatalog(fixture_db)
    return tuple(profile_table(catalog, name) for name in catalog.tables())


@pytest.fixture(scope="module")
def confirmed(fixture_db):
    return FixtureCatalog(fixture_db).relationships()


def names(selection) -> set[str]:
    return {table.table.rsplit(".", 1)[-1] for table in selection.tables}


def test_a_question_gets_the_tables_it_names(schema, confirmed):
    chosen = select("How many orders per country did customers place?", schema, confirmed)

    assert {"orders", "customers"} <= names(chosen)


def test_a_question_gets_a_table_its_columns_name(schema, confirmed):
    """The question says `carrier` and nothing says `shipments`, but the shipment table is
    the one carrying a carrier id, and a column name is what says so."""
    chosen = select("What did each carrier cost to ship with?", schema, confirmed)

    assert "carriers" in names(chosen)
    assert "shipments" in names(chosen)


def test_a_table_a_join_has_to_go_through_is_readable(schema, confirmed):
    """`order_items` sits between `orders` and `products` and shares a word with neither.
    A spec may only join through a confirmed relationship, so a selection that dropped the
    bridge would leave a correct answer unwritable."""
    chosen = select("Which product category produced the most revenue?", schema, confirmed)

    assert "products" in names(chosen)
    assert "order_items" in names(chosen), "the bridge a join needs was left out"


def test_a_question_that_matches_nothing_still_gets_tables_to_read(schema, confirmed):
    """A schema whose names share no vocabulary with the question is exactly where a model
    needs something to read. Sending nothing would be a prompt that cannot be answered."""
    chosen = select("Wie hat den grössten Umsatz?", schema, confirmed)

    assert len(chosen.tables) == FLOOR
    assert chosen.withheld == len(schema) - FLOOR


def test_the_prompt_is_bounded_however_large_the_schema(confirmed):
    """The failure at the far end: past some size the request stops fitting in a context
    window at all, and arrives as whatever the endpoint says about an oversized request."""
    wide = tuple(
        TableProfile(
            table=f"vizmith.shop.table_{index}",
            row_count=1_000,
            columns=tuple(
                ColumnProfile(
                    name=f"revenue_column_{column}",
                    type="decimal",
                    null_rate=0.0,
                    distinct_count=10,
                    distinct_count_exact=True,
                    minimum="1",
                    maximum="2",
                    samples=(),
                )
                for column in range(30)
            ),
        )
        for index in range(150)
    )

    chosen = select("revenue per column", wide)

    assert len(chosen.tables) < len(wide)
    assert chosen.withheld == len(wide) - len(chosen.tables)
    written = prompt("revenue per column", chosen.tables, withheld=chosen.withheld)
    assert len(written) < BUDGET + 12_000, "the schema half of the prompt is not bounded"


def test_one_table_is_sent_even_where_it_does_not_fit(confirmed):
    """A budget that sent nothing would be a prompt with no schema in it, which is worse
    than one table over the line."""
    enormous = TableProfile(
        table="vizmith.shop.wide",
        row_count=1,
        columns=tuple(
            ColumnProfile(
                name=f"column_{index}",
                type="string",
                null_rate=0.0,
                distinct_count=1,
                distinct_count_exact=True,
                minimum=None,
                maximum=None,
                samples=(),
            )
            for index in range(2_000)
        ),
    )

    chosen = select("anything at all", (enormous,))

    assert len(chosen.tables) == 1
    assert chosen.withheld == 0


def test_a_whole_schema_that_fits_is_sent_whole(schema, confirmed):
    """Eight fixture tables fit several times over, so nothing is withheld and the prompt
    is what it was before selection existed."""
    chosen = select("revenue per country per month by carrier and product category", schema, confirmed)

    assert chosen.withheld >= 0
    assert len(chosen.tables) <= len(schema)


def test_the_tables_keep_the_schema_s_order(schema, confirmed):
    """The ranking is this file's reasoning. A prompt that reordered the schema per question
    would make two prompts differ by more than the tables they hold, which is a prefix a
    provider's cache cannot share and a diff nobody can read."""
    chosen = select("revenue per country", schema, confirmed)
    order = [table.table for table in schema if table in chosen.tables]

    assert [table.table for table in chosen.tables] == order


def test_the_prompt_says_a_subset_is_a_subset(schema):
    """A model handed four tables and told nothing reads them as the whole source, and
    answers a question about a fifth by inventing it."""
    chosen = select("Wie hat den grössten Umsatz?", schema)
    written = prompt("Wie hat den grössten Umsatz?", chosen.tables, withheld=chosen.withheld)

    assert f"{chosen.withheld} more" in written
    assert "rather than inventing one" in written


def test_every_eval_question_still_gets_the_tables_its_answer_needs(schema, confirmed):
    """The question set is what says a prompt change is safe, and this is the half of that
    which needs no model: an answer cannot reference a table the prompt did not carry, so a
    question whose tables were selected away would fail layer two whatever the model said."""
    for entry in json.loads(QUESTIONS.read_text())["questions"]:
        chosen = select(entry["question"], schema, confirmed)
        missing = set(entry["tables"]) - names(chosen)

        assert not missing, f"{entry['name']} lost {sorted(missing)}"


def test_a_suggestion_nobody_confirmed_does_not_make_room_for_a_table(schema):
    """Reach is what a join may actually go through. A suggested relationship is not a join
    until a person confirms it, so a table only a suggestion reaches is not one this makes
    room for either."""
    invented = [
        Relationship(
            "vizmith.shop.orders", "customer_id", "vizmith.shop.carriers", "id", kind=DECLARED
        )
    ]

    with_reach = select("orders placed", schema, invented)
    without = select("orders placed", schema, ())

    assert "carriers" in names(with_reach)
    assert "carriers" not in names(without) or len(without.tables) == FLOOR
