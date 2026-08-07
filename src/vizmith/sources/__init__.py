"""The sources Vizmith can be pointed at, one module each.

A source is chosen by configuration and never by a request, which is the sentence `api.py`
opens with, so this is where a name in a settings file becomes a catalog and the only place
that mapping exists. Adding one is a module here, an entry in `KINDS`, and the settings it
needs in `config.py`.

Nothing is imported until it is asked for. A checkout configured for a warehouse should not
have to have DuckDB installed, and one configured for a local file should not pay for the
Databricks SDK, so `build` imports the module for the kind it was asked for and no other.
"""

from collections.abc import Callable

from vizmith.catalog import Catalog

# What a source of each kind is made of: the settings it reads, in the order its
# constructor takes them. The names are what `config.py` asks for and what a `.env` holds,
# and this is the list `api.py` checks before it says a source is configured.
KINDS: dict[str, tuple[str, ...]] = {
    "databricks": (
        "VIZMITH_DATABRICKS_PROFILE",
        "VIZMITH_DATABRICKS_CATALOG",
        "VIZMITH_DATABRICKS_SCHEMA",
        "VIZMITH_DATABRICKS_WAREHOUSE",
    ),
    "duckdb": (
        "VIZMITH_DUCKDB_PATH",
        "VIZMITH_DUCKDB_DATABASE",
        "VIZMITH_DUCKDB_SCHEMA",
    ),
}

# What a person gets without saying, which is the source that shipped first. A checkout
# that predates there being a choice keeps working without adding a setting.
DEFAULT = "databricks"


def _databricks(profile: str, catalog: str, schema: str, warehouse: str) -> Catalog:
    from vizmith.sources.databricks import DatabricksCatalog

    return DatabricksCatalog(profile=profile, catalog=catalog, schema=schema, warehouse=warehouse)


def _duckdb(path: str, database: str, schema: str) -> Catalog:
    from vizmith.sources.duckdb import DuckDBCatalog

    return DuckDBCatalog(path=path, database=database, schema=schema)


_BUILDERS: dict[str, Callable[..., Catalog]] = {"databricks": _databricks, "duckdb": _duckdb}


def build(kind: str, values: list[str]) -> Catalog:
    """One catalog of the named kind, from the settings `KINDS` lists for it.

    A kind nothing here knows is a configuration error rather than a failure behind the
    server, and it says what the choices are, because the person who typed it is one
    character away from the one they meant."""
    if kind not in _BUILDERS:
        raise ValueError(
            f"'{kind}' is not a source Vizmith knows. VIZMITH_SOURCE is one of "
            f"{', '.join(sorted(_BUILDERS))}."
        )
    return _BUILDERS[kind](*values)
