# Vizmith

[![CI](https://github.com/lucasabella/vizmith/actions/workflows/ci.yml/badge.svg)](https://github.com/lucasabella/vizmith/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/coverage-98%25-brightgreen.svg)](#from-a-checkout)

Ask a question in plain language, get a chart back.

![The Chart view: a stacked bar of revenue per country by product category, the wells that built it, and the Fields panel listing every table with its row count](docs/vizmith.png)

*Drawn by `docs/screenshot.py` against the committed fixture data, so it is a real query
over real rows. They are just invented rows.*

Vizmith connects to a database or lakehouse, reads its **metadata** (schemas, column types, cardinality, null rates, value ranges), and uses an LLM to turn a natural language question into a validated visualisation spec. A deterministic renderer draws the chart. The LLM never renders anything and never sees a row.

**Who it is for.** Data and analytics engineers on Databricks who want self-serve charts
without shipping rows to somebody else's SaaS. It runs on your machine, against your
warehouse, with a model endpoint you choose. If your data cannot leave your infrastructure,
that constraint is the reason this exists rather than a feature bolted onto it.

**To try it without a warehouse**, point it at a DuckDB file: `VIZMITH_SOURCE=duckdb` and a
path. Same interface, same specs, no bill.

**Status: early development.** The interface works against a configured source: the Fields panel shows every table and column with its profile, dragging a column into a well rewrites the spec and runs it, clicking a mark asks the same question about what was clicked, the Data view is where a suggested relationship is confirmed, and the Dashboards view saves several specs under a name and opens them again. Asking a question in words needs a model endpoint, and so do the second opinion on a chart and the eval harness that scores one.

## Why metadata and not the data

The LLM receives a profile of your tables, not their contents. That keeps token cost bounded and keeps results reproducible. No row is ever assembled and sent, and a query's results go to the renderer rather than back into a prompt.

Be precise about where that boundary sits, because it is the question a governance review will ask. A profile is per column: the name, the type, the null rate, the distinct count, and then two things that come out of the data rather than out of the schema. The extremes of an ordered column, its `min` and its `max`. And, for a column with no more distinct values than the sample threshold, currently 25, that whole set of values. So a `status` or a `category` column of eight values sends its eight values, and an `order_total` column sends its largest and its smallest. That is the boundary, it is `SAMPLE_THRESHOLD` in `profiler.py`, and nothing else widens it.

What that buys is a model that can tell a country code from a currency code without guessing, which is most of what makes a spec right the first time. What it costs is that low cardinality columns leave in full. Whether that is acceptable is a question about your data and your endpoint, not one this README can answer for you, so it is stated here rather than summarised into a promise.

## Design

```
question -> catalog + profile -> query -> result set -> chart spec -> renderer
```

The model does not write SQL. It returns a query IR: a JSON description of tables, joins, filters, grouping and aggregation, which a deterministic builder compiles into a query. Nothing the model produces reaches the database unvalidated.

The spec is versioned JSON, validated against a schema, diffable in git. Every feature produces the same output type:

- Type a question, get a spec.
- Drag a column into a well, get a spec.
- Click a mark, get the same spec narrowed to what you clicked.
- Adjust the spec by hand if you want more control.
- Save a set of specs as a dashboard.

Nothing generated is executed as code. A spec the interface writes goes through the same validator a model's answer does, because the validator is the only judge of what is legal.

Suggest an improvement, under the chart, is a second opinion on the spec on screen. What it may say is what a rule refuses — today, a mark the shape of the result contradicts, judged from the profiles so an unreadable chart is named before the query is paid for — and a chart nothing refuses is told so rather than improved, because an assistant that always finds something is finding somebody's taste. The rule refuses without naming a replacement, so the replacement is what the model is asked for; it arrives as a spec beside yours, it may differ only in the chart, and it changes nothing until you press Use it. The chart it replaces stays one control away.

A chart built by dragging needs to know how two tables relate, and that comes from the catalog rather than from a prompt. Foreign keys the source declares are facts; everything else is suggested from column names and types and is not used for a join until somebody confirms it in the Data view. A wrong join produces a plausible number rather than an error, which is the failure the whole design exists to prevent, so a column with no confirmed path between its table and the query's is refused with both table names rather than joined on a guess.

A dashboard is the last of those bullets: several specs saved under a name, in the state directory beside the relationship answers. A tile holds a spec and never a row, so opening a dashboard runs each tile against the source through the same endpoint a single chart goes through, and what a tile shows is what the data says now rather than what it said when it was saved. That is one statement per tile, which is why a dashboard is capped at 24 of them. Arranging is an order and a width, both of them controls you can see. A tile is corrected where it was made: Edit opens its spec in the Chart view, and Put it back lands it in the tile it came from, keeping that tile's place and width, or Never mind leaves the dashboard as it was. The name is what identifies a dashboard, and Rename does the save and the delete that implies.

## Bring your own model

Vizmith targets the OpenAI-compatible `base_url` convention, so one adapter covers hosted providers, Azure OpenAI, Ollama, vLLM and LM Studio. You supply the key and the endpoint. Vizmith ships with no model access of its own.

A compatible base URL does not mean a compatible feature set. Vizmith asks the model for a chart spec constrained to a JSON Schema, and support for that differs: an endpoint accepts a schema or refuses it, and OpenAI does both depending on which schema it is. Where the schema is refused, the question is asked in prose with the schema in the prompt instead.

Which endpoints do what, when each was last checked and how, is in
**[docs/compatibility.md](docs/compatibility.md)**. It is kept there rather than here
because it rots on a schedule nobody sets, and a dated row is the only honest way to carry
it. The adapter does not rely on that table in any case: `Model.constrains_output(schema)`
asks the endpoint with the schema it is about to send and believes the answer.

## Stack

Python (FastAPI, httpx) backend, React frontend, ECharts for rendering. Sources: Databricks Unity Catalog, DuckDB — a file on your own machine, opened read only, which is what makes this runnable without a warehouse and a bill — BigQuery, Snowflake and PostgreSQL. All five go through the same catalog interface, and the DuckDB one is what the deterministic half of the suite runs against, so it cannot rot without the suite going red. What each source answers for the contracts that have a `None` in them, and how each was checked, is in [docs/compatibility.md](docs/compatibility.md); BigQuery, Snowflake and PostgreSQL have not been run against a real project, account or server yet, and that table says so.

Vizmith runs on your machine and serves a browser. The frontend talks to the backend over HTTP and nothing else, which keeps a desktop build possible later without touching application code.

## Running it

There is no PyPI release yet, so installing means building the wheel. That needs Node,
because the interface is built into the package:

```
git clone https://github.com/lucasabella/vizmith && cd vizmith
python -m build
pipx install dist/vizmith-*.whl

vizmith configure
vizmith serve
```

`serve` starts the API on port 8000 and opens a browser. The wheel carries the built
interface, so nothing else has to be running.

When there is a release, the first three lines collapse to `pipx install vizmith`, or to
nothing at all with `uvx vizmith configure` and `uvx vizmith serve`. That is the plan
rather than the present, and this file will say so when it is true.

`configure` asks which kind of source this is and then for the values that kind needs, and writes them to `config.env` in the state directory, readable by you and nobody else, because one of them is a key. `VIZMITH_SOURCE` is `databricks` or `duckdb`; unset means Databricks, which is what shipped first. Its four `VIZMITH_DATABRICKS_` values, or DuckDB's three `VIZMITH_DUCKDB_` ones, are the source, without which a spec has nothing to run against. The three `VIZMITH_MODEL_` values are the endpoint that writes a spec from a question, and without them the question field stays disabled while a spec pasted by hand still runs. Pass them as flags instead where there is no terminal to ask in, and run `vizmith configure --show` to see where each one is coming from — it prints that and never the values.

Configuration is read from three places, nearest first: a real environment variable, a `.env` found from the working directory upwards, then the file `configure` wrote. Nothing over HTTP writes any of it, so a request cannot point Vizmith at a database and the model key has no path into a browser at all.

### From a checkout

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env      # and fill it in
.venv/bin/vizmith serve
```

That resolves the dependency floors in `pyproject.toml` to whatever is newest today, which
is usually what you want while working. `uv.lock` records the set CI installs and the set
these numbers were measured against, so `uv sync --extra dev` is the way to reproduce a
result rather than approximate it. A change that moves a dependency has to move the lock
with it, and CI refuses the two when they disagree.

An editable install serves `web/dist`, so build the frontend once with `npm run build` in `web/`, or run Vite alongside it:

```
cd web
npm install
npm run dev
```

Vite proxies `/api` to port 8000. Building a wheel runs that build itself and puts the result inside the package, which is why `python -m build` needs Node and installing does not.

Tests and lint:

```
.venv/bin/pytest
.venv/bin/ruff check .
cd web && npm test && npm run lint
```

`pytest --cov` adds the coverage report. The offline suite reaches 98% of the package with
no warehouse and no model endpoint; CI runs it that way and fails under 90. The frontend's
own number is lower, around 72%, and the gap is mostly the views, which are covered by the
browser suite below rather than by `npm test`.

A handful of flows are driven in a real browser, against the same fixture data through the real server. They need the frontend built and a Chromium that Playwright can launch, and they skip where either is missing:

```
cd web && npm run build && cd ..
.venv/bin/playwright install chromium
.venv/bin/pytest tests/test_interface.py
```

Set `VIZMITH_CHROMIUM` to a browser's path where one is already installed and Playwright's own copy is not it. These cover what crosses a view, a reload or a repaint, which is what a static render cannot reach; everything else about the interface is tested by `npm test`.

The screenshot at the top of this file is taken by the same machinery, so a change to the
interface can be shown rather than described:

```
.venv/bin/python docs/screenshot.py
```

The suite runs offline against DuckDB. With a profile and a warehouse in `.env` it also compiles and runs every fixture spec against the workspace, through the query builder and through the HTTP API. Without one those tests skip.

Scoring the model on a fixed question set:

```
.venv/bin/vizmith eval
.venv/bin/vizmith eval --only revenue_by_country
```

Every question is asked of the synthetic fixture dataset, so this needs the source in `.env` pointed at it, and it needs the model endpoint. Each question is scored in four layers — does the answer validate, does it reference the tables and columns the question needs, does it return the expected rows, is the mark defensible for their shape — and a question stops at the first layer it fails. The run is written to `eval-runs/`, so two runs can be diffed; what makes that worth doing is that the record names the model and the endpoint that produced it. Answers are cached against the prompt that produced them, in the state directory beside the profiles, so re-running a set costs nothing until a prompt, a profile or an endpoint changes. `--no-cache` asks anyway.

`--repair` measures the critique rather than the prompt: wherever a question fails the mark layer, it asks for a suggestion and records whether the same rule accepts it, on the rows that question already fetched. A critique may only change the chart, so those rows cannot have moved, and the run counts how many refused marks it repaired.

## Contributing

Issues are labelled by type, area and priority, and the open ones are a fair picture of
what is missing rather than a wish list. [ROADMAP.md](ROADMAP.md) is where the decisions
already made are written down, including the ones that were tried and cut, which is usually
the faster way to find out why something is the shape it is.

- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Reporting a vulnerability: [SECURITY.md](SECURITY.md). Please use the private route
  rather than an issue, because people run Vizmith against their own warehouses.

## Licence

Apache-2.0. See [LICENSE](LICENSE), and [NOTICE](NOTICE) for what travels inside a built
wheel.
