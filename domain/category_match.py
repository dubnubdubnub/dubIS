"""Does a search term NAME a category? The one rule, stated once, in Python.

The feature: a search term that names a category ("diodes") should show the
whole category, not only the rows whose text happens to contain the term. The
motivating case is that every Diodes row's description reads "Diode 100V 2A
Surface Mount", so searching the plural matched no haystack at all and returned
nothing.

The frontend owns the grid and implements this itself in `js/category-match.js`;
this module is the twin the CLI uses (`dubis search --category-hit`). Both are
pinned against one case table, `tests/fixtures/category-name/cases.json`, so the
grid and the CLI cannot drift into two different definitions of "names a
category" — the same shape as `domain/thresholds.py` and `js/threshold-logic.js`.

## The rule

The query's alphanumeric tokens must appear as a **contiguous run** inside the
section key's tokens.

* **Contiguous**, so "power regulators" does NOT name "ICs - Power / Voltage
  Regulators": the term is a phrase, not a bag of words. A bag-of-words match
  would let any two common words light up a category.
* **Whole tokens**, so "ic" does NOT name "ICs - Microcontrollers" and "switch"
  does NOT name "Switches". This is why the match is not `query in section`: two
  letters would then light up nine IC sections (and "ic" is a substring of
  "Switches" too), i.e. the feature would swallow the grid for the shortest,
  most common keystrokes. Substring matching is what the text search already
  does; doing it again here adds only damage.
* **No singular/plural folding**, so "capacitor" does NOT name "Capacitors".
  Folding an "s" is what turns "ics" back into "ic" and re-opens the bug above —
  the two requirements are in genuine tension, and the over-match loses. The
  plain text search still finds capacitor rows through their descriptions, so a
  singular term is a narrower result, never an empty one.
* **A parent name names its children**, because the child key literally carries
  the parent's tokens ("connectors" is a token of "Connectors > SMD"). Not a
  special case — the hierarchy falling out of the rule, and what makes typing
  "Connectors" show the whole family rather than only the ungrouped rows.

Separators are not part of a token, which is what lets one rule cover the whole
vocabulary in `data/constants.json`'s SECTION_ORDER: the dash, slash, ampersand,
comma and " > " all split.
"""

from __future__ import annotations

import re

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def name_tokens(text: object) -> list[str]:
    """The alphanumeric tokens of a category name or search term, lowercased.

    Never raises: anything that is not a string is stringified first, because a
    section value arriving from a CSV cell can be ``None`` and that means "no
    category", which is an answer, not an error.
    """
    raw = "" if text is None else str(text)
    return [t for t in _TOKEN_SPLIT.split(raw.lower()) if t]


def matches_category_name(query: object, section: object) -> bool:
    """True when ``query`` NAMES ``section`` (e.g. "Passives - Capacitors > MLCC").

    An empty query, or a row with no section, names nothing and returns False —
    it never matches "everything", which is the failure mode that would make the
    flag look like it returned the whole inventory.
    """
    needle = name_tokens(query)
    hay = name_tokens(section)
    if not needle or not hay:
        return False
    span = len(needle)
    for i in range(len(hay) - span + 1):
        if hay[i:i + span] == needle:
            return True
    return False
