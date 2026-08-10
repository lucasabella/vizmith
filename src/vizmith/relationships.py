"""The relationship graph a join path is resolved against.

Three parts, and they are deliberately separate. The catalog reports what the source
declares. `suggest` infers the rest from column names and types. `Confirmations` records
what a person answered about a suggestion, on disk, because an answer that does not
survive a restart is one that gets asked again every morning.

Only declared and confirmed relationships resolve. A suggestion is never silently
promoted: a wrong join produces a plausible number rather than an error, which is the
failure the whole design exists to avoid.

Inference is conservative on purpose. A suggestion needs a name that reads as a key and a
type that matches, and anything weaker is not offered at all, because a person confirming
a list of ten bad suggestions stops reading it at the third.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from vizmith.catalog import DECLARED, SUGGESTED, Relationship
from vizmith.state import stored, write

# What a foreign key column is called, everywhere this looks. `customer_id` names
# `customers`, and nothing without this suffix is guessed at.
KEY_SUFFIX = "_id"

# An answer to a suggestion. Open means it was neither, which is what every suggestion
# starts as and what un-confirming returns one to.
CONFIRMED = "confirmed"
REJECTED = "rejected"
OPEN = "open"


def suggest(columns: Mapping[str, Mapping[str, str]]) -> list[Relationship]:
    """Relationships inferred from names and types, given every table's columns keyed by
    qualified table name. Never declared, never confirmed: what comes back here is a
    question for a person.

    A column named `<stem>_id` names the table whose own name is that stem, singular or
    plural, and it has to point at a key column of the same type on that table. Both
    halves are required. A name match with a different type is a column that happens to
    be called the same thing, and offering it is how a person learns to click Confirm
    without reading."""
    by_stem: dict[str, str] = {}
    for table in columns:
        by_stem.setdefault(_stem(table), table)

    found = []
    for table, types in columns.items():
        for column, type_ in types.items():
            if not column.endswith(KEY_SUFFIX):
                continue
            parent = by_stem.get(column[: -len(KEY_SUFFIX)])
            if parent is None or parent == table:
                continue
            key = _key_column(columns[parent], type_)
            if key is not None:
                found.append(Relationship(table, column, parent, key, kind=SUGGESTED))
    return sorted(found)


def graph(declared: Sequence[Relationship], suggested: Sequence[Relationship]) -> list[Relationship]:
    """Everything known about the source's relationships, with what it declares winning.
    A source that states a foreign key has answered the question a suggestion asks, so
    the suggestion is dropped rather than offered next to the fact."""
    facts = {relationship.key: relationship for relationship in declared}
    return sorted(facts.values()) + sorted(
        relationship for relationship in suggested if relationship.key not in facts
    )


def resolve(relationships: Sequence[Relationship], left: str, right: str) -> list[Relationship]:
    """The shortest path between two tables, as the relationships it walks through.

    Raises rather than choosing where there is nothing to choose from honestly. Two paths
    of the same length are an ambiguity, and picking one of them would answer a question
    nobody can see was asked. No path at all names both tables, because that message ends
    up in front of a person who dragged a field into a well.

    Only what is given here resolves, so a caller that passes unconfirmed suggestions has
    decided to join on a guess."""
    if left == right:
        return []

    edges: dict[str, list[tuple[str, Relationship]]] = {}
    for relationship in relationships:
        edges.setdefault(relationship.left_table, []).append((relationship.right_table, relationship))
        edges.setdefault(relationship.right_table, []).append((relationship.left_table, relationship))

    # Breadth first, one whole layer at a time, so that every path of the shortest length
    # is in hand before one of them is returned. Stopping at the first arrival would hide
    # the second one, which is the ambiguity this has to report.
    seen = {left}
    frontier: dict[str, list[list[Relationship]]] = {left: [[]]}
    while frontier:
        arrived = [path for table, walked in frontier.items() if table == right for path in walked]
        if arrived:
            if len(arrived) > 1:
                raise ValueError(
                    f"'{left}' and '{right}' are connected by {len(arrived)} confirmed paths of "
                    f"the same length, so Vizmith cannot choose a join safely."
                )
            return arrived[0]

        following: dict[str, list[list[Relationship]]] = {}
        for table, walked in frontier.items():
            for next_table, relationship in edges.get(table, []):
                if next_table in seen:
                    continue
                following.setdefault(next_table, []).extend(path + [relationship] for path in walked)
        seen.update(following)
        frontier = following

    raise ValueError(
        f"No confirmed relationship connects '{left}' to '{right}', directly or through "
        f"another table."
    )


class Confirmations:
    """What a person answered about a suggestion, kept in a file the server owns.

    Beside the profile it belongs to rather than in the source, because a person's answer
    about a source they read is not something they can necessarily write back to it. It
    survives a re-profile by construction: nothing here is derived from a profile, and a
    relationship is keyed by the columns it joins rather than by where it sat in a list.

    Written whole and moved into place, like the dashboards and the profile cache: an
    interrupted write here would truncate answers a person gave by hand, and the cost of
    losing them is being asked the same questions again. A file that cannot be read is
    refused rather than started empty, naming itself so it can be moved aside."""

    def __init__(self, path: Path):
        self._path = path
        self._answers: dict[str, str] = stored(path, "answers")

    def state(self, relationship: Relationship) -> str:
        """A declared relationship is confirmed by the source itself, and a person is not
        asked to approve a foreign key."""
        if relationship.kind == DECLARED:
            return CONFIRMED
        return self._answers.get(relationship.key, OPEN)

    def record(self, relationship: Relationship, answer: str) -> None:
        """Answer one suggestion. `OPEN` is the way back from a confirmation, because a
        column of Confirm buttons with no undo turns one mis-click into a wrong join."""
        if answer not in (CONFIRMED, REJECTED, OPEN):
            raise ValueError(f"'{answer}' is not an answer, which is one of {CONFIRMED}, {REJECTED}, {OPEN}")
        if answer == OPEN:
            self._answers.pop(relationship.key, None)
        else:
            self._answers[relationship.key] = answer
        self._write()

    def offered(self, relationships: Sequence[Relationship]) -> list[Relationship]:
        """What is worth showing a person: everything except the suggestions they have
        already turned down. A rejected suggestion that came back would be the same
        question asked every time the source is profiled."""
        return [r for r in relationships if self.state(r) != REJECTED]

    def usable(self, relationships: Sequence[Relationship]) -> list[Relationship]:
        """What a join path may be resolved through. Declared, or confirmed by a person."""
        return [r for r in relationships if self.state(r) == CONFIRMED]

    def _write(self) -> None:
        write(self._path, json.dumps({"answers": self._answers}, indent=2, sort_keys=True))


def _stem(table: str) -> str:
    """What a foreign key column would call this table: its last segment, singular. A
    plural is stripped of one `s`, which covers `customers` and `shipment_scans` and gets
    a table called `status` wrong. That costs a suggestion nobody was offered rather than
    a join nobody checked."""
    name = table.rsplit(".", 1)[-1]
    return name.removesuffix("s")


def _key_column(columns: Mapping[str, str], type_: str) -> str | None:
    """The column a key points at: `id`, of the same type as the key naming it. Nothing
    else counts, because a table whose key is called something else is a table this
    cannot infer anything about, and it says so by offering nothing."""
    return "id" if columns.get("id") == type_ else None
