# Roadmap

Milestones and the decisions behind them. Task level work lives in issues.

## Decisions made

**The model emits a query IR, not SQL.** The LLM returns a JSON structure describing tables, joins, filters, grouping and aggregation. A deterministic builder compiles that to SQL. This is what makes "the model never emits code that gets executed" true rather than aspirational, and it means the query layer is testable without an LLM call. The cost is expressiveness: anything the IR cannot describe cannot be asked. That is accepted for v1.

**The chart spec is an abstract grammar, not an ECharts config.** Mark plus encoding channels, in the shape of Vega-Lite but without the dependency. The renderer compiles it. An ECharts config would be unreadable in a diff and would weld the project to one rendering library.

**A multi series chart must limit by dimension, not by row.** `limit` caps rows, so on a query grouped by two columns it cuts series members off at an arbitrary point and produces a chart that looks correct and is not. The IR has `limit_by` for that: keep the top N values of the outer dimension, then every row belonging to them. The validator rejects a coloured multi series chart without it. This costs a windowed subquery in the builder at M3, which is cheaper than a wrong answer nobody notices.

**The result set contract.** Query builder, HTTP API and renderer all pass the same thing: an array of plain objects whose keys are exactly the query's output columns, meaning the `as` alias or the last segment of each `select` and `group_by` item, plus every aggregate alias. Column order is stable and set by the builder, so a caller can rely on it, but the renderer addresses columns by name and must not depend on order. A null value in a field bound to `color` is its own series rather than an error or a dropped row. This is written down here because three separate pieces of work depend on it, and whichever lands first would otherwise decide it by accident.

**Relationships are catalog metadata, confirmed once, not inferred per query.** Dragging a field into a chart has to produce a join path, and that can only come from three places: the model, a resolver, or nowhere. Asking the model on every drag makes direct manipulation slow and unrepeatable, which removes the reason to offer direct manipulation at all. So the catalog builds a relationship graph when it profiles a source: declared foreign keys are read where they exist, the rest is suggested from column names and types and confirmed by a person once. Joins then resolve as the shortest path through that graph, deterministically and without a model call. The model receives the graph too, so a join it proposes can be checked against something rather than trusted. The cost is a concept the product did not have and a screen to maintain it, and since DuckDB files built from CSV or Parquet usually declare no foreign keys, the suggest-and-confirm path is the common case rather than the exception.

**Aggregation lives in the query, never in the renderer.** The renderer draws what it is given. This keeps one source of truth for what a number means.

**Hosted API first, self hosted second.** Both are targets, but development happens against a hosted OpenAI-compatible endpoint because structured output is reliable there. Small local models fail schema validation more often, which makes the retry loop the dominant UX. That loop gets designed once the schema is proven, not before.

## Milestones

**M1, spec and renderer.** JSON Schema, semantic validator, golden fixtures, ECharts renderer driven by those fixtures. No database, no LLM. Done when a spec file renders a correct chart and every invalid fixture is rejected for the right reason.

**M2, catalog and profiler.** DuckDB connector over synthetic e-commerce data. Types, cardinality, null rate, min/max, distinct samples for low cardinality columns. Deterministic and cacheable. Done when the profile of a fixed database is byte identical across runs.

**M3, query builder.** Query IR to SQL, executed against DuckDB. Done when every valid fixture returns a result set the renderer accepts.

**M4, LLM adapter.** OpenAI-compatible `base_url`, user supplied key and endpoint. Question plus profile in, spec out, validated, rejected and retried on failure with the validator errors as feedback. Structured output support differs across providers and needs verifying against current docs before the interface is fixed.

**M5, eval harness.** A fixture question set scored for correctness. This is what makes prompt changes measurable instead of anecdotal.

**M6, dashboards.** Several specs saved together, arranged, reloaded.

## Deferred

**Databricks connector.** The interface assumes a third source gets added by someone who has neither DuckDB nor Databricks. Implementation waits until the DuckDB path is proven.

**Rebuilding a dashboard from a screenshot.** Needs a vision model. Vision support across self hosted OpenAI-compatible servers is uneven, so this breaks the single adapter assumption. Revisit after M6.

**Critique pass.** An assistant that suggests improvements to an existing spec. Cheap once the spec exists, since it is JSON in and JSON out through the same validator. Waits for M5, because without evaluation there is no way to tell whether its suggestions are good.
