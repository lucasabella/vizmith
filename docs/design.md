# The design system

What the interface is allowed to look like, and why. Everything here governs committed
code: `web/src/styles/tokens.css` declares it, `web/src/styles/shell.css` spends it,
`web/src/chart/option.ts` holds a second copy of the part a canvas needs, and
`web/src/mirrors.test.ts` is what keeps the two copies honest.

This file exists because the stylesheet used to open by citing a document that is not in
this repository. The tokens were the copy and the original was somewhere else, so a
contributor adding a panel, a chart type or a status indicator had to reconstruct the rules
from component docstrings and infer backwards. The rules are here now, and the docstrings
cite this file rather than restating it.

The numbers in the three tables below are measured rather than asserted. `docs/palette.py`
reads `tokens.css` and prints them, so a token that moves without this file being
regenerated is visibly stale:

```
.venv/bin/python docs/palette.py
```

## The shape of the thing

Vizmith looks like an instrument rather than a document. A dark chrome frames a light work
surface; the work surface is where data lives and the chrome is where controls live, and
the two never trade places. Radii are 2px on a control and 3px on a card, which is nearly
square on purpose: the corner is there to stop an edge looking accidental, not to soften
anything. There is one shadow, and it is 6% of the ink at one pixel.

Two font stacks, and which one applies is a rule rather than a taste. `--ui` is for anything
the interface says: labels, buttons, headings, prose. `--mono` is for anything the warehouse
says: a table name, a column name, a figure, a category that came out of a result set, a
spec. That is why the chart's axis labels are mono — a category name and a number are both
the warehouse talking — and why the button beside the chart is not.

## Surfaces, and the ink that is legal on each

A surface is something to draw on. There are six, and they divide in two.

| token | hex | what it is |
|---|---|---|
| `--chrome` | `#1e2b38` | the frame: rail, top bar, page strip |
| `--chrome-2` | `#27384a` | a raised or hovered thing inside the frame |
| `--chrome-3` | `#34485e` | the selected thing inside the frame |
| `--canvas` | `#e4e9ee` | the ground the work surface sits on |
| `--surf` | `#ffffff` | a card, a panel, a chart's plotting area |
| `--surf-2` | `#f4f6f9` | a header row, a well, a secondary band on a card |

Ink follows the surface it is on, and the pairing is not free choice. On the light surfaces,
`--ink` is body text and anything a person reads to get an answer, `--ink-2` is a label or a
secondary line, and `--ink-3` is for text that is decoration rather than information —
placeholder, disabled, a unit suffix. On the dark chrome, `--chrome-ink` and
`--chrome-ink-2` are the same two ranks. Nothing else is ink. A colour from the categorical
order is never text, and a status colour is never body copy.

Measured, with WCAG 2.1 contrast against the two surfaces each is worn on:

| token | hex | on --surf | on --chrome |
|---|---|---|---|
| `--ink` | `#14202b` | 16.52 | 1.15 |
| `--ink-2` | `#5c6b7a` | 5.47 | 2.63 |
| `--ink-3` | `#8c99a6` | 2.91 | 4.95 |
| `--chrome-ink` | `#c3d2df` | 1.54 | 9.33 |
| `--chrome-ink-2` | `#7d93a8` | 3.18 | 4.53 |
| `--brass` | `#e8b04b` | 1.95 | 7.37 |
| `--good` | `#0ca30c` | 3.35 | 4.29 |
| `--warning` | `#fab219` | 1.83 | 7.85 |
| `--serious` | `#ec835a` | 2.64 | 5.46 |
| `--bad` | `#d03b3b` | 4.80 | 3.00 |

Two of those rows are the rule stated as arithmetic. `--ink-3` at 2.91 on `--surf` is under
the 4.5:1 body-text floor and is under the 3:1 large-text floor by a hair, which is why it
is only ever worn by text that carries no information: a placeholder that repeats what the
label already said, a disabled control, a unit. Text a person has to read to get an answer
is `--ink` or `--ink-2`, and both clear 4.5:1. `--brass` at 1.95 on `--surf` is not text on
a light surface at all; it is the accent the chrome wears, where it is 7.37.

The status four — `--good`, `--warning`, `--serious`, `--bad` — are for a state, not for a
quantity. A chart never wears them, because a chart's colour means *which series* and a
status colour means *how bad*, and one channel cannot carry both. `--warning` at 1.83 on
`--surf` is a fill or a rule and never a word; where a warning has words, the words are ink
and the colour is the thing beside them. Which is the next rule.

## Provenance is never colour alone

Where the interface says where something came from, or how sure it is, it says it in a word.
The colour may come too, and the colour may never come alone.

`Data.tsx` is the case that made the rule: a relationship the source declared and a
relationship Vizmith guessed are different in kind — one is a fact and one is a suggestion
that has to be confirmed before a join can use it — and encoding that difference in a
swatch means a person who cannot see the swatch is looking at a screen where facts and
guesses are the same thing. Provenance was encoded in colour alone twice in this project and
was cut both times. Each section carries its word: *Declared*, *Suggested*, *Confirmed*.

The same applies to a chart's refusal, an error, a stale badge, and anything else where the
difference between two states matters. If you can describe the difference only by pointing
at the colour, it is not done.

## The categorical order

Eight colours, and it is an **order** rather than a palette to choose from.

| slot | token | hex |
|---|---|---|
| 1 | `--series-1` | `#2a78d6` |
| 2 | `--series-2` | `#eb6834` |
| 3 | `--series-3` | `#1baf7a` |
| 4 | `--series-4` | `#eda100` |
| 5 | `--series-5` | `#e87ba4` |
| 6 | `--series-6` | `#008300` |
| 7 | `--series-7` | `#4a3aa7` |
| 8 | `--series-8` | `#e34948` |

**A series takes the next free slot.** First series is slot 1, second is slot 2, and so on.
Nothing takes a slot for looking better in it, nothing is skipped for being close to a
brand colour, and nothing is reassigned to make one chart nicer.

### The gate the order clears

The sequence is a colour vision safety mechanism. Most charts have two, three or four
series, so the pairs a reader has to tell apart are overwhelmingly the *adjacent* ones — a
two-series chart is slots 1 and 2, a three-series chart adds slot 3. The order is chosen so
that consecutive slots stay far apart once a dichromat's eye has been through them.

The gate is: **adjacent slots separate by at least 25 CIE76 dE under protanopia and
deuteranopia simulation**, the two common dichromacies, which together are about 6% of men.
Measured, with the Viénot, Brettel and Mollon (1999) simulation:

| pair | normal | protan | deutan | tritan |
|---|---|---|---|---|
| 1–2 | 114 | 107 | 129 | 91 |
| 2–3 | 103 | 28 | 52 | 92 |
| 3–4 | 90 | 53 | 68 | 70 |
| 4–5 | 84 | 82 | 73 | 9 |
| 5–6 | 114 | 64 | 49 | 68 |
| 6–7 | 141 | 121 | 111 | 18 |
| 7–8 | 96 | 95 | 114 | 85 |

The worst adjacent pair is 2–3 at 28 under protanopia, which clears the gate and does not
clear it by much. That is what "assigning out of order breaks the gate" means concretely:
the order has almost no slack in it, and a swap that looks harmless is a chart two readers
in a hundred cannot read.

### What the gate does not promise

Two things, and both are stated here rather than discovered.

**Tritanopia is not gated.** Slots 4 and 5 separate by 9 dE under tritanopia, and 6 and 7 by
18. Tritanopia is very rare — on the order of one in ten thousand, and not sex-linked — and
a palette that cleared all three dichromacies at eight slots would be a palette of greys and
blues. The trade is taken deliberately, and it is one of the reasons the escape hatch below
is mandatory rather than nice.

**Non-adjacent pairs are not gated either.** The worst pair anywhere in the order is:

| vision | dE | slots |
|---|---|---|
| normal | 22 | 2 and 8 |
| protan | 10 | 2 and 6 |
| deutan | 11 | 3 and 5 |
| tritan | 9 | 4 and 5 |

So a chart using all eight slots is a chart where some pair is hard for some reader, whatever
the order. Colour is not carrying the whole load at eight series, which is the second reason
the escape hatch is mandatory.

### There is no ninth colour

Past slot 8 the renderer refuses to draw, and the message names the count, the cap and the
field that sets it. The two alternatives were both rejected and the reasoning is in
[DESIGN.md](../DESIGN.md): cycling puts one colour on two entries of one legend, which is a
chart that lies about which series it is, and folding the tail into "Other" is aggregation,
which lives in the query and never in the renderer.

## The contrast rule, and the escape hatch that makes the palette legal

**A mark that fails 3:1 against the surface it is drawn on must carry a visible label, or
the same numbers must be readable as text.**

Measured against every surface:

| slot | hex | --surf | --surf-2 | --canvas | --chrome | --chrome-2 | --chrome-3 |
|---|---|---|---|---|---|---|---|
| 1 | `#2a78d6` | 4.42 | 4.08 | 3.61 | 3.26 | 2.72 | 2.13 |
| 2 | `#eb6834` | 3.20 | 2.96 | 2.62 | 4.50 | 3.75 | 2.94 |
| 3 | `#1baf7a` | 2.82 | 2.60 | 2.30 | 5.12 | 4.26 | 3.34 |
| 4 | `#eda100` | 2.17 | 2.00 | 1.77 | 6.65 | 5.54 | 4.34 |
| 5 | `#e87ba4` | 2.69 | 2.49 | 2.20 | 5.35 | 4.46 | 3.49 |
| 6 | `#008300` | 4.95 | 4.57 | 4.05 | 2.91 | 2.43 | 1.90 |
| 7 | `#4a3aa7` | 8.56 | 7.90 | 7.00 | 1.68 | 1.40 | 1.10 |
| 8 | `#e34948` | 3.95 | 3.65 | 3.24 | 3.64 | 3.03 | 2.38 |

Charts are drawn on `--surf`, which is the column that governs. Three slots are under 3:1
there: **3 at 2.82, 4 at 2.17, and 5 at 2.69**. That is the sentence
`chart/Table.tsx` and `chart/Visual.tsx` are paraphrasing.

An interior segment of a stacked bar cannot carry a label — it is a few pixels tall on a
long tail and the label lands outside the segment it belongs to — so the first branch of the
rule is unavailable for exactly the chart that most needs eight colours. The second branch
is what is taken: **the Table tab.** It shows every row the chart was drawn from, in the
builder's column order, and it is one control away from any chart.

So the Table tab is not a convenience and is not optional. It is the compliance mechanism
that makes slots 3, 4 and 5 legal, and it is also what makes the ungated tritan pair and the
weak all-pairs minimum above survivable. **A view that draws marks in the series colours and
has no table beside it is a view that fails the contrast rule.** If you add one, add the
table with it, and if a table is genuinely impossible in that context, the marks there are
restricted to the slots that clear 3:1 on `--surf`: 1, 2, 6, 7 and 8.

`--canvas` is a worse surface than `--surf` for a mark — slot 2 falls to 2.62 there and slot
1 to 3.61 — which is why the chart's plotting area is `--surf` and never the ground it sits
on. Marks on the chrome are a different column of the table and a different problem; nothing
draws them today, and anything that starts to should read the `--chrome` column first, where
slot 7 is 1.68 and effectively invisible.

## The second copy, and what keeps it honest

ECharts paints onto a `<canvas>`, and a canvas cannot read a CSS custom property. So
`chart/option.ts` writes out, as string literals, the eight series colours and the chrome
the chart wears: `SURF`, `INK`, `INK_2`, `INK_3`, `RULE`, `RULE_2`, `UI`, `MONO`. That is a
second copy of a token by necessity, and every second copy in this project has something
that fails when the two drift.

Here it is `web/src/mirrors.test.ts`, which reads `tokens.css` as text and asserts that the
eight series colours match **in order** — the order being the safety mechanism, it is the
order that is asserted and not the set — and that each chrome constant equals the token it
copies. Both sides are read rather than assumed, so a constant renamed away fails as a
missing mirror rather than passing as two undefineds that agree.

If you change a colour, change it in `tokens.css` and let that test tell you what else to
change. If you add a chrome constant to `option.ts`, add its row to the mirror.

## Adding to any of this

**A new surface, ink or status token.** Add it to `tokens.css` with a comment saying what it
is for, add it to the `SURFACES` or `INKS` tuple in `docs/palette.py`, run the script, and
paste the regenerated table into this file. Then check the rules above: body text clears
4.5:1 on every surface it can appear on, a mark clears 3:1 or has a label or a table, and if
the token encodes a state or a provenance, the word ships with it.

**A ninth series colour.** The answer is no, and the reason is that there is nowhere to put
it: the renderer refuses past eight rather than cycling, so a ninth token would be a colour
nothing can assign. If the cap itself is what you want to move, that is a change to the
refusal in `option.ts` and to the argument in [DESIGN.md](../DESIGN.md), not a change to the
palette, and it has to answer what eight already fails to answer — the all-pairs minimum
above is 10 dE for a protanope at eight slots, and adding a ninth makes that worse, not
better.

**A colour swapped within the order.** Run `docs/palette.py`, check the adjacent-pair table
against the 25 dE gate, check the new colour's contrast on `--surf`, and if it lands under
3:1, say so here — the count of low-contrast slots is quoted in `Table.tsx` and
`Visual.tsx`, and those docstrings are wrong the moment it changes.

**A new view that draws data.** It needs the table, or it needs to stay inside slots 1, 2, 6,
7 and 8. There is no third option.
