"""Where a spec may read, held to by every catalog rather than by one of them.

`api.py` opens with the sentence this file exists to keep: a request carries a spec and
nothing else, the data source is server configuration, so a client cannot name a database.
A spec is hand editable by design, so the name in it is the one name in the system that
came from outside, and what stops it naming somewhere else is `Scope`.

The rule used to be a method on `DatabricksCatalog` that nothing above it knew about. It
reached a spec only because `describe` happened to call it on the way in, the protocol did
not mention it, and no test ever asked a catalog to refuse anything — so the second
implementation in the repository, the fixture catalog the whole suite runs against, took
the last segment of whatever it was given and rebuilt the name against the schema that does
exist. That is the shape of the failure this file is written against: not a source that
refuses wrongly, but one that answers a question nobody was allowed to ask.

Every test here that can be is run against each catalog, because a contract one source is
checked against is a contract the other is free to break. The live half skips without
credentials, the way the rest of the live suite does.
"""

import json
from pathlib import Path

import pytest
from conftest import CATALOG, SCHEMA, needs_warehouse

from vizmith.catalog import Scope
from vizmith.query import build

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
REVENUE_BY_COUNTRY = FIXTURES / "valid" / "revenue_by_country.json"

# The two levels a lakehouse table sits under, as this repository's fixtures spell them.
# A source with one level rather than two is a shape the record already takes, and it is
# asserted below rather than waited for: PostgreSQL is a schema and a table.
SHOP = Scope(levels=("catalog", "schema"), values=("vizmith", "shop"))


# The same table this server does read, named in a database it does not. The last segment
# is deliberately the one that exists, because that is the reachable case: a column
# qualifier still matches, the spec still validates, and what refuses it is the scope
# rather than anything about the shape of the query.
ELSEWHERE = "hr.people.orders"


def elsewhere(spec: dict, name: str = ELSEWHERE) -> dict:
    """The same spec, reading from somewhere this server was not pointed at."""
    moved = json.loads(json.dumps(spec))
    moved["query"]["from"] = name
    return moved


def test_a_shorter_name_is_filled_in_from_what_is_configured():
    """Filling in is the convenience half. A spec may name a table with fewer segments than
    the source uses, and the configured values are what the missing ones are."""
    assert SHOP.qualify("orders") == "vizmith.shop.orders"
    assert SHOP.qualify("shop.orders") == "vizmith.shop.orders"
    assert SHOP.qualify("vizmith.shop.orders") == "vizmith.shop.orders"


@pytest.mark.parametrize(
    "name",
    ["hr.shop.salaries", "vizmith.hr.salaries", "hr.people.salaries"],
)
def test_a_name_outside_what_is_configured_is_refused(name):
    """The boundary half. A three segment name used to be taken at its word, which made the
    settings describe where short names resolve rather than where a spec may read."""
    with pytest.raises(ValueError, match="outside the configured schema"):
        SHOP.qualify(name)


def test_the_refusal_says_where_this_server_does_read():
    """A person hand editing a spec is the likeliest caller to meet this, so the message
    says what to write instead rather than only that this was wrong."""
    with pytest.raises(ValueError) as refused:
        SHOP.qualify("hr.people.salaries")

    assert "vizmith.shop" in str(refused.value)


def test_more_segments_than_a_table_name_has_is_refused():
    with pytest.raises(ValueError, match="at most catalog.schema.table"):
        SHOP.qualify("a.b.c.d")


def test_a_source_with_one_level_refuses_in_its_own_words():
    """The record carries what the source calls its levels only so that a refusal reads in
    them. A source with a schema and no catalog — PostgreSQL, and it is on the list — is
    the shape this has to take without a second implementation of the rule."""
    public = Scope(levels=("schema",), values=("public",))

    assert public.qualify("orders") == "public.orders"
    assert public.qualify("public.orders") == "public.orders"
    with pytest.raises(ValueError, match="at most schema.table"):
        public.qualify("a.b.c")
    with pytest.raises(ValueError, match="outside the configured schema"):
        public.qualify("hr.salaries")


def test_a_source_names_its_levels_in_its_own_refusal():
    """BigQuery has a project and a dataset rather than a catalog and a schema, and a
    message that called them something else would send somebody looking for a setting that
    is not there."""
    dataset = Scope(levels=("project", "dataset"), values=("acme", "shop"))

    with pytest.raises(ValueError, match="at most project.dataset.table"):
        dataset.qualify("a.b.c.d")
    with pytest.raises(ValueError, match="outside the configured dataset"):
        dataset.qualify("other.shop.orders")


def test_a_spec_naming_another_database_is_refused_before_the_source_is_asked(catalog):
    """The one that would have caught this. The refusal is the builder's now rather than a
    property of whichever `describe` a source happened to write, so the fixture catalog is
    held to it exactly as the workspace is — and it is held to it without being able to
    answer, because nothing reaches the source at all."""
    moved = elsewhere(json.loads(REVENUE_BY_COUNTRY.read_text()))
    catalog.statements.clear()
    catalog.described.clear()

    with pytest.raises(ValueError, match="outside the configured schema"):
        build(moved, catalog)

    assert catalog.statements == [], "an out of scope spec reached the source"
    assert catalog.described == [], "an out of scope name was sent to the source to resolve"


@needs_warehouse
def test_a_spec_naming_another_database_is_refused_against_the_workspace_too(live_catalog):
    """The same spec against the source a user actually configures. This one always
    refused; what is new is that the suite says so."""
    moved = elsewhere(json.loads(REVENUE_BY_COUNTRY.read_text()))

    with pytest.raises(ValueError, match="outside the configured schema"):
        build(moved, live_catalog)


def test_a_spec_naming_the_configured_schema_in_full_still_builds(catalog):
    """The refusal has to be about the values rather than about the number of segments: a
    spec that spells out where it reads is the ordinary output of the ask path."""
    spec = json.loads(REVENUE_BY_COUNTRY.read_text())

    sql, _ = build(spec, catalog)

    assert '"vizmith"."shop"."orders"' in sql


@needs_warehouse
def test_both_catalogs_carry_the_same_two_levels(live_catalog, catalog):
    """A connector owes the scope its configuration makes, and nothing else. Two sources
    that named their levels differently would refuse the same spec with two messages."""
    assert catalog.scope.levels == live_catalog.scope.levels == ("catalog", "schema")
    assert live_catalog.scope.values == (CATALOG, SCHEMA)
