# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Vizmith turns a natural language question into a validated chart spec, using database metadata rather than the data itself. Apache-2.0.

## Hard rules

Breaking one of these breaks the project.

1. **No proprietary or confidential material in this repo.** No real schemas, table names, business logic or sample data from any organisation. All test data is synthetic and generic e-commerce. If a task would require real data, stop and say so.
2. **The LLM never sees raw rows.** It receives metadata and column profiles. Query results go to the renderer, not back into a prompt. Changing this is a design decision, not an implementation detail.
3. **The LLM never renders and never emits code that gets executed.** It emits a chart spec, validated against the JSON Schema before anything downstream touches it. Invalid spec means reject and retry, not patch it up.
4. **No committing or pushing unless asked.**

## Documentation policy

This repository is public. Documentation is written for someone deciding whether to use or contribute to the project, not to demonstrate effort.

- Three markdown files at root: `README.md`, `CLAUDE.md`, `ROADMAP.md`. A fourth needs a reason.
- No summary files, no status reports, no implementation notes, no per-feature writeups. Finished work is described by its code and tests.
- To-do lists belong in issues, not in the repo.
- Docs explain decisions and trade-offs. Anything that only restates what the code does gets deleted instead of written.
- Update the file that already covers the topic rather than adding a new one.
- Short beats complete. A doc nobody reads is worse than no doc.

## Architecture

```
question -> catalog + profile -> query -> result set -> chart spec -> renderer
```

- **Catalog layer**: interface with per-source implementations. DuckDB first, then Databricks. Assume a third source gets added by someone who has neither.
- **Profiling layer**: types, cardinality, null rate, min/max, distinct samples for low cardinality columns. Deterministic and cacheable. This is the LLM's entire view of the data.
- **LLM adapter**: OpenAI-compatible `base_url` convention. Azure OpenAI is not a clean drop-in for it, so verify its auth and URL shape against current docs before the interface is finalised.
- **Spec**: versioned JSON, JSON Schema validated, human editable.
- **Renderer**: ECharts, driven purely by the spec.
- **Eval harness**: runs a question set and scores correctness. Question sets are fixtures, synthetic.

## Stack

Python 3.12, FastAPI, DuckDB, React, ECharts.

## Skills

Project skills live in `.claude/skills/`. Read the relevant one before starting, not after.

- **`vizmith-design`**, before any screen, component, mockup or chart. Layout, tokens, component specs, copy rules, and the list of things that were tried and cut. Charts fall back to the data viz method for anything it does not cover.
- **`vizmith-packaging`**, before touching the CLI entry point, installers, a desktop shell, code signing or a release.
- **`vizmith-issues`**, before writing, picking up, implementing or closing a GitHub issue.
- **`vizmith-commits`**, before staging anything. This repository is public, so a commit is a publication.

One rule from the packaging skill constrains code written today: the frontend calls nothing but the HTTP API, no native dialogs and no desktop specific calls. That is what keeps a Tauri or Electron build a packaging job rather than a rewrite.

## Code style

- Minimum complexity that works. No abstractions for a second use case that does not exist yet.
- No comments or docstrings on code that was not changed.
- No error handling that was not asked for.
- Prefer editing existing files over adding new ones.
- Never use em dashes.

## Testing

The spec schema plus golden fixtures are the backbone. Anything testable without an LLM call must be tested without one. LLM round trips are expensive; deterministic code and fixtures are not.
