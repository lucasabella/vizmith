"""The HTTP surface: a spec goes in, its errors or its rows come back.

A request carries a spec and nothing else. The data source is server configuration, so a
client cannot name a database, and no response carries SQL, so a client cannot learn one
either. The artefact a client holds is the spec, which is the point of the whole design.

Validator messages are returned word for word. They are written to be fed back to a model
on retry, so rewording them here would break that loop before it is written.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vizmith import __version__, query
from vizmith.catalog import Catalog, DatabricksCatalog
from vizmith.spec import validate_spec

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

CONFIGURATION = (
    "VIZMITH_DATABRICKS_PROFILE",
    "VIZMITH_DATABRICKS_CATALOG",
    "VIZMITH_DATABRICKS_SCHEMA",
    "VIZMITH_DATABRICKS_WAREHOUSE",
)

app = FastAPI(title="Vizmith")


@lru_cache
def source() -> Catalog:
    """The configured source, built once and on first use rather than at import, so that
    health answers on a server that has none and a test can put its own in its place."""
    profile, catalog, schema, warehouse = (os.environ[name] for name in CONFIGURATION)
    return DatabricksCatalog(profile=profile, catalog=catalog, schema=schema, warehouse=warehouse)


class SpecRequest(BaseModel):
    # Deliberately untyped: the validator answers for every shape a spec can arrive in,
    # including the ones that are not objects at all, and a model that rejected those
    # first would replace its message with one of pydantic's.
    spec: object


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    """Answerable without a source, because it is what a deployment check runs. It says
    whether one is configured so that the interface can name the missing piece rather than
    letting it arrive as a failed query."""
    return {
        "status": "ok",
        "version": __version__,
        "source": all(os.environ.get(name) for name in CONFIGURATION),
    }


# Neither endpoint below declares a response model. One would re-serialise the rows on
# their way out, and pydantic and the encoder disagree about a decimal, so a total would
# reach the chart as text. What a value looks like belongs to the source, and the API
# hands on the result set it was given.
@app.post("/api/validate")
def validate(request: SpecRequest):
    """The validator's errors. An empty list means valid. Reaches no source."""
    return {"errors": validate_spec(request.spec)}


@app.post("/api/execute")
def execute(request: SpecRequest, catalog: Annotated[Catalog, Depends(source)]):
    """The rows a valid spec produces, with the spec that produced them. An invalid spec is
    refused with its errors and never reaches the source."""
    errors = validate_spec(request.spec)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    return {"spec": request.spec, "rows": query.execute(request.spec, catalog)}


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
