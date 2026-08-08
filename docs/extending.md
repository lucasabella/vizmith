# Extending Vizmith

What a change has to touch to be complete, per thing somebody would want to add.

This is not the house style and it is not the gate — those are
[CONTRIBUTING.md](../CONTRIBUTING.md), which says what makes a contribution *acceptable*.
This page says what makes one *finished*, and it is per extension point rather than
general, because the five things below have five different lists and a page that tried to
hold both would bury one.

Two rules run through all of it.

**The grammar grows first and everything else follows, never the reverse.** The mark set,
the operators, the aggregate functions and the truncation units are closed by
`src/vizmith/spec/v1/spec.schema.json`, which is the grammar and the only judge of a spec.
A renderer that can draw something the schema does not allow is a renderer nobody can reach;
a schema that allows something no renderer draws is a chart that comes back blank. The
schema is therefore the first edit in every list here, and the suite is arranged so that
making it fails several tests at once — which is the point. Read the failures as the
checklist.

**The browser never judges a spec.** `/api/validate` decides, and an opinion in the
interface is one that can disagree with the one that counts. Everything the browser holds
about the grammar is vocabulary rather than judgement — a list of marks for a control, a
list of operators for a filter it builds — and `web/src/mirrors.test.ts` holds every one of
those lists against the schema, so the copy cannot drift without the frontend suite going
red.

## Adding a mark

A heatmap, a boxplot, a horizontal bar. Longest of the five lists, and the two steps whose
absence is *silent* are 3 and 6 — a mark no rule can refuse, and a chart that draws nothing
without raising.

1. **`src/vizmith/spec/v1/spec.schema.json`** — the `chart.mark` enum. The grammar. Nothing
   downstream is reachable until this allows the word.

2. **`src/vizmith/spec/validate.py`** — a semantic rule, *if the new mark constrains its
   encoding*. Today no rule there is about a mark: the encoding rules are about channels —
   the value axis carries a measure, a chart with no `x` draws one figure and has nothing to
   colour. A mark that needs both `x` and `y` and a colour, or that refuses a stack, says so
   here, because this is the pass that runs before anything reaches a source.

3. **`src/vizmith/critique.py`, `misreads()`** — what makes the mark *refusable*. This one
   rule set is what the second opinion offers suggestions from and what `vizmith eval
   --repair` scores against, so a mark absent from it is a mark no rule can ever refuse and
   no evaluation can ever count. Nothing fails when you skip this. It is part of adding a
   mark rather than a follow-up.

4. **`web/src/spec/spec.ts`, `MARKS`** — the browser's one copy of the enum, as a tuple.
   `mirrors.test.ts` reads the schema and fails if this list and the schema's disagree, so
   this step announces itself. There is no second copy in the renderer: `option.ts` imports
   `Mark` from here.

5. **`web/src/chart/option.ts`** — the renderer, which is two edits:
   - `SERIES_TYPE`, the mark-to-ECharts-series map. It is a `Record` keyed by the marks, so
     step 4 makes this a compile error until it is filled in — except for `arc`, which is
     excluded by name because a pie is a branch in `buildOption` rather than a lookup. A mark
     that needs its own branch is the same case.
   - `markStyle`, where the mark has geometry the design system fixes.

6. **`web/src/chart/option.ts`'s `SeriesOption`, and `web/src/chart/Chart.tsx`'s
   `echarts.use([...])`** — the composed option type and the runtime registration. These two
   must agree, and this is the quiet one: *a series type that is used and not registered
   draws nothing rather than raising.* The `echarts` barrel would register everything and
   costs 1.37 MB inside the wheel, which is why the registration is explicit and why it is
   on this list.

7. **A fixture in `tests/fixtures/specs/valid/`** — the cheap end of the test for all of the
   above, and not optional:
   `test_valid_set_covers_every_mark_the_schema_allows` in `tests/test_spec_validation.py`
   fails the moment step 1 lands without one. Every valid fixture is then validated,
   executed against DuckDB and checked against the result set contract by `tests/test_api.py`,
   and rendered by `web/src/chart/option.test.ts`, which builds an option for each. One JSON
   file buys all of that.

8. **A case in `option.test.ts`** for whatever the fixture does not reach — a stack, an
   absent `x`, a colour channel — and, where step 6 registered something new, a painted case
   in `tests/test_interface.py`. The browser suite is the only tier that would catch a
   missing registration, because a blank canvas is what an unregistered series looks like
   and jsdom has no canvas to be blank.

## Adding a well

A well is a drop target that rewrites the spec. Four edits, all in the browser.

1. **`web/src/spec/spec.ts`, `WELLS`** — the tuple, which is the order they appear in.
2. **`place()`** — what a drop into it does to the draft.
3. **`clear()`** — the way back out. A drop with no way back is a trap, so this is not
   optional.
4. **`web/src/panels/Wells.tsx`** — the block that renders it, and whatever control it shows
   for what the drop inferred. An inference nobody can see or change is the quiet kind of
   wrong this project exists to avoid, which is why the aggregate and the truncation unit are
   both shown in the well and both changeable there.

The rule stated in that file's docstring holds for anything added to it: **nothing there may
judge the result.** A well writes a spec and `/api/execute` decides whether it is legal. A
drop that produced no measure yet is not sent at all — it is unfinished rather than invalid,
and answering it with a required-property error would put a refusal on screen for every drop
but the last.

## Adding a view

Three edits, all inside `web/src/App.tsx`: the `view` state's union, a button in the rail
that sets it, and a branch in the render. The rail uses `aria-current="page"` rather than
`aria-pressed`, because three buttons that choose a view are navigation.

The reason this list is short is not that it is well factored — see issue #158. Anything
substantial in the new view belongs in `web/src/views/`, the way `Dashboards.tsx` and
`Data.tsx` do, with `App.tsx` holding the state that outlives the view. State that lives in
a view is state that is thrown away when the view is, which is why the dashboard being
arranged does not live in `Dashboards.tsx`.

## Adding an endpoint

Two ends, and each has exactly one place.

1. **`src/vizmith/api.py`** — the route. Where it can refuse for a reason behind the server,
   it answers through `refused(spoke, …)`, which names *which part* refused: `source`,
   `model`, `spec`, or `rations`. Every refusal comes back as the same `errors` list the
   validator's own refusal uses, so the interface keeps one way to show one. Where it spends
   money or a query, it takes `Depends(rationed(...))`.
2. **`web/src/api.ts`** — a function and its response type. Nothing else in the browser may
   call `fetch`: this file is the transcript of what the server answers, and a request made
   somewhere else grows a second reading of what a failure is, which is exactly how the
   canvas and a dashboard tile came to show one refusal two different ways.

If the endpoint can produce a new `spoke`, add the sentence for it to `SAID` in
`web/src/outcome.ts` — `mirrors.test.ts` reads every `spoke` the server can write out of
`api.py` and fails when the browser has no sentence for one. Interpreting a refusal is
`outcome.ts`'s job and never a component's; `refusal()` there is the one reading, and the
canvas, a dashboard tile and the dashboard save bar all go through it.

## Adding a source

A connector is a module in `src/vizmith/sources/`, an entry in `KINDS` with the settings its
constructor takes in order, and those settings in `config.py`. Nothing imports it until it is
the configured kind, so a checkout pointed at a warehouse does not install a database engine.

What it owes beyond the `Catalog` protocol:

- **A `Scope`** — the levels the source puts in front of a table and the values configured
  for them. `query.py` resolves every reference in a spec through it before a name reaches a
  source, which is where the promise that a spec cannot address anything outside the
  configured scope is kept. `tests/test_scope.py` holds every catalog to it.
- **A `Dialect`** — how an identifier is quoted, how a set of distinct values is collected,
  and the approximate distinct count where the source has one. The profiler writes one query
  and the catalog runs it, so a second source is a dialect rather than a second profiler.
- **The value contract** — a decimal is a `float`, a date is a `datetime.date`, a truncated
  value is a timestamp, and a null is `None` whatever the column's type. `conform` in
  `catalog.py` is what a source whose client answers in text or in objects calls to keep it,
  and `tests/test_result_set.py` is what says it did.
- **A freshness token, or an honest `None`** — `modified` keys the profile cache. A token
  that does not move when the data does is worse than no token, because the cache then serves
  a stale profile forever; `None` turns the cache off, which DuckDB does deliberately.
- **A row in `docs/compatibility.md`**, with the date it was checked and how. Three of the
  five connectors have never been run against a real project, account or server, and that
  table is where that is said rather than implied.

## Where the reasoning lives

This page says what to touch. [DESIGN.md](../DESIGN.md) says why each of these is a separate
decision in the first place — why the mark set is closed by the schema, why the browser
copies a rule instead of deriving it, why the critique refuses rather than ranks, why a
source is configuration and never a request. If a step here looks like duplication worth
removing, the entry for it is usually already there, arguing with itself.
