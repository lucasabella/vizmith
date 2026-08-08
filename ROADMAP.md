# Roadmap

What is next. Why the built parts are the shape they are is in [DESIGN.md](DESIGN.md),
and task level work is in [issues](https://github.com/lucasabella/vizmith/issues).

## Milestones

All six are built. What that phrase is worth varies, and the markers say
where.

**M1, spec and renderer. Built.** JSON Schema, semantic validator, golden fixtures, ECharts renderer driven by those fixtures. No database, no LLM. Done when a spec file renders a correct chart and every invalid fixture is rejected for the right reason.

**M2, catalog and profiler. Built.** Unity Catalog connector over the synthetic fixture dataset loaded into a workspace. Types, cardinality, null rate, min/max, distinct samples for low cardinality columns. Deterministic and cacheable. Done when the profile of a fixed schema is byte identical across runs.

**M3, query builder. Built.** Query IR to SQL, executed against a SQL warehouse. Done when every valid fixture returns a result set the renderer accepts.

**M4, LLM adapter. Built.** OpenAI-compatible `base_url`, user supplied key and endpoint. Question plus profile in, spec out, validated, rejected and retried on failure with the validator errors as feedback. Structured output support differs across providers; what that check found is recorded above.

**M5, eval harness. Built, never run.** A fixture question set scored for correctness. This is what makes prompt changes measurable instead of anecdotal — and it has never produced a number, because it needs a model endpoint and a workspace and neither belongs in a public repository's CI. Until it does, every prompt change here is defended by argument. That is [#61](https://github.com/lucasabella/vizmith/issues/61), and it blocks more than itself.

**M6, dashboards. Built.** Several specs saved together, arranged, reloaded.

## Deferred

**A second connector.** The catalog is still an interface with one implementation behind it, because the layers above it must not learn Databricks vocabulary. Anything else, DuckDB included, waits until somebody has a reason to run it.

**Rebuilding a dashboard from a screenshot.** Needs a vision model. Vision support across self hosted OpenAI-compatible servers is uneven, so this breaks the single adapter assumption. Revisit after M6.

**Critiquing a dashboard rather than a chart.** The critique above is about one spec. A set of charts needs an argument about what a set is for — whether two tiles answering the same question is a fault, whether an arrangement can be wrong — and nothing in the project has one yet. It is not the chart rule applied several times.
