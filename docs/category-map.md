# The category map

`category_map.csv` is the table that says what a distributor's own category
means in dubIS. It exists because dubIS used to file a part by reading its
description string and nothing else, and a description is a marketing sentence
written by whoever listed the part.

```
distributor,category,subcategory,section,note
lcsc,Capacitors,,Passives - Capacitors,"JLC's bucket; unambiguous."
mouser,Passive Components,,"","Spans resistors, capacitors and inductors; no single parent."
```

Four columns and a note. It is CSV rather than a Python dict on purpose: the
work is re-labelling buckets, which is judgement about a catalogue, not code,
and the person doing it should be able to open a spreadsheet.

## Why the distributor's word outranks the description

The corpus in `tests/fixtures/categorize-corpus/` is 4,600 real catalogue rows
labelled with the distributor's own category. Scored against it, the
description rules land in a section their own label allows 93.9% of the time
(LCSC) and 96.4% (DigiKey). Feeding the distributor's category through this map
first, with the misfile list applied: **99.4% and 99.7%**, moving 163 LCSC
parts and 52 DigiKey parts and un-placing none.

Read that number with the caveat in [The misfile list](#the-misfile-list),
though. The label a part is scored against *is the distributor's category*, so
for a part the distributor misfiled, "correct" means "agrees with the
misfile". The honest version of the win is the three parts in
`known-misfiles.json` whose fault is the description and which the category now
places correctly:

`742-DG2750DN1-T1-GE4TR-ND` is the type case: DigiKey's own description is
`LOW VOLTAGE DUAL ANALAOG SWITCH`, misspelled, so the Switches rule claims it
and no amount of rule-writing will stop it, because the string really does say
switch. DigiKey's breadcrumb says
`Interface / Analog Switches, Multiplexers, Demultiplexers`, and that is what
the part is.

That is the argument for the map. The description rules are not being replaced;
they are being demoted to what they are good at, which is the parts nobody has
fetched and the parts their distributor files wrong -- which is why the second
list below exists.

## Three shapes of row, and the difference matters

| Rows for one key | Meaning | Scoring accepts | Placement commits to |
|---|---|---|---|
| one row, one section | a decision | that section | that section |
| several rows | the taxonomies genuinely overlap | any of them | nothing |
| one row, empty `section` | someone looked and declined | nothing (unlabelled) | nothing |

**Several rows** is not indecision, it is the truth about a bucket: LCSC's
`Wire/Cable/DataCable` really is Connectors, Mechanical & Hardware or Other
depending on which part is in it. Picking one would misfile two thirds of the
bucket, so the map abstains at runtime and the part falls through to the
description rules, which read the part rather than the bucket.

**An empty `section`** is a recorded decision, and it is different from a
missing row. `mouser,Semiconductors,,` says: this bucket spans eight dubIS
sections, mapping it to any one would misfile nearly everything under it. A
missing row says nobody looked. Both produce no opinion today; only one of them
tells the next person not to re-derive it. The Mouser pass produced 8 placements
and 20 abstentions, and the abstentions are the majority of the value: they are
why nobody has to have that argument twice.

An abstention also **stops** the lookup rather than falling through to a vaguer
row. If a subcategory says "three sections, decide by description", quietly
preferring the parent bucket because it happened to be decidable is how a map
ends up placing parts nobody decided on.

## The lookup order, and why there is one

The three distributors keep different pieces of the breadcrumb, so one part
offers up to four keys and the first the table knows decides:

1. `(category, subcategory)` -- a row keyed on exactly this branch;
2. `("", subcategory)` -- LCSC's table is keyed on the leaf alone, because JLC
   spells one tree several ways at the top level;
3. `(category, "")` -- a row keyed on the bucket;
4. `(subcategory, "")` -- for a distributor whose stored `subcategory` *is* the
   level the table is keyed on. Mouser's rows are its top-level catalogue and
   its client keeps the last two crumbs, so the bucket only ever arrives there.

| Distributor | `category` holds | `subcategory` holds |
|---|---|---|
| LCSC | `parentCatalogName` -- the catalogue bucket | `catalogName` -- its leaf |
| DigiKey | last breadcrumb crumb | the crumb above it |
| Mouser | last breadcrumb crumb, or the Search API's `Category` | the crumb above it |
| Pololu | **first** breadcrumb crumb | second crumb |

Pololu is the odd one out and is the one distributor with no rows: its client
keeps the *first* crumb, so its rows would key on a level nobody has mapped,
and no ground-truth pass over Pololu's catalogue has happened. The store accepts
pololu rows today; the map has none. `test_pololu_is_the_one_distributor_nobody_has_labelled_yet`
keeps that from being invisible.

## What the map cannot place

Two limits, both measured rather than assumed.

**Ambiguity is the ceiling.** 68% of LCSC parts and 63% of DigiKey parts get a
runtime opinion. The rest sit in buckets the table labels ambiguous, which is
the correct answer and not a gap to close by force.

**Mouser's rows mostly do not fire.** They are its *top-level* catalogue, and
its client keeps the last two crumbs of a deep breadcrumb, so a part under
`Semiconductors > Discrete Semiconductors > Diodes > Single Diodes` offers
`Single Diodes` and `Diodes` -- neither of which is a row. They take effect for
the shallow breadcrumbs where the parent crumb *is* the bucket, and for the rest
they are the vocabulary and the abstention record. Making them bite properly
needs either Mouser's root crumb captured or a leaf-level pass; both are
follow-ups, and neither is a reason to leave the 20 abstentions out.

## The veto

The distributor's word is not taken blindly. If the description both **disclaims**
the proposed section and **offers a specific one of its own**, the description
wins. (The other thing that refuses a category is per part, not per word: see
[The misfile list](#the-misfile-list).)

```python
parent = from_rules if (from_rules not in ("Other", proposed)
                        and _disclaimed(proposed, desc)) else proposed
```

`_disclaimed` reads the `exclude_desc` lists in `categorize.py` -- the only
place the project records "a description containing X is not one of these". LCSC
files a part under `Optoelectronics` and the description says `LED DRIVER`: the
LEDs rule exists precisely to refuse that phrase, the rules have a specific
answer ready (`ICs - Power / Voltage Regulators`), and they win.

Both halves are needed, and the second one was earned. Firing the veto on the
disclaimer alone cost a heart rate sensor whose LCSC description reads `Built-in
temperature sensor` -- the disclaimer is there to stop an amplifier that merely
*lists* a temperature sensor, and applying it refused a correct `ICs - Sensors`
placement in favour of `Other`. A description that says nothing leaves the
distributor's word standing, which is the entire point of asking.

## Subcategories still come from the description

The map decides the parent section and nothing else. No distributor's taxonomy
is dubIS's sub-branching, and `SUBCATEGORY_RULES` still run on whatever parent
was chosen -- so a capacitor LCSC placed in `Passives - Capacitors` still sorts
into `> MLCC` and still lands in the right place in the value-sorted view.

## Editing it

Add or change a row, then:

```bash
python scripts/score-categorize-corpus.py tests/fixtures/categorize-corpus/*.jsonl \
    --with-categories
```

It reports the correct share, how many parts get a category opinion, and every
part that *moved* versus the description alone -- which is the only way to see
what a row you just added did to a part you were not thinking about. Then
`pytest tests/python/test_categorize_corpus.py`, which holds the floors.

Load-time validation is strict, because the failure it prevents is invisible:

* a `section` that is not a parent in `SECTION_ORDER` is refused. A section the
  frontend does not know renders no group header, so the part would be filed
  somewhere nobody can scroll to -- present in the data, absent from the screen,
  and every test that reads the data stays green. Compound sections
  (`"Passives - Capacitors > MLCC"`) are refused for the same reason.
* a key that is both an abstention and a section is refused. A bucket either has
  a home or it does not.
* an unknown `distributor`, a row naming neither category nor subcategory, and a
  missing column are all refused.

## The misfile list

`category_refusals.csv`, beside `category_map.csv`, is the per-part carve-out:

```
part_id,distributor,category,subcategory,reason
C7497415,lcsc,Development Boards & Tools,"Programmers, Emulators, And Debuggers",FU6522L is a microcontroller in LQFP-48; ...
```

One row says *this distributor, about this part, said this, and it was wrong*.
`domain/distributor_categories.resolve` consults it before it asks the map
anything, so a refused category never reaches `categorize()` and the part is
placed by its description -- the signal that was right about it.

This is not a detail. JLC files a microcontroller under *Programmers*, a reed
switch under *Linear Hall Sensors*, a power-distribution switch under *Surge
Protectors*; the map places all three of those buckets, so without the list the
feature would move seven correctly-filed parts into the bucket their
distributor got wrong, and the corpus would score that as an improvement
because the corpus's ground truth is that same distributor category.

Three things about the shape:

* **It is per distributor, not per part.** Refusing LCSC's answer for a part
  hands the decision to DigiKey, and if nobody else answered, to the
  description. A hook that could only refuse a *section* would blind every
  distributor for the part at once.
* **It names what was said.** `category`/`subcategory` are the misfile, not a
  formality: leave both blank and the row refuses that distributor about the
  part forever; name them and a correct re-filing of the part is believed
  again. Matching uses the same candidate expansion as the map, so a row
  written against the leaf covers a part stored as bucket-plus-leaf and vice
  versa.
* **It ships.** `data/distributor_categories.csv` has a per-part opt-out too
  (blank the row), but that is one user's machine, and the next fetch of that
  part rewrites the row from the live page. A distributor's misfile is a fact
  about its catalogue, so it belongs with the app: `Dockerfile` and `dubIS.spec`
  both copy it, and `domain.distributor_categories.content_hash` folds its
  digest into the cache checkpoint so a deploy that adds a row forces the
  rebuild that moves the part back.

`reason` is required. A carve-out that carries no sentence saying why is a
carve-out the next reader deletes, and the part goes back to being misfiled.

The corpus holds both directions of this: `known-misfiles.json` records each
misfile's `fault` -- `distributor` or `description` -- and
`test_a_part_the_distributor_misfiled_is_never_moved_by_its_category` fails if
the map moves one, while
`test_every_misfile_the_map_could_act_on_is_refused` fails at the moment a map
row lands over a part that has no refusal yet.

## Per-part overrides on one machine

For a single inventory, the durable store is the seam: `data/distributor_categories.csv`,
one row per (part, distributor). Blanking a row stops dubIS believing that
distributor about that part, and the part falls back to its description.

That file is written when a product is fetched and read at rebuild; see
`domain/distributor_categories.py` for why it has to be durable rather than
fetched, and why a change to it forces a full rebuild instead of a catch-up.
What it cannot do is survive the next fetch of that part -- which is exactly
what `category_refusals.csv` is for.
