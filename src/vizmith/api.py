"""The HTTP surface: a spec goes in, its errors or its rows come back.

What a client can also ask for is metadata: the tables in the configured schema and the
profile of one of them, which is the figures the model was given and never a row out of a
table. The profiler's sample threshold is the boundary that keeps that true, and nothing
here widens it. And a second opinion on a spec, which comes back as findings and a spec
beside the one that was sent: nothing here applies one, so a suggestion nobody took has
changed nothing and cost no query.

A request carries a spec and nothing else. The data source is server configuration, so a
client cannot name a database. The artefact a client holds is the spec, which is the point
of the whole design. A source's own error message is passed on even where it quotes the
statement that failed, because the person reading it asked for that query and withholding
the only clue protects nothing.

Two things here write, and both write specs or answers about them rather than configuration:
a person's answer about a suggested relationship, and a dashboard, which is several specs
saved under a name. Neither can point the server at data, which is the sentence the read
only surface existed to keep.

Validator messages are returned word for word. They are written to be fed back to a model
on retry, so rewording them here would break that loop before it is written.
"""

import json
import os
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vizmith import __version__, query
from vizmith.ask import SCHEMA, Step, asking
from vizmith.catalog import (
    DECLARED,
    METADATA_WORKERS,
    UNSUPPORTED,
    Catalog,
    Held,
    Relationship,
)
from vizmith.config import kind, source_settings, state_dir
from vizmith.critique import critique
from vizmith.dashboards import Dashboards, Refused
from vizmith.model import Endpoint, Model, ModelError
from vizmith.profiler import Profiles, TableProfile
from vizmith.rationing import MODEL, QUERY, Exhausted, Rations
from vizmith.relationships import Confirmations, graph, resolve, suggest
from vizmith.sources import build
from vizmith.spec import validate_spec
from vizmith.state import Damaged


def _web() -> Path:
    """The built interface. Inside a wheel it sits in the package, put there at build time;
    in a checkout it is `web/dist`, where `npm run build` leaves it. The packaged copy wins,
    because an installed Vizmith run from a checkout's directory should serve what it
    shipped rather than whatever happens to be built there."""
    packaged = Path(__file__).resolve().parent / "web"
    return packaged if packaged.is_dir() else Path(__file__).resolve().parents[2] / "web" / "dist"


WEB_DIST = _web()

MODEL_CONFIGURATION = (
    "VIZMITH_MODEL_BASE_URL",
    "VIZMITH_MODEL_NAME",
    "VIZMITH_MODEL_KEY",
)

app = FastAPI(title="Vizmith")

# The names that mean this machine. A browser reaches the server through one of these or
# it is not the person who started it.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _allowed_hosts() -> frozenset[str]:
    """Which host names this server will answer to.

    Loopback always, and whatever `VIZMITH_ALLOWED_HOSTS` adds. That variable is the way
    out for somebody serving on a real interface on purpose: binding elsewhere does not
    say which name clients will use to arrive, so it cannot be inferred from `--host` and
    has to be stated. Nothing over HTTP writes it, the same as the rest of the settings.
    """
    named = os.environ.get("VIZMITH_ALLOWED_HOSTS", "")
    return LOOPBACK | frozenset(name.strip().lower() for name in named.split(",") if name.strip())


def _hostname(value: str | None) -> str | None:
    """The host out of a `Host` header or an `Origin`, without its port or its brackets.

    Both go through `urlsplit` so that `[::1]:8000` and `http://[::1]:8000` reduce to the
    same thing, and so an `Origin` of `null`, which is what a sandboxed frame sends, comes
    back as nothing rather than as a name that might match one."""
    if not value:
        return None
    prefix = "" if "//" in value else "//"
    try:
        return urlsplit(f"{prefix}{value}").hostname
    except ValueError:
        return None


@app.middleware("http")
async def only_this_machine(request: Request, call_next):
    """Refuse anything that did not arrive addressed to this machine, by this machine.

    There is no authentication on this API, and until there is, these two headers are what
    stands between a warehouse and any page the person happens to have open in another tab.

    The `Host` check is what stops DNS rebinding. Same origin policy keys on the name in
    the URL rather than on the address it resolves to, so a page on a name that has been
    repointed at 127.0.0.1 can read our answers as same origin, and no CORS setting is
    consulted because nothing about it is cross origin any more. What that request cannot
    do is claim to be addressed to `localhost`, because the browser sends the name it
    dialled. So the name is what we check.

    The `Origin` check is what stops the writes. A cross site POST carrying `text/plain`
    is a simple request, needs no preflight, and Starlette parses the body as JSON without
    consulting the content type, so a page that cannot read the answer can still spend a
    warehouse bill or confirm a relationship nobody confirmed. It cannot forge `Origin`.
    Absent is allowed, because a same origin GET does not always carry one; present and
    foreign is refused.
    """
    allowed = _allowed_hosts()
    if _hostname(request.headers.get("host")) not in allowed:
        return JSONResponse(
            status_code=403,
            content={
                "errors": [
                    (
                        "This server answers only to localhost. Reach it at "
                        "http://127.0.0.1:8000, or name the host in VIZMITH_ALLOWED_HOSTS."
                    )
                ]
            },
        )
    origin = request.headers.get("origin")
    if origin is not None and _hostname(origin) not in allowed:
        return JSONResponse(
            status_code=403,
            content={"errors": [f"A request from {origin} is not one this server answers."]},
        )
    return await call_next(request)


@app.exception_handler(Damaged)
def damaged(request: Request, failure: Damaged) -> JSONResponse:
    """A state file the server cannot read, in the shape every other refusal arrives in.

    It is registered here rather than caught in each endpoint because the stores are built
    as dependencies, so the failure happens before any endpoint body runs. 503 rather than
    500: the request was well formed, nothing is wrong with what was asked, and the endpoint
    can answer again as soon as the file named in the message is moved aside."""
    return JSONResponse(status_code=503, content={"errors": [str(failure)]})


@app.exception_handler(Exhausted)
def exhausted(request: Request, failure: Exhausted) -> JSONResponse:
    """A ration that ran out, in the shape every other refusal arrives in.

    429 with `Retry-After`, which is what a client that means well reads and backs off on.
    Registered here rather than raised as an `HTTPException` so that the body is the same
    `errors` list the validator and the source refusals use: the interface has one way to
    show a refusal, and a second shape would be a second way."""
    return JSONResponse(
        status_code=429,
        content={"errors": [str(failure)], "spoke": "rations"},
        headers={"Retry-After": str(failure.retry_after)},
    )


@lru_cache
def rations() -> Rations:
    """What is left of what the costed endpoints may spend, for the life of the process.

    One object rather than one per request, which is the whole point of it, and built on
    first use rather than at import so that a test can put its own clock in front of it and
    so that the environment `serve` sets is read after `serve` has set it."""
    return Rations()


def rationed(what: str):
    """A dependency for an endpoint that costs something: a token now, a slot for as long
    as it runs.

    The slot is released in a `finally` on the far side of the `yield`, which FastAPI runs
    after the endpoint has returned, so a handler that raised still gives its slot back. The
    token is taken first and is not returned: what a bucket rations is requests made, and a
    request that failed at the source was still paid for."""

    def ration(request: Request):
        allowance = rations()
        client = request.client.host if request.client else "unknown"
        allowance.spend(client, what)
        allowance.enter()
        try:
            yield
        finally:
            allowance.leave()

    return ration


@lru_cache
def source() -> Catalog:
    """The configured source, built once and on first use rather than at import, so that
    health answers on a server that has none and a test can put its own in its place.

    Built once is also what lets `Held` mean anything: the window it holds a freshness
    answer for belongs to the source object, so a source rebuilt per request would hold
    nothing. What is held is bounded by a number in `catalog.py` rather than by the life of
    the process, which is what the profile cache on disk exists to refuse.

    Which kind of source is configuration and never a request, which is the sentence this
    module opens with. `sources.build` is the only place a name in a settings file becomes
    a catalog, and it is handed the values the chosen kind asks for and no others."""
    return Held(build(kind(), [os.environ[name] for name in source_settings()]))


@lru_cache
def model() -> Model:
    base_url, name, key = (os.environ[variable] for variable in MODEL_CONFIGURATION)
    return Model(Endpoint(base_url=base_url, model=name, api_key=key))


@lru_cache(maxsize=1)
def constrains(writer: Model) -> bool:
    """Whether this endpoint honours the schema a question will be asked with, asked once
    and kept for the life of the process. The probe carries that schema rather than a
    simpler one, because an endpoint can take a simple schema and refuse this one. The
    probe is a billed request, so asking per question would pay for the same answer every
    time. A probe that never got an answer raises rather than reporting no, and nothing is
    remembered, so the next question asks again."""
    return writer.constrains_output(SCHEMA)


# Profiling one table is two statements that mostly wait, so the schema is profiled several
# at a time. Wide enough that the waiting overlaps, narrow enough that the warehouse is not
# handed more concurrent statements than a small one will run, which only moves the queue.
PROFILE_WORKERS = 8


def profiles(catalog: Catalog) -> tuple[TableProfile, ...]:
    """Every table in the configured schema, read through the profile cache.

    The cache is what keeps this affordable: profiling a table is two passes over it, and
    a table that has not changed since the last one is answered out of the file instead.
    What is paid on every call is one metadata read per table asking the source when the
    table last changed, which is what lets a schema that moved under a running server be
    noticed without restarting it — once per table per burst rather than per call, since
    the configured source holds that answer for a few seconds. See `Held` in `catalog.py`.

    `tables` runs first and on this thread, which is also what builds the source's client
    before anything shares it."""
    names = catalog.tables()
    kept = Profiles(state_dir() / "profiles.json")
    with ThreadPoolExecutor(max_workers=PROFILE_WORKERS) as pool:
        return tuple(pool.map(lambda name: kept.read(catalog, name), names))


def profile(catalog: Catalog, name: str) -> TableProfile:
    """One table, through the same cache the whole schema is read through, which is what
    makes reading one table on its own honest: a profile is a profile whether it was asked
    for by name or as part of a schema, and both find the same stored one. Reading the
    whole schema to answer for one table would pay for fifteen freshness checks to serve
    one, which is what a panel drawing a tree of tables does per table."""
    return Profiles(state_dir() / "profiles.json").read(catalog, name)


def saved() -> Dashboards:
    """The dashboards a person saved, read from disk on every request for the same reason
    the relationship answers are: the file is small, and a copy held between requests is
    one more thing that can answer with what was true a moment ago."""
    return Dashboards(state_dir() / "dashboards.json")


def answers() -> Confirmations:
    """What a person has said about the suggested relationships, read from disk on every
    request rather than cached, because the file is small and a cache would be one more
    thing that can hold a stale answer."""
    return Confirmations(state_dir() / "relationships.json")


def relationship_graph(catalog: Catalog) -> list[Relationship]:
    """Everything known about how the tables relate: what the source declares, plus what
    the column names and types suggest.

    Built from `describe` rather than from the profiles, because a name and a type is all
    `suggest` reads and a profile costs a freshness statement per table to produce the rest.
    Dragging a column onto another table asks for a join path, so that was a bill in
    statements on every drag, for figures nothing here looked at.

    It also stops dropping join keys: a profile leaves out a column whose type the catalog
    calls unsupported, and a key of an unsupported type still joins perfectly well. What the
    interface shows is unchanged — the Data view is drawn from the profiles as before, and
    nothing here is a row.

    Describing the schema is one round trip per table, and they used to run one after
    another, which put a schema's worth of latency in front of every drag of a column onto
    another table: `/api/join-path` is what a drop calls, and it builds this from nothing
    every time. They overlap now, through a pool wider than the profiler's because these are
    metadata reads rather than statements a warehouse queues.

    It is one round trip per table rather than two. The declared keys are read off the
    descriptions rather than asked for again: a constraint is held on the table that
    declares it, so the response that listed a table's columns already carried them, and
    `catalog.relationships()` was describing the same schema a second time to find them.

    And it is none at all on the drag after the first, because the configured source holds a
    description for a window of its own — see `Held` in `catalog.py`. Cold this is a listing
    plus one overlapped read per table; warm it reaches the source for the listing only,
    which is what a person dragging a second column pays.

    `tables` runs first and on this thread, which is what builds the source's client before
    the pool shares it, the same order `profiles` relies on."""
    names = catalog.tables()
    with ThreadPoolExecutor(max_workers=METADATA_WORKERS) as pool:
        described = tuple(pool.map(catalog.describe, names))
    columns = {
        table.name: {column.name: column.type for column in table.columns} for table in described
    }
    declared = sorted(relationship for table in described for relationship in table.relationships)
    return graph(declared, suggest(columns))


class SpecRequest(BaseModel):
    # Deliberately untyped: the validator answers for every shape a spec can arrive in,
    # including the ones that are not objects at all, and a model that rejected those
    # first would replace its message with one of pydantic's.
    spec: object


class QuestionRequest(BaseModel):
    question: str


class DashboardRequest(BaseModel):
    """The tiles of one dashboard, and the filters that apply across them. The name is in
    the path, because it is what addresses the dashboard, and a name in the body as well
    would be two of them to disagree.

    Both lists are untyped for the same reason a spec is: the validator answers for every
    shape a spec can arrive in, and a model that rejected the wrong ones first would
    replace its message with one of pydantic's. `filters` defaults to empty, so a client
    that has never heard of one saves exactly what it used to."""

    tiles: list[object]
    filters: list[object] = []


class AnswerRequest(BaseModel):
    """A person's answer about one suggested relationship. It names the relationship by
    the columns it joins rather than by an index into a list, because the list is derived
    and would renumber under a re-profile."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    answer: str


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    """Answerable without a source, because it is what a deployment check runs. It says
    what is configured so that the interface can name the missing piece rather than
    letting it arrive as a failed query."""
    return {
        "status": "ok",
        "version": __version__,
        "source": bool(source_settings()) and all(os.environ.get(name) for name in source_settings()),
        "kind": kind(),
        "model": all(os.environ.get(name) for name in MODEL_CONFIGURATION),
    }


@app.get("/api/tables", dependencies=[Depends(rationed(QUERY))])
def tables(catalog: Annotated[Catalog, Depends(source)]):
    """Every table in the configured schema, as the profile the prompt path was given.

    The profiles rather than the names, because this endpoint built them either way and
    threw them away, and the panel then asked for each one back: a page load was one
    freshness check per table here and a second one per table there, plus a schema listing
    per table for the 404 check below. On a fifty table schema that is about 150 source
    calls, 100 of them statements a warehouse bills for, before anybody has asked anything.
    One request, one freshness check per table.

    Profiling a schema nobody has profiled is the wait the first question pays, and every
    path reads the same cache, so it is paid once however it is reached and not again until
    a table changes. `as_dict` is the serialisation here as below, and a column above the
    profiler's sample threshold carries no sample values: this answers with profiles and
    never with a row."""
    try:
        return {"tables": [table.as_dict() for table in profiles(catalog)]}
    except RuntimeError as failure:
        return refused("source", failure).response


@app.get("/api/shape")
def shape(catalog: Annotated[Catalog, Depends(source)]):
    """Every table in the configured schema, as its name, its columns and their types, and
    nothing else.

    This is what the Fields panel is actually drawn from, and it costs no statement. A table
    row is a name and a count, a column row is a name and a type, and dragging one into a
    well needs the type and nothing more — `spec.ts` reads no other figure to infer an
    aggregate or a truncation unit. The profile figures appear only when somebody opens a
    column, so the tree a person interacts with is `describe`, which is a metadata read.

    What that removes is the wait in front of the first paint. `/api/tables` answers only
    once every table has been profiled, so a schema nobody has profiled put its whole cold
    read — modelled at about 25 seconds and 456 billed statements over 152 tables — in front
    of the first table name on screen. The profiles still cost what they cost, and the panel
    still replaces itself with them when they land. What changes is that nobody watches them
    arrive, and that nothing is billed before there is something to look at.

    Run through the same pool width the relationship graph uses, because it is the same call
    against the same control plane: nothing bills for a description and no cluster queues
    one, so what bounds it is the source's own rate limit. #122 argued for a wider pool here
    than there; two widths for one operation is two numbers to tune and one of them wrong,
    and the second caller pays nothing anyway because the source holds a description.

    Columns whose type the catalog calls unsupported are left out, which is what a profile
    does with them. The panel is drawn from this and then from the profiles, so a column
    that appeared here and vanished there would be the tree losing a row as it filled in.

    No row, no figure, no sample. `describe` is metadata about a table, and this endpoint
    has no path to a statement."""
    try:
        names = catalog.tables()
        with ThreadPoolExecutor(max_workers=METADATA_WORKERS) as pool:
            described = tuple(pool.map(catalog.describe, names))
    except RuntimeError as failure:
        return refused("source", failure).response
    return {
        "tables": [
            {
                "table": table.name,
                "columns": [
                    {"name": column.name, "type": column.type}
                    for column in table.columns
                    if column.type != UNSUPPORTED
                ],
            }
            for table in described
        ]
    }


@app.get("/api/tables/{name}", dependencies=[Depends(rationed(QUERY))])
def table(name: str, catalog: Annotated[Catalog, Depends(source)]):
    """One table's profile: the figures the prompt path was given for that table.

    Read from the same profiles the model sees rather than profiled a second time here. A
    second path could produce figures that disagree with the prompt's, and a panel whose
    claim is that this is what the model saw is worse than no panel when it is wrong.

    `as_dict` is the serialisation, because it already turns every figure into text, so a
    date, a decimal and a string survive the same JSON round trip. A column above the
    profiler's sample threshold carries no sample values, here as everywhere: this answers
    with a profile and never with a row.

    A name the schema does not hold is refused at 404 naming it, rather than raising. The
    name is checked against the source's own listing rather than against the profiles,
    because profiling a schema to find out that one of its tables is missing is a bill for
    an answer the listing already had. The panel takes its names from the list above, so a
    miss means the table went away between the two requests."""
    try:
        if name not in catalog.tables():
            return JSONResponse(
                status_code=404,
                content={"errors": [f"no table named {name} in the configured schema"]},
            )
        return profile(catalog, name).as_dict()
    except RuntimeError as failure:
        return refused("source", failure).response


@app.get("/api/relationships")
def relationships(
    catalog: Annotated[Catalog, Depends(source)],
    confirmations: Annotated[Confirmations, Depends(answers)],
):
    """What the source declares and what the profiles suggest, each with what a person
    has said about it. A suggestion they turned down is not in the list, because a
    rejected suggestion that came back would be the same question asked every time.

    Nothing here is a row. A suggestion is made from column names and types, so naming
    the two columns is the whole of the evidence for it."""
    try:
        known = relationship_graph(catalog)
    except RuntimeError as failure:
        return refused("source", failure).response
    return {
        "relationships": [
            {**relationship.as_dict(), "state": confirmations.state(relationship)}
            for relationship in confirmations.offered(known)
        ]
    }


@app.post("/api/relationships")
def answer(
    request: AnswerRequest,
    catalog: Annotated[Catalog, Depends(source)],
    confirmations: Annotated[Confirmations, Depends(answers)],
):
    """Confirm a suggestion, mark it as not a match, or take a confirmation back. The
    relationship has to be one the graph holds: an answer about a pair nothing suggested
    would be a person naming a join by hand, which is a different feature and is not this
    one."""
    try:
        known = relationship_graph(catalog)
    except RuntimeError as failure:
        return refused("source", failure).response

    named = Relationship(
        request.left_table, request.left_column, request.right_table, request.right_column
    )
    match = next((r for r in known if r.key == named.key), None)
    if match is None:
        left = f"{named.left_table}.{named.left_column}"
        right = f"{named.right_table}.{named.right_column}"
        return JSONResponse(
            status_code=404,
            content={"errors": [f"nothing relates '{left}' to '{right}'"]},
        )
    if match.kind == DECLARED:
        return JSONResponse(
            status_code=400,
            content={
                "errors": [
                    f"'{match.key}' is declared by the source, which is not a person's to approve"
                ]
            },
        )
    try:
        confirmations.record(match, request.answer)
    except ValueError as failure:
        return JSONResponse(status_code=400, content={"errors": [str(failure)]})
    return {"relationship": match.as_dict(), "state": confirmations.state(match)}


@app.get("/api/join-path")
def join_path(
    left: str,
    right: str,
    catalog: Annotated[Catalog, Depends(source)],
    confirmations: Annotated[Confirmations, Depends(answers)],
):
    """How to get from one table to another, as the joins a spec would carry.

    Resolution walks confirmed relationships only, so an ambiguity and an absence are
    both refusals rather than a guess. They answer 400 with the resolver's own words,
    because that message is what a person who dragged a field reads."""
    try:
        known = relationship_graph(catalog)
    except RuntimeError as failure:
        return refused("source", failure).response
    try:
        path = resolve(confirmations.usable(known), left, right)
    except ValueError as failure:
        return JSONResponse(status_code=400, content={"errors": [str(failure)]})
    return {"joins": _joins(left, path)}


def _joins(left: str, path: list[Relationship]) -> list[dict]:
    """A resolved path in the spec grammar's own shape. Each step names the table being
    joined, which is whichever end of the relationship the walk had not reached yet, so
    the direction of the foreign key does not decide the direction of the walk."""
    joins = []
    reached = {left}
    for relationship in path:
        table = (
            relationship.right_table
            if relationship.left_table in reached
            else relationship.left_table
        )
        joins.append(
            {
                "table": table,
                "on": [
                    {
                        "left": f"{relationship.left_table}.{relationship.left_column}",
                        "right": f"{relationship.right_table}.{relationship.right_column}",
                    }
                ],
            }
        )
        reached.add(table)
    return joins

# Neither endpoint below declares a response model. One would re-serialise the rows on
# their way out, and pydantic and the encoder disagree about a decimal, so a total would
# reach the chart as text. What a value looks like belongs to the source, and the API
# hands on the result set it was given.
@app.post("/api/validate")
def validate(request: SpecRequest):
    """The validator's errors. An empty list means valid. Reaches no source."""
    return {"errors": validate_spec(request.spec)}


@app.get("/api/dashboards")
def dashboards(store: Annotated[Dashboards, Depends(saved)]):
    """Every saved dashboard, as its name and how many tiles are under it.

    The tiles themselves are not here. A list of names is what a person picks from, and
    answering it with every spec of every dashboard would send the whole store to draw a
    menu. Reaches no source: a dashboard is specs, and running one is what runs a query."""
    return {
        "dashboards": [
            {"name": name, "tiles": len(store.read(name).tiles)} for name in store.names()
        ]
    }


@app.get("/api/dashboards/{name}")
def dashboard(name: str, store: Annotated[Dashboards, Depends(saved)]):
    """One dashboard: its tiles, in the order they are drawn in, each with its width, and
    the filters that apply across all of them. The rows are not here either. Every tile
    goes back through `/api/execute`, so a tile and a single chart are the same spec run
    the same way against the same source — the dashboard's filters are applied to a tile's
    spec on the way, by the interface, and the result is judged like any other spec."""
    found = store.read(name)
    if found is None:
        return JSONResponse(
            status_code=404, content={"errors": [f"nothing is saved under the name '{name}'"]}
        )
    return found.as_dict()


@app.put("/api/dashboards/{name}")
def save_dashboard(
    name: str, request: DashboardRequest, store: Annotated[Dashboards, Depends(saved)]
):
    """Save a dashboard under a name, replacing whatever that name held.

    Every tile is validated before anything is written, and a rejection comes back in the
    same `errors` list the validator's own refusal uses, naming which tile by its position
    on the grid. Nothing partial is stored: a save that refused one tile leaves what was
    saved before exactly as it was, because half a dashboard is not a thing a person asked
    for."""
    try:
        return store.save(name, request.tiles, request.filters).as_dict()
    except Refused as failure:
        return JSONResponse(status_code=400, content={"errors": failure.errors})


@app.delete("/api/dashboards/{name}")
def delete_dashboard(name: str, store: Annotated[Dashboards, Depends(saved)]):
    """Forget one. A name nothing is saved under is a 404 rather than a quiet success,
    since a delete that always succeeds cannot tell a person they deleted the other one."""
    if not store.delete(name):
        return JSONResponse(
            status_code=404, content={"errors": [f"nothing is saved under the name '{name}'"]}
        )
    return {"name": name}


@dataclass(frozen=True)
class Reply:
    """A status and a body, before either has been written into a response.

    It exists because a question is answered down two channels now — a JSON body, and an
    event stream that says which step is running — and both have to answer the same thing.
    Holding the body as a dict until the last moment is what lets the stream put it in a
    frame and the JSON endpoint put it in a response, from one line that decided it.

    The body is what a source produced, so it holds dates and decimals rather than JSON
    types. `jsonable_encoder` is what turned those into the wire shape while an endpoint
    could return a dict and let FastAPI do it; returning a response or a frame means doing
    it here, on both paths, or a temporal value stops being ISO-8601 text on one of them.
    """

    status: int
    body: dict

    @property
    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status, content=jsonable_encoder(self.body))


def refused(spoke: str, failure: Exception, status: int = 502) -> Reply:
    """A failure after validation, in the shape the validator's refusal already uses, so the
    interface keeps one way to show a refusal rather than growing a second. 502 by default,
    rather than 500 or 400: the request was well formed and the spec was valid, and what
    failed sits behind the server. `spoke` names which part failed, because a question
    passes through the source, the model and the source again, and a caller cannot tell
    from a message which of them produced it."""
    return Reply(status, {"errors": [str(failure)], "spoke": spoke})


def execute_spec(spec: dict, catalog: Catalog) -> Reply:
    """The rows, or what refused to produce them.

    `RuntimeError` is the source: a statement it did not finish, or a result too large for
    one chunk. `ValueError` is the spec, and it answers 400 rather than 502 because nothing
    behind the server was reached and the spec is what has to change. Both callers validate
    before they get here, so the only rule left to fail is the builder's re-aggregation
    check, which needs the compiled query and cannot run inside the validator."""
    try:
        rows = query.execute(spec, catalog)
    except ValueError as failure:
        return refused("spec", failure, status=400)
    except RuntimeError as failure:
        return refused("source", failure)
    return Reply(200, {"spec": spec, "rows": rows})


@app.post("/api/execute", dependencies=[Depends(rationed(QUERY))])
def execute(request: SpecRequest, catalog: Annotated[Catalog, Depends(source)]):
    """The rows a valid spec produces, with the spec that produced them. An invalid spec is
    refused with its errors and never reaches the source."""
    errors = validate_spec(request.spec)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    return execute_spec(request.spec, catalog).response


def answering(
    question: str,
    catalog: Catalog,
    writer: Model,
    confirmations: Confirmations,
) -> Iterator[Step | Reply]:
    """A question, as the steps it passes through and then the one answer it ends in.

    A question is not one wait. It reads the profiles, builds the relationship graph, asks
    the model up to three times and then runs the query, and on a large schema the metadata
    in front of the model is the long part — measured at around 18 seconds on 152 tables,
    before a token is requested. A caller shown one spinner over all of that cannot tell
    whether the model is slow, the warehouse is cold, or nothing is happening.

    So the steps are yielded as they start, and the answer is the last thing yielded rather
    than returned: both endpoints below read one sequence, and the JSON one is the streaming
    one with the steps dropped. That is what stops the two from answering differently.

    Profiling reaches the source before the model is asked anything, so the first thing a
    question can fail on is the source rather than the model.
    """
    yield Step("profiles")
    try:
        tables = profiles(catalog)
        # What a join may be resolved through, so a table the answer has to join through is
        # readable even where the question never names it. Declared and confirmed only,
        # which is the same rule the resolver applies: a suggestion nobody confirmed is not
        # a join, so a table only a suggestion reaches is not one this makes room for.
        confirmed = confirmations.usable(relationship_graph(catalog))
    except RuntimeError as failure:
        yield refused("source", failure)
        return
    try:
        answer = yield from asking(
            question,
            tables,
            writer,
            constrained=constrains(writer),
            relationships=confirmed,
        )
    except ModelError as failure:
        yield refused("model", failure)
        return
    if answer.spec is None:
        yield Reply(400, {"errors": answer.errors, "cost": answer.spent.as_dict()})
        return
    yield Step("query")
    # What the question cost, beside what it produced. The central claim of this design is
    # that sending metadata rather than data keeps token cost bounded, and the number that
    # demonstrates it was in hand on every request and thrown away. A question that took
    # three attempts costing three times one that took one is the thing worth seeing, which
    # is why this is the loop's total and carries the count of calls that made it.
    ran = execute_spec(answer.spec, catalog)
    yield Reply(ran.status, {**ran.body, "cost": answer.spent.as_dict()})


def frames(events: Iterable[Step | Reply]) -> Iterator[str]:
    """The same sequence as server-sent events.

    The status line is 200 whatever happens, because the headers go out before the first
    step runs and a refusal is decided after. So the refusal is an event named `refused`
    carrying the status the JSON endpoint would have sent, and a reader goes by the name of
    the event rather than by the status of the response. That is the cost of answering more
    than once over one request, and it is why `spoke` was already on the body.
    """
    for event in events:
        if isinstance(event, Reply):
            yield frame("answer" if event.status == 200 else "refused", event.body)
        else:
            yield frame("step", event.as_dict())


def frame(name: str, body: dict) -> str:
    """One event. The body is one line of JSON, which is what keeps a value out of the
    protocol: a frame ends at a blank line, and `json.dumps` has already escaped every
    newline a source's own error message might carry."""
    return f"event: {name}\ndata: {json.dumps(jsonable_encoder(body))}\n\n"


# Asked for by a caller that wants the steps. `Accept` rather than a second path, because
# it is the same question producing the same answer and only the channel differs — and a
# path answering the same thing twice is two contracts to keep in step.
EVENTS = "text/event-stream"


@app.post("/api/ask", dependencies=[Depends(rationed(MODEL))])
def question(
    request: QuestionRequest,
    http: Request,
    catalog: Annotated[Catalog, Depends(source)],
    writer: Annotated[Model, Depends(model)],
    confirmations: Annotated[Confirmations, Depends(answers)],
):
    """A question, answered as the spec it produced and the rows that spec returned. The
    model sees the profiles of the tables the question is about, and the question. The rows
    it caused to be fetched go to the caller, never back to it.

    Two channels, one answer. A caller that asks for `text/event-stream` is told which step
    is running as it starts and gets the answer as the last event; a caller that does not is
    answered with the body it has always been answered with, which is the last event of the
    same sequence. Everything that decides what the answer is lives in `answering` above,
    so there is no second account of what a question does.
    """
    events = answering(request.question, catalog, writer, confirmations)
    if EVENTS in (http.headers.get("accept") or ""):
        return StreamingResponse(
            frames(events),
            media_type=EVENTS,
            # A proxy that buffers is a proxy that turns this back into one wait with the
            # steps arriving all at once at the end. Both headers are advisory and neither
            # costs anything where nothing is listening to them.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return waited(events)


def waited(events: Iterable[Step | Reply]) -> JSONResponse:
    """The answer, with the steps dropped. What a caller that did not ask to hear them was
    always going to be sent: the sequence ends in exactly one `Reply`, and this is it."""
    replies = [event for event in events if isinstance(event, Reply)]
    return replies[-1].response


@app.post("/api/critique", dependencies=[Depends(rationed(MODEL))])
def suggestion(
    request: SpecRequest,
    catalog: Annotated[Catalog, Depends(source)],
    writer: Annotated[Model, Depends(model)],
):
    """What a rule refuses about a spec, and the spec suggested in its place.

    A second opinion on rules that already exist, which is deliberately all it is: what it
    may say is what is refusable, and the model's part is naming a replacement the rule does
    not name. See `critique.py` and DESIGN.md.

    Nothing is applied. The suggestion comes back beside the findings and the caller decides,
    which is why this answers with a spec and never with rows: running one is `/api/execute`,
    the same endpoint every other spec goes through, and a suggestion nobody accepted has
    then cost no query.

    A spec that does not validate is refused with the validator's own words rather than
    critiqued. The findings are about a chart, and a spec that is not one yet has a shorter
    list of things wrong with it that the person should read first.

    An empty `findings` means there was nothing to say, and no request was made of the model:
    a model asked to improve a chart that is fine will improve it, and what comes back is
    somebody's taste with a bill attached.
    """
    errors = validate_spec(request.spec)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    try:
        tables = profiles(catalog)
    except RuntimeError as failure:
        return refused("source", failure).response
    try:
        return critique(request.spec, tables, writer, constrained=constrains(writer)).as_dict()
    except ModelError as failure:
        return refused("model", failure).response


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
