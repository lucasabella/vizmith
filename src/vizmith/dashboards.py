"""Several specs saved together, arranged, and opened again.

A dashboard is a name, an ordered list of tiles, and the filters that apply across all of
them; a tile is a spec plus how wide it sits on the grid. Nothing else is in one — in
particular there is no stored result set, because rows
belong to the source and a stored copy of them is a number that stops being true without
saying so, and there is no stored option, because what a spec draws is the renderer's to
decide every time it draws it. A dashboard is therefore worth exactly what the specs in it
are worth, which is the artefact the rest of the design exists to produce.

Every tile is validated before it is stored, by the same validator a model's answer and a
person's paste go through. The alternative is a store that holds a spec nothing can draw
and only says so on the day somebody opens it, which moves a refusal from the save a person
is watching to an open they are not.

`Dashboards` is the file the server owns. It sits beside the relationship answers and the
profile cache and behaves like the first rather than the second: those are answers a person
gave and this is work a person did, so a file written under a shape this version does not
recognise is refused rather than dropped. A cache may throw itself away to save a bill. A
dashboard may not.
"""

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from vizmith.spec import validate_filters, validate_spec
from vizmith.state import stored, write

# The grid a dashboard is arranged on. A tile is one column or the whole width, which is
# the whole of the arrangement: two sizes and an order. A free canvas of pixel positions
# would be a second layout engine and a stored geometry that is wrong on the next screen.
COLUMNS = 2

# Opening a dashboard runs one statement per tile, against a warehouse that bills for each
# of them. A cap is what keeps the cost of an open something a person can see on the screen
# they are looking at.
TILE_LIMIT = 24

# A name is a heading in an interface and a key in a file, and it is what addresses the
# dashboard in a URL. Long enough for a sentence, and nothing in it that a line of text
# cannot hold.
NAME_LIMIT = 80


class Refused(ValueError):
    """What is wrong with a dashboard, in the shape the API already refuses in. It carries
    the list rather than one sentence, because a person who pasted two broken tiles should
    be told about both of them and the validator's own words are what say why."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Tile:
    """One chart on the grid: the spec that draws it, and how many columns it spans."""

    spec: dict
    width: int = 1

    def as_dict(self) -> dict:
        return {"spec": self.spec, "width": self.width}


@dataclass(frozen=True)
class Dashboard:
    """A name, the tiles under it in the order they are drawn in, and the filters that
    apply across all of them. The order is the list's own, because an arrangement that is a
    set of positions can disagree with itself and a list cannot.

    `filters` is the one thing here that is not per tile. It is stored beside the tiles
    rather than written into their specs: a tile holds the question somebody built, and a
    dashboard filter is a narrowing of the whole page that has to be removable without
    leaving a trace in a spec nobody edited. Applying it is the interface's job, at the
    moment a tile runs, and the narrowed spec goes through the same validator every other
    spec does."""

    name: str
    tiles: tuple[Tile, ...]
    filters: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "tiles": [tile.as_dict() for tile in self.tiles],
            "filters": list(self.filters),
        }


def review(name: str, tiles: Sequence[object], filters: object = ()) -> list[str]:
    """Everything wrong with a dashboard about to be stored, as sentences. Empty means it
    can be saved.

    A tile is named by its position, counting from one, because that is where it sits on
    the screen the person is looking at and a tile carries no other identity. The
    validator's messages are passed through word for word: they are written to be read by
    whoever has to fix the spec, and rewording them here would make the same spec fail
    differently depending on which endpoint refused it."""
    errors = _name_errors(name)
    if not tiles:
        errors.append("a dashboard holds at least one tile")
    if len(tiles) > TILE_LIMIT:
        errors.append(
            f"a dashboard holds at most {TILE_LIMIT} tiles, and this one holds {len(tiles)}, "
            f"which is a statement per tile every time it is opened"
        )
    for position, tile in enumerate(tiles, start=1):
        errors.extend(f"tile {position}: {error}" for error in _tile_errors(tile))
    # A tuple where this was called from code and a list where it came off the wire. The
    # schema judges a JSON array, and a tuple is not one.
    errors.extend(validate_filters(list(filters) if isinstance(filters, list | tuple) else filters))
    return errors


def _name_errors(name: object) -> list[str]:
    if not isinstance(name, str) or name.strip() == "":
        return ["a dashboard is saved under a name, and this one has none"]
    if len(name) > NAME_LIMIT:
        return [f"a name is at most {NAME_LIMIT} characters, and this one is {len(name)}"]
    if name != name.strip():
        return ["a name carries no leading or trailing space, since two names that differ by one read as one name"]
    # A slash would address something else once the name is in a URL, and a control
    # character is not something a heading can show. Both are refused by naming the
    # character rather than by quietly rewriting the name a person typed.
    for character in name:
        if character == "/" or ord(character) < 0x20 or ord(character) == 0x7F:
            return [f"a name cannot hold {character!r}, because it is what addresses the dashboard"]
    return []


def _tile_errors(tile: object) -> list[str]:
    if not isinstance(tile, dict):
        return ["a tile is an object holding a spec and a width"]
    if "spec" not in tile:
        return ["a tile holds a spec, which is what draws it"]
    width = tile.get("width", 1)
    errors = validate_spec(tile["spec"])
    if width not in range(1, COLUMNS + 1) or isinstance(width, bool):
        errors.append(f"a tile spans 1 to {COLUMNS} columns, and this one says {width!r}")
    return errors


class Dashboards:
    """The dashboards a person has saved, in a file the server owns.

    Kept in the state directory beside the relationship answers, for the same reason: work
    a person did cannot live in a process that ends. The whole set is written at once and
    moved into place, so a reader arriving mid save finds the last whole file rather than
    half of the next one.

    Read on construction rather than held open, because the API builds one of these per
    request and a copy kept between them is one more thing that can serve a stale answer.
    A file that cannot be read raises instead of starting empty: an unreadable cache costs
    one more profile, and an unreadable dashboard file that quietly became an empty one
    would be answered by a save that overwrites everything in it.

    Two saves that overlap can still lose one of the two, since each of these read the file
    when it was built. Vizmith serves one person's own browser, so the two saves are that
    person's, and the fix for it is a lock across processes for a race a local application
    does not have."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._stored: dict[str, dict] = stored(path, "dashboards")

    def names(self) -> list[str]:
        """Every saved name, sorted, so a list of them is in the same order twice running
        whatever order they were saved in."""
        return sorted(self._stored)

    def read(self, name: str) -> Dashboard | None:
        """One dashboard, or None where nothing is saved under that name. The caller is
        what turns that into a 404, since a missing dashboard is not an error down here."""
        stored = self._stored.get(name)
        if stored is None:
            return None
        return Dashboard(
            name=name,
            tiles=tuple(Tile(spec=tile["spec"], width=tile.get("width", 1)) for tile in stored["tiles"]),
            # Absent in a file written before a dashboard could hold one, which is the
            # ordinary case and not a broken file: no filter across the tiles is what
            # every dashboard saved until now meant.
            filters=tuple(stored.get("filters", [])),
        )

    def save(self, name: str, tiles: Sequence[object], filters: Sequence[object] = ()) -> Dashboard:
        """Store a dashboard under a name, replacing whatever was there.

        The review runs here rather than in the caller, so that there is no way to reach
        the file with a spec nothing judged. A save under an existing name replaces it,
        because that is what saving the thing on screen means, and a name that has to
        survive is one a person types differently."""
        errors = review(name, tiles, filters)
        if errors:
            raise Refused(errors)
        saved = Dashboard(
            name=name,
            tiles=tuple(
                Tile(spec=tile["spec"], width=tile.get("width", 1))  # type: ignore[union-attr]
                for tile in tiles
            ),
            filters=tuple(filters),  # type: ignore[arg-type]
        )
        with self._lock:
            self._stored[name] = {
                "tiles": [tile.as_dict() for tile in saved.tiles],
                "filters": list(saved.filters),
            }
            self._write()
        return saved

    def delete(self, name: str) -> bool:
        """Forget one, reporting whether there was one to forget."""
        with self._lock:
            if name not in self._stored:
                return False
            del self._stored[name]
            self._write()
        return True

    def _write(self) -> None:
        write(self._path, json.dumps({"dashboards": self._stored}, indent=2, sort_keys=True))
