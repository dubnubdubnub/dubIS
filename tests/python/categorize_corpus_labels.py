"""Distributor category -> the dubIS sections that count as a correct answer.

Labels for the real-part corpus in tests/fixtures/categorize-corpus/, which
test_categorize_corpus.py scores categorize.py against.  The ground truth is
the *distributor's* own category for each part; this module only translates
that taxonomy into dubIS's.

The table is not here -- it is ``category_map.csv`` at the repo root, read
through ``category_map.py``, which is also what ``categorize()`` consults at
runtime.  It used to be three Python dicts in this file, and that had two
costs: a labeling decision was invisible to whoever re-labels buckets in a
spreadsheet, and the corpus could only ever score the mapping its own test
fixture happened to carry.  One file, two readers, no way to disagree.

What the corpus adds on top of the shared reader is only its dialect:

* An answer is a **set**, because the two taxonomies genuinely overlap (an NTC
  thermistor is a sensor to one person and a resistor to another).  Runtime
  placement commits only when that set has exactly one member; scoring accepts
  any of them.
* ``None`` means no label: a distributor's catch-all bucket says nothing about
  what a part is.  "Other" in a set means dubIS has no better home for it.
* ``Unmapped`` is raised for a category with no row at all, so a rebuilt corpus
  that brings in a new category fails loudly instead of being quietly dropped
  from the denominator.
"""
from __future__ import annotations

from category_map import Unmapped, allowed, load

__all__ = ["Unmapped", "allowed", "load"]
