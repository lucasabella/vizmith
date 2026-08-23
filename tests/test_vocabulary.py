"""What the selector does when the question's words are not the schema's.

The offline half of #121. The strategy that would bridge this gap — a synonym index, a
model-assisted first call, embeddings — is deliberately not chosen here: that choice is
gated on the live baseline #61 records, and picking one before any number exists is how
a guess becomes load-bearing. What is here is the measurement that choice will be made
against.

Two things are pinned down. First, the behaviour itself: for every vocabulary-mismatched
question, which tables the selector chose, whether any word of the question matched
anything, and whether the tables the answer needs survived — recorded in a committed file
so the current failure mode is inspectable rather than folklore. Second, that the record
stays true: it is regenerated deterministically from the fixture schema and compared,
the way the fixture Parquet is.
"""

import json
import os
from pathlib import Path

import pytest
from conftest import FixtureCatalog

from vizmith.evals import questions as question_set
from vizmith.profiler import profile_table
from vizmith.relevance import select

SET = Path(__file__).parent / "fixtures" / "evals" / "questions-vocabulary.json"
RECORD = Path(__file__).parent / "fixtures" / "evals" / "vocabulary-record.json"


@pytest.fixture(scope="module")
def schema(fixture_db):
    """Every fixture table, profiled once, which is what the prompt path is handed."""
    catalog = FixtureCatalog(fixture_db)
    return tuple(profile_table(catalog, name) for name in catalog.tables())


@pytest.fixture(scope="module")
def confirmed(fixture_db):
    return FixtureCatalog(fixture_db).relationships()


def short(selection) -> set[str]:
    return {table.table.rsplit(".", 1)[-1] for table in selection.tables}


def measured(schema, confirmed) -> list[dict]:
    """What the selector does today, per vocabulary-mismatched question."""
    out = []
    for entry in question_set(SET):
        chosen = select(entry.question, schema, confirmed)
        got = short(chosen)
        out.append(
            {
                "name": entry.name,
                "needs": list(entry.tables),
                "chosen": sorted(got),
                "words_matched": chosen.words_matched,
                "missing": sorted(set(entry.tables) - got),
            }
        )
    return out


def test_the_record_says_what_the_selector_does_today(schema, confirmed):
    """Committed so the current failure mode is readable without running anything, and
    compared so it cannot go stale silently. A change to selection that moves these
    numbers rewrites them on purpose, with the diff saying what moved:
    `VIZMITH_REGENERATE_RECORD=1 pytest tests/test_vocabulary.py`."""
    fresh = {"recorded": measured(schema, confirmed)}

    if os.environ.get("VIZMITH_REGENERATE_RECORD") == "1":
        RECORD.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n")

    held = json.loads(RECORD.read_text())
    assert held == fresh


def test_at_least_one_question_selects_away_a_table_its_answer_needs(schema, confirmed):
    """This is the failure #121 names, recorded rather than asserted away: some business
    word for a thing the schema holds selects nothing that reaches the table holding it.
    If a future strategy makes this test fail by passing, that is the day the strategy
    landed, and the record above is what shows which questions it moved."""
    held = {entry["name"]: entry for entry in json.loads(RECORD.read_text())["recorded"]}
    wounded = [
        name
        for name, entry in held.items()
        if set(entry["needs"]) - set(entry["chosen"])
    ]

    assert wounded, "every mismatched question selected its tables; #121's premise is gone"


def test_a_question_whose_words_match_nothing_still_gets_something_to_read(schema, confirmed):
    """The floor is the reason a mismatched question is answerable at all: the model sees
    four real tables rather than an empty prompt, and the prompt says a subset is a
    subset. Whatever strategy replaces the floor has to keep this property."""
    for entry in measured(schema, confirmed):
        if not entry["words_matched"]:
            assert entry["chosen"], f"{entry['name']} was sent nothing"
