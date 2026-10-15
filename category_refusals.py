"""Parts whose distributor category must never be believed.

``category_map.csv`` is a decision about a *bucket*: "when LCSC says
`Capacitors`, dubIS says `Passives - Capacitors`".  That is the right shape for
the general case and the wrong shape for the specific one, because distributors
misfile individual parts -- JLC files a microcontroller under *Programmers,
Emulators, And Debuggers*, a reed switch under *Linear Hall Sensors*, a
power-distribution switch under *Surge Protectors*.  A bucket-level fix for
those would break every part correctly filed under that bucket, so the fix has
to be per part.

``category_refusals.csv``, beside this module, is that list.  One row says:
*this distributor, about this part, said this thing, and it was wrong.*  The
resolver in ``domain/distributor_categories.py`` consults it before it asks the
map anything, so a refused category never reaches ``categorize()`` at all and
the part is placed by its description -- the signal that was right about it.

Why a shipped file rather than a column in the user's store:
``data/distributor_categories.csv`` already has a per-part opt-out (blank the
row and dubIS stops believing that distributor about that part), but it is
*local* and it is *temporary* -- the next fetch of that part rewrites the row
from the live page, and the misfile is back.  A misfile is a fact about the
distributor's catalogue, not about one user's inventory, so it ships with the
app and survives the fetch that caused it.

The file is data, so an outsider can add a row.  Load-time validation is
strict for the same reason ``category_map.py``'s is: the failure mode is a part
quietly filed in the wrong section, which no test on the user's machine sees.
"""
from __future__ import annotations

import csv
import hashlib
import os
import threading
from typing import Iterable, NamedTuple

from category_map import DISTRIBUTORS, CategoryMap, DistributorCategory

REFUSALS_FILENAME = "category_refusals.csv"
COLUMNS = ["part_id", "distributor", "category", "subcategory", "reason"]

_KEY = tuple[str, str, str]  # (distributor, category, subcategory)


class RefusalError(ValueError):
    """``category_refusals.csv`` says something impossible; refuse to guess."""


class Refusal(NamedTuple):
    """One row: this distributor, about this part, said this, and it was wrong."""

    part_id: str
    distributor: str
    category: str
    subcategory: str
    reason: str

    def keys(self) -> list[_KEY]:
        """The keys this refusal could have been written against.

        Same shape ``CategoryMap.candidates`` produces, for the same reason:
        the distributors record the breadcrumb differently (LCSC the bucket and
        its leaf, DigiKey and Mouser the leaf and its parent), so whoever wrote
        the row may have named either level.  Matching on the candidate set
        means a row written against the leaf refuses the part whichever way the
        client happened to store it.
        """
        return CategoryMap.candidates(self.distributor, self.category, self.subcategory)

    def applies_to(self, entry: DistributorCategory) -> bool:
        """True when this refusal covers what the distributor actually said.

        A row naming neither category nor subcategory is a blanket refusal --
        "never believe LCSC about this part" is a decision someone can make on
        purpose -- and matches anything from that distributor.  Otherwise the
        two candidate sets must intersect, so a row naming one level covers a
        stored pair that contains it and nothing else.
        """
        if self.distributor != entry.distributor.strip().lower():
            return False
        if not (self.category or self.subcategory):
            return True
        mine = set(self.keys())
        return bool(mine & set(
            CategoryMap.candidates(entry.distributor, entry.category, entry.subcategory)))


class Refusals:
    """The shipped list, indexed by part."""

    def __init__(self, rows: Iterable[dict[str, str]], source: str = "<rows>"):
        self.source = source
        self._by_part: dict[str, list[Refusal]] = {}
        for row in rows:
            self._add(row)

    def _add(self, row: dict[str, str]) -> None:
        line = row.get("_line", "?")
        part_id = (row.get("part_id") or "").strip()
        distributor = (row.get("distributor") or "").strip().lower()
        if not part_id:
            raise RefusalError(
                f"{self.source}:{line}: a refusal with no part_id refuses every "
                "part, which is what deleting the map row is for. Name one part."
            )
        if distributor not in DISTRIBUTORS:
            raise RefusalError(
                f"{self.source}:{line}: unknown distributor {distributor!r}; this "
                f"list only speaks {', '.join(DISTRIBUTORS)}"
            )
        reason = (row.get("reason") or "").strip()
        if not reason:
            raise RefusalError(
                f"{self.source}:{line}: refusal for {part_id} has no reason. A "
                "carve-out nobody can justify gets deleted by the next person "
                "who reads it, so it has to carry its own justification."
            )
        refusal = Refusal(
            part_id=part_id,
            distributor=distributor,
            category=(row.get("category") or "").strip(),
            subcategory=(row.get("subcategory") or "").strip(),
            reason=reason,
        )
        key = (refusal.distributor, refusal.category, refusal.subcategory)
        for existing in self._by_part.setdefault(part_id, []):
            if (existing.distributor, existing.category, existing.subcategory) == key:
                raise RefusalError(
                    f"{self.source}:{line}: {part_id} / {key} is refused twice "
                    f"(line above says: {existing.reason!r}). Say one thing."
                )
        self._by_part[part_id].append(refusal)

    # -- lookup ------------------------------------------------------------

    def covering(self, part_id: str) -> list[Refusal]:
        """Every refusal written against this part (usually none)."""
        return list(self._by_part.get((part_id or "").strip(), ()))

    def refusal_for(
        self, part_id: str, entry: DistributorCategory
    ) -> Refusal | None:
        """The refusal that covers this distributor's answer, if any."""
        for refusal in self.covering(part_id):
            if refusal.applies_to(entry):
                return refusal
        return None

    def refuses(self, part_id: str, entry: DistributorCategory) -> bool:
        return self.refusal_for(part_id, entry) is not None

    def parts(self) -> set[str]:
        return set(self._by_part)

    def rows(self) -> list[Refusal]:
        return [r for part in sorted(self._by_part) for r in self._by_part[part]]

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_part.values())

    def __bool__(self) -> bool:
        return bool(self._by_part)


def refusals_path() -> str:
    """Absolute path to ``category_refusals.csv``.

    Module-relative, and the filename is a *literal* in the ``os.path.join`` on
    purpose: ``tests/python/test_pyinstaller_spec.py`` and
    ``tests/python/test_container_assets.py`` both find a module's sibling reads
    by walking the AST for ``os.path.join(__file__-rooted, "literal", ...)``.
    A constant here would make this file invisible to both guards, and a
    refusal list that did not ship is worse than no refusal list -- the parts
    it protects would go back to being misfiled with nothing failing anywhere.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_refusals.csv")


_lock = threading.Lock()
_cache: dict[str, Refusals] = {}


def load(path: str | None = None) -> Refusals:
    """Read the list, once per path.

    An absent file is not an error: it means the project has recorded no
    misfiles, which is the state a fresh checkout of a fork would be in.  A
    file that exists and is malformed is a loud error, because the silent
    reading of a broken row is "no refusal", which is the bug this file exists
    to prevent.
    """
    target = path or refusals_path()
    with _lock:
        cached = _cache.get(target)
        if cached is not None:
            return cached
        if not os.path.exists(target):
            loaded = Refusals([], target)
        else:
            with open(target, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
                if missing:
                    raise RefusalError(
                        f"{target} is missing column(s) {', '.join(missing)}; a "
                        f"refusal row needs {', '.join(COLUMNS)}"
                    )
                rows = []
                for i, row in enumerate(reader, start=2):
                    row["_line"] = i
                    rows.append(row)
            loaded = Refusals(rows, target)
        _cache[target] = loaded
        return loaded


def reset() -> None:
    """Drop the cache. For tests that write their own list."""
    with _lock:
        _cache.clear()


def content_hash(path: str | None = None) -> str:
    """A digest of the refusals that affect placement.

    ``domain.distributor_categories.content_hash`` folds this in: the shipped
    list is an input to ``section`` in exactly the sense the store is, and a
    deploy that adds a row has to force the rebuild that moves the part back --
    otherwise the carve-out sits in the repo and changes nothing until some
    unrelated event happens to rebuild the cache.
    """
    rows = [(r.part_id, r.distributor, r.category, r.subcategory) for r in load(path).rows()]
    digest = hashlib.sha256()
    for row in sorted(rows):
        digest.update("\x1f".join(row).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def refuses(part_id: str, entry: DistributorCategory) -> bool:
    """See :meth:`Refusals.refuses`, on the list at :func:`refusals_path`."""
    return load().refuses(part_id, entry)
