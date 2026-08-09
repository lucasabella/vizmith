# Extending Vizmith

What a change has to touch to be complete, per thing somebody would want to add.

This is not the house style and it is not the gate — those are
[CONTRIBUTING.md](../CONTRIBUTING.md), which says what makes a contribution *acceptable*.
This page says what makes one *finished*, and it is per extension point rather than
general, because the five things below have five different lists and a page that tried to
hold both would bury one.

Two rules run through all of it.

**The grammar grows first and everything else follows, never the reverse.** The mark set,
the operators, the aggregate functions, the truncation units and the four ways a number
reads are closed by
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

## Adding a window function

A seventh way to read a row against the other rows — a moving average, a percentile, a rank
that does not skip a tie. Six edits, and the one whose absence is *silent* is 3.

1. **`src/vizmith/spec/v1/spec.schema.json`** — the `window.fn` enum, and the `if`/`then`
   beside it if the function reads the rows in an order, since that is what requires `along`
   and what refuses it for the two that do not. There is a second `if`/`then` for the same
   job on `direction`: a key a function makes no use of is refused there rather than accepted
   and ignored, which is why `share` cannot carry one.

2. **`src/vizmith/spec/validate.py`** — a rule in `_read_errors`, *if the function's answer
   depends on the walk being unambiguous*. The two that are there are the shape to copy: a
   walk leaves exactly one dimension unpartitioned, and anything that reaches back a row
   walks a dimension rather than a measure, because two rows can hold the same measure and
   which of them comes first is then the source's to decide. `LAGGING` is that list.

3. **`src/vizmith/query.py`, `_window()`** — the template, and this is the quiet one: what
   the source is asked to compute. A frame that is left to the default is a frame the
   dialects choose, and a division that is not promoted is integer division on PostgreSQL —
   a column of zeroes rather than an error. Neither fails a test that only checks the SQL
   compiles.

4. **`src/vizmith/ask.py`, `INSTRUCTIONS`** — the sentence naming it. The schema says the
   word exists; the instructions are where a model learns which question it answers, and
   they are sent whether or not the endpoint honours a schema.

5. **`web/src/spec/spec.ts`, `WINDOW_FNS`** — the browser's one copy of the enum.
   `mirrors.test.ts` reads the schema and fails when the two disagree, so this step
   announces itself. Nothing in the browser *writes* a window; the Values well names the one
   the chart draws, which is `windowFor`.

6. **A fixture in `tests/fixtures/specs/valid/`** —
   `test_valid_set_covers_every_window_the_schema_allows` in `tests/test_spec_validation.py`
   fails the moment step 1 lands without one, the same gate the marks get. The fixture is
   then compiled, run against DuckDB, checked against the result set contract and rendered,
   which is most of the coverage for one JSON file. Add the refusal case to
   `tests/fixtures/specs/invalid/` too where step 2 added a rule, with its expected message
   in `EXPECTED_ERROR`.

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

## Adding a control that crosses a dashboard

There is one so far — the filter bar — and the shape it settled on is the one to copy.

1. **The state goes on `Arrangement`** in `web/src/dashboard/dashboard.ts`, not on a tile
   and not inside a spec. A tile holds the question somebody built; anything that narrows or
   reframes the page has to come off again without leaving a trace in a spec nobody edited.
2. **The rewrite is a pure function** in `web/src/dashboard/across.ts` taking a spec and
   answering the spec that would run, plus what it could not do. Memoise the result in the
   view: a tile fetches on the spec object it is handed, so a fresh one per render is a
   query per render.
3. **The store learns the field** in `src/vizmith/dashboards.py`, and it is judged by the
   grammar rather than by a rule written twice — `validate_filters` builds a validator out of
   the schema's own `$defs`. A key absent from a file saved before the field existed reads as
   "none of it", because the store refuses a shape it does not recognise and every dashboard
   saved until now has to keep opening.
4. **A tile the control cannot reach says so, on the tile.** This is the rule that does not
   move. Do not guess a join to make it reach — a join nobody confirmed produces a plausible
   number rather than an error — and do not hide the tile, because a dashboard that drops
   half of itself is one nobody can read.

## Adding a view

Two edits, both inside `web/src/App.tsx`.

1. **`VIEWS`** — the id, in the position the rail should draw it. This is also the `ViewId`
   union, because the union is derived from the list.
2. **`views`** — the entry: a `label` the rail button is named by, an `icon` from
   `web/src/icons.tsx`, `page` for whether the canvas scrolls, and `render`.

The second is not optional in the way a forgotten registration usually is: `views` is a
`Record<ViewId, View>`, so an id in `VIEWS` with no entry is a type error naming the missing
key rather than a rail button that switches to a blank canvas. The rail uses
`aria-current="page"` rather than `aria-pressed`, because buttons that choose a view are
navigation.

`page: true` is the Data and Dashboards surface — padded and scrolling. `page: false` is the
chart canvas, a column that fits, and it is the right answer only for something that must
not be scrolled to. `docs/design.md` has the argument; there are two surfaces and this is
the switch between them.

Anything substantial in the new view belongs in `web/src/views/`, the way `Dashboards.tsx`
and `Data.tsx` do, with `App.tsx` holding the state that outlives the view. State that lives
in a view is state that is thrown away when the view is, which is why the dashboard being
arranged does not live in `Dashboards.tsx`. If what the view needs is the spec on screen,
the request that produced it or the way back from it, that is `useAsked` in
`web/src/asked.ts` and it is already held once — do not start a second copy.

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

## Adding a step to a question

`/api/ask` says which part of a question is running, as server-sent events. A fourth part —
a cache being filled, a second model call — is three places:

1. **`STEPS` in `src/vizmith/ask.py`** — the name, in the tuple. The vocabulary and nothing
   a person reads, the same way `spoke` names which part refused.
2. **`answering()` in `src/vizmith/api.py`** — a `yield Step("…")` where the work starts, or
   a `yield` inside the loop that does it, the way `asking()` reports each attempt.
3. **`STEPS` in `web/src/api.ts` and `STEP` in `web/src/outcome.ts`** — the name again, and
   the sentence somebody waiting reads. `STEP` is a `Record` keyed by the union, so a name
   with no sentence is a compile error, and `mirrors.test.ts` reads the Python tuple and
   fails when the two lists disagree.

Nothing else changes. The JSON body is the last event of the same sequence, so a step added
here is invisible to a caller that did not ask for the stream.

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
