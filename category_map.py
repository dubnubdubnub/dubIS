"""Translate a distributor's own category into a dubIS section.

``category_map.csv``, beside this module, is the table: one row per
(distributor, category, subcategory, section) the project has decided on.  It
is data, not code, so a non-programmer can re-label a bucket in a spreadsheet
and the next rebuild honours it -- and so one table serves two jobs:

* **Runtime placement.**  ``categorize()`` asks :func:`opinion` about a part
  whose distributor category the fetch path recorded (see
  ``domain/distributor_categories.py``) and uses the answer as the parent
  section instead of reading the description.
* **Corpus scoring.**  ``tests/python/categorize_corpus_labels.py`` reads the
  same rows as the *set* of sections that count as a correct answer, so the
  labels the corpus is scored against and the mapping the app applies cannot
  drift apart.

A bucket-level table cannot express "*this* part is misfiled", because the
bucket is shared by the parts that belong there.  That carve-out lives beside
this file, per part, in ``category_refusals.csv`` -- see ``category_refusals.py``.

Three shapes of key, and the difference is decided at load time, not guessed
at read time:

* **Several rows for one key** -- several acceptable answers, because the two
  taxonomies genuinely overlap ("Wire/Cable/DataCable" is Connectors,
  Mechanical & Hardware or Other depending on which part).  Scoring accepts
  any of them; placement commits to none, since picking one would misfile the
  rest of the bucket.  So: **no runtime opinion**, and the part falls through
  to the description rules.
* **One row with an empty ``section``** -- an explicit abstention.  Someone
  looked at the bucket and declined to guess (Mouser's "Semiconductors" spans
  eight dubIS sections).  Same runtime effect as above, but it is a *recorded*
  decision, and it stops the lookup dead rather than falling through to a
  vaguer row that might happen to fit.
* **One row with one section** -- the only shape that places a part.

A key absent from the file is a fourth case: no opinion at runtime, and
``Unmapped`` in the corpus, where a category nobody labelled is a gap in the
table rather than a decision about the part.
"""
from __future__ import annotations

import csv
import json
import os
import threading
from typing import Iterable, NamedTuple

# The filename lives in map_path()'s os.path.join and nowhere else: the asset
# guards read that literal, and a constant holding a second copy is a name they
# cannot see and nobody can keep in step.
COLUMNS = ["distributor", "category", "subcategory", "section", "note"]
DISTRIBUTORS = ("lcsc", "digikey", "mouser", "pololu")

_KEY = tuple[str, str, str]  # (distributor, category, subcategory)


class DistributorCategory(NamedTuple):
    """What one distributor calls one part, as its own site words it.

    The unit the map translates, and the unit the durable store keeps (see
    ``domain/distributor_categories.py``).  ``subcategory`` is empty when the
    distributor only exposed one level, and means the *crumb above* the leaf
    for DigiKey and Mouser but the *leaf itself* for LCSC -- which is why
    lookup tries several keys rather than assuming one shape.
    """

    distributor: str
    category: str = ""
    subcategory: str = ""


class CategoryMapError(ValueError):
    """``category_map.csv`` says something impossible; refuse to guess."""


class Unmapped(KeyError):
    """A distributor category nobody has decided a dubIS home for yet.

    Raised only by :func:`allowed`, i.e. by the corpus, where an unlabelled
    part is a gap in the table.  Runtime placement treats a missing key as no
    opinion instead: an inventory part with an unlabelled category still gets
    categorized, just from its description.
    """


def map_path() -> str:
    """Absolute path to ``category_map.csv``.

    Module-relative on purpose: in the frozen bundle this module and the CSV
    both land at the bundle root, and
    ``tests/python/test_pyinstaller_spec.py`` reads this exact ``os.path.join``
    to demand the CSV is in ``dubIS.spec``'s ``datas``.  Resolving it against
    the CWD instead works in the repo and reads an absent file in the built
    app, which is the failure that guard exists to catch.

    The filename is a literal for the same reason: this guard and
    ``tests/python/test_container_assets.py`` find a module's sibling reads by
    walking the AST, and a non-literal segment ends the path they trace -- which
    would make this file invisible to the only thing that notices an unshipped
    asset.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_map.csv")


def _legal_sections() -> frozenset[str]:
    """The parent sections a map row may name.

    Read straight from ``data/constants.json`` rather than from
    ``inventory_api.FLAT_SECTION_ORDER``: that module imports ``inventory_ops``,
    which imports ``categorize``, which imports this module, so importing it
    back would close a cycle.

    Parent level only.  A row naming ``"Passives - Capacitors > MLCC"`` would
    set a *parent* section to a string that is not a section, the frontend
    renders no header for it, and the part silently disappears from the grid
    while every test that reads the data stays green.
    """
    constants = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "constants.json")
    with open(constants, encoding="utf-8") as f:
        order = json.load(f)["SECTION_ORDER"]
    return frozenset(
        entry if isinstance(entry, str) else entry["name"] for entry in order
    )


class CategoryMap:
    """The decided rows, indexed for both jobs above."""

    def __init__(self, rows: Iterable[dict[str, str]], source: str = "<rows>"):
        self.source = source
        self.legal_sections = _legal_sections()
        # key -> the sections it allows; a key in _abstained allows nothing.
        self._by_key: dict[_KEY, set[str]] = {}
        self._abstained: set[_KEY] = set()
        self._notes: dict[_KEY, list[str]] = {}
        for row in rows:
            self._add(row)

    def _add(self, row: dict[str, str]) -> None:
        line = row.get("_line", "?")
        distributor = (row.get("distributor") or "").strip().lower()
        if distributor not in DISTRIBUTORS:
            raise CategoryMapError(
                f"{self.source}:{line}: unknown distributor {distributor!r}; this "
                f"map only speaks {', '.join(DISTRIBUTORS)}"
            )
        key = (
            distributor,
            (row.get("category") or "").strip(),
            (row.get("subcategory") or "").strip(),
        )
        if not any(key[1:]):
            raise CategoryMapError(
                f"{self.source}:{line}: {distributor} row names neither a "
                "category nor a subcategory, so no part could ever match it. "
                "(A part with no category at all is an abstention by "
                "definition -- see allowed() -- and needs no row.)"
            )
        section = (row.get("section") or "").strip()
        if not section:
            if self._by_key.get(key):
                raise CategoryMapError(
                    f"{self.source}:{line}: {key} is both an explicit abstention "
                    f"and {sorted(self._by_key[key])}. A bucket either has a home "
                    "or it does not; say one thing."
                )
            self._abstained.add(key)
        elif section not in self.legal_sections:
            raise CategoryMapError(
                f"{self.source}:{line}: {section!r} is not a dubIS section. The "
                f"parent sections are: {', '.join(sorted(self.legal_sections))}. "
                "A section the frontend does not know renders no group header, "
                "so anything filed under it would be invisible."
            )
        elif key in self._abstained:
            raise CategoryMapError(
                f"{self.source}:{line}: {key} is both an explicit abstention and "
                f"{section!r}. Say one thing."
            )
        else:
            self._by_key.setdefault(key, set()).add(section)
        note = (row.get("note") or "").strip()
        if note:
            self._notes.setdefault(key, []).append(note)

    # -- lookup ------------------------------------------------------------

    @staticmethod
    def candidates(distributor: str, category: str, subcategory: str) -> list[_KEY]:
        """Every key this part could match, most specific first.

        The distributors capture the breadcrumb differently -- LCSC stores the
        bucket and its leaf, DigiKey and Mouser the leaf and its parent -- so
        one part offers up to four keys and the *first the table knows decides*:

        1. the pair, for a row keyed on exactly this branch;
        2. the subcategory alone, which is how the LCSC table is keyed (JLC
           spells one tree several ways at the top level, so only the leaf is
           unambiguous);
        3. the category alone, for a row keyed on the bucket;
        4. the subcategory as a bucket, for a distributor whose stored
           ``subcategory`` is the level the table is keyed on (Mouser's rows
           are its top-level catalogue, and its client keeps the last two
           crumbs, so the top level only ever arrives in ``subcategory``).
        """
        d = distributor.strip().lower()
        cat = category.strip()
        sub = subcategory.strip()
        out: list[_KEY] = []
        for key in ((d, cat, sub), (d, "", sub), (d, cat, ""), (d, sub, "")):
            if key[1] or key[2]:
                if key not in out:
                    out.append(key)
        return out

    def _resolve(self, keys: list[_KEY]) -> tuple[_KEY | None, set[str] | None]:
        """First key the table knows, and what it allows.

        Stops at the first hit rather than searching on for a single-section
        answer: a subcategory that says "three sections, pick by description"
        is a more specific statement than the bucket above it, and quietly
        preferring the vaguer row because it happened to be decidable is how a
        map starts placing parts nobody decided on.
        """
        for key in keys:
            if key in self._abstained:
                return key, None
            if key in self._by_key:
                return key, set(self._by_key[key])
        return None, None

    def allowed(self, distributor: str, category_path: list[str]) -> set[str] | None:
        """Every dubIS section a part in this distributor category may land in.

        ``None`` is a real answer, not a gap: it is the map's abstention, the
        bucket saying nothing about what the part is (a distributor catch-all
        like "Other Electronic Components").  Raises ``Unmapped`` when nobody
        has written a row at all -- the corpus reads that as a labelling
        failure, because a rebuilt corpus that brings in a new category must
        be labelled rather than quietly dropped.
        """
        key = self._path_to_key(distributor, category_path)
        if not any(key):
            # The distributor recorded no category for this part. That is not a
            # gap in the table -- there is nothing to look up -- so it is the
            # same answer an abstention gives, and the old labels table spelled
            # it as its own `"": None` row.
            return None
        keys = self.candidates(distributor, *key)
        _, sections = self._resolve(keys)
        if sections is None and not any(k in self._abstained for k in keys):
            raise Unmapped(f"{distributor}: {' / '.join(category_path)}")
        return sections

    def opinion(self, distributor: str, category: str, subcategory: str = "") -> str | None:
        """The single section this distributor category places a part in.

        ``None`` -- no opinion -- whenever the table does not commit: no row,
        an abstention, or a bucket with more than one acceptable section.
        Placement then falls through to the description rules, which is what
        an ambiguous bucket needs.
        """
        keys = self.candidates(distributor, category, subcategory)
        _, sections = self._resolve(keys)
        if sections and len(sections) == 1:
            return next(iter(sections))
        return None

    def notes(self, distributor: str, category: str, subcategory: str = "") -> list[str]:
        """The sentence a human wrote next to the row that decided this.

        Read from the key that actually resolved, not the first key carrying a
        note: a reviewer asking "why is this an Other?" needs the reason that
        was applied to their part, and the subcategory row that abstained is a
        different sentence from the bucket row underneath it.
        """
        key, _ = self._resolve(self.candidates(distributor, category, subcategory))
        return list(self._notes.get(key, ())) if key else []

    def stats(self) -> dict[str, int]:
        """Row census -- used by the guard test and by corpus scoring."""
        return {
            "keys": len(self._by_key) + len(self._abstained),
            "placed": sum(1 for s in self._by_key.values() if len(s) == 1),
            "ambiguous": sum(1 for s in self._by_key.values() if len(s) > 1),
            "abstained": len(self._abstained),
        }

    @staticmethod
    def _path_to_key(distributor: str, category_path: list[str]) -> tuple[str, str]:
        """A corpus ``category_path`` -> the (category, subcategory) a client stores.

        The corpus fixtures record a full breadcrumb; the distributor clients
        record at most the last two crumbs.  Translating here, once, is what
        lets scoring and placement ask the same question of the same rows.
        """
        if str(distributor).lower() == "lcsc":
            # LCSC's fixture breadcrumb is exactly [top, leaf] and either half
            # can be empty, so keep the positions rather than filtering: the
            # table is keyed on the leaf alone *and* on the top alone, and a
            # filtered one-element path cannot tell those two apart.
            top = (category_path[0] if len(category_path) > 0 else "").strip()
            sub = (category_path[1] if len(category_path) > 1 else "").strip()
            return top, sub
        path = [p.strip() for p in category_path if p and p.strip()]
        if str(distributor).lower() == "digikey":
            # The fixtures key DigiKey on its breadcrumb *below* the top level:
            # the top level is redundant, and leaf names repeat across branches.
            path = path[1:]
        if not path:
            return "", ""
        if len(path) == 1:
            return path[0], ""
        return path[-1], " / ".join(path[:-1])


_lock = threading.Lock()
_cache: dict[str, CategoryMap] = {}


def load(path: str | None = None) -> CategoryMap:
    """Read the map, once per path.

    Cached because ``categorize()`` asks per part on every rebuild and the
    file is hundreds of rows; :func:`reset` is the escape hatch for a test that
    points the map at its own fixture.
    """
    target = path or map_path()
    with _lock:
        cached = _cache.get(target)
        if cached is not None:
            return cached
        with open(target, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise CategoryMapError(
                    f"{target} is missing column(s) {', '.join(missing)}; a map "
                    f"row needs {', '.join(COLUMNS)}"
                )
            rows = []
            for i, row in enumerate(reader, start=2):
                row["_line"] = i
                rows.append(row)
        loaded = CategoryMap(rows, target)
        _cache[target] = loaded
        return loaded


def reset() -> None:
    """Drop the cache. For tests that write their own map."""
    with _lock:
        _cache.clear()


def opinion(distributor: str, category: str, subcategory: str = "") -> str | None:
    """See :meth:`CategoryMap.opinion`, on the map at :func:`map_path`."""
    if not (category.strip() or subcategory.strip()):
        return None
    return load().opinion(distributor, category, subcategory)


def allowed(distributor: str, category_path: list[str]) -> set[str] | None:
    """See :meth:`CategoryMap.allowed`, on the map at :func:`map_path`."""
    return load().allowed(distributor, category_path)
