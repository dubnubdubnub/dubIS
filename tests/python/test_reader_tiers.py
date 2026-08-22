"""Tests for the pinned reader model table and tier selection.

The table's job is to be *auditable*: a wrong sha256 here is not caught by CI, it is
caught on the operator's machine after a multi-GiB download. So these tests check the
shape and the invariants of every pinned field — 64 hex characters, a 40-hex commit sha
rather than a branch name, a non-empty mmproj, a plausible byte count — and the
selection tests encode the design doc's table plus the free-not-total policy rule.

The sha256 values themselves were verified against the live HuggingFace API (the
``blobs=true`` LFS oid and the ``x-linked-etag`` on the pinned resolve URL, which agree)
when the table was written; that is a network check and does not belong in this suite.
"""

from __future__ import annotations

import re
from dataclasses import fields

import pytest

import reader_tiers
from reader_memory import BudgetInfo

GIB = 1024 ** 3
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def tier(name_fragment):
    matches = [t for t in reader_tiers.TIERS if name_fragment in t.name]
    assert len(matches) == 1, f"expected exactly one tier matching {name_fragment!r}"
    return matches[0]


# ============================================================= the selection table


def test_below_five_gib_has_no_tier():
    """The design doc's floor: under 5 GiB the reader stays off and tesseract stays."""
    for free in (0, 1 * GIB, 4 * GIB, 5 * GIB - 1):
        assert reader_tiers.choose_tier(free) is None, f"{free} should not get a tier"


def test_five_gib_gets_the_3b():
    assert reader_tiers.choose_tier(5 * GIB) is tier("3b")


@pytest.mark.parametrize("free", [5 * GIB, 6 * GIB, 8 * GIB, 10 * GIB - 1])
def test_five_to_ten_gib_gets_the_3b(free):
    assert reader_tiers.choose_tier(free) is tier("3b")


def test_ten_gib_gets_the_7b():
    assert reader_tiers.choose_tier(10 * GIB) is tier("7b")


@pytest.mark.parametrize("free", [10 * GIB, 20 * GIB, 24 * GIB, 96 * GIB])
def test_ten_gib_and_up_gets_the_7b(free):
    assert reader_tiers.choose_tier(free) is tier("7b")


def test_the_y740_2060_lands_on_the_3b_only():
    """RTX 2060: 6 GiB total, ~4 of 6 usable per the design doc -> 3B, or nothing."""
    six_gib_card_idle = BudgetInfo(6 * GIB, 6 * GIB, "nvidia-smi", unified=False)
    assert reader_tiers.choose_tier(six_gib_card_idle) is tier("3b")
    six_gib_card_in_use = BudgetInfo(6 * GIB, 4 * GIB, "nvidia-smi", unified=False)
    assert reader_tiers.choose_tier(six_gib_card_in_use) is None


# =============================================== policy rule 1: free, not total


def test_selection_reads_free_not_total():
    """A 24 GiB card with a 27B already resident must not be offered the 7B."""
    crowded = BudgetInfo(24 * GIB, 4 * GIB, "nvidia-smi", unified=False)
    assert reader_tiers.choose_tier(crowded) is None

    roomy = BudgetInfo(24 * GIB, 20 * GIB, "nvidia-smi", unified=False)
    assert reader_tiers.choose_tier(roomy) is tier("7b")


def test_a_small_card_that_is_entirely_free_still_gets_its_tier():
    """The mirror image: total must not cap a choice that free supports."""
    assert reader_tiers.choose_tier(BudgetInfo(6 * GIB, 6 * GIB, "x", unified=False)) \
        is tier("3b")


def test_unknown_headroom_declines_every_tier():
    """Capacity without headroom (the Windows registry probe alone) is not a pass."""
    capacity_only = BudgetInfo(24 * GIB, None, "windows-registry", unified=False)
    assert reader_tiers.choose_tier(capacity_only) is None


def test_no_budget_declines():
    assert reader_tiers.choose_tier(None) is None


def test_negative_free_declines():
    assert reader_tiers.choose_tier(-1) is None
    assert reader_tiers.choose_tier(BudgetInfo(24 * GIB, -1, "x", unified=False)) is None


def test_a_bool_is_not_a_byte_count():
    """`True` is an int in Python; treating it as 1 byte of free memory is nonsense."""
    assert reader_tiers.choose_tier(True) is None
    assert reader_tiers.choose_tier(False) is None


def test_a_budget_with_a_non_numeric_free_declines():
    class Weird:
        free_bytes = "lots"

    assert reader_tiers.choose_tier(Weird()) is None


def test_apple_unified_budget_selects_from_the_wireable_figure():
    """An M4 Max reports 128 GiB total and ~90 GiB wireable; either way it is the 7B."""
    mac = BudgetInfo(137438953472, 96207267430, "apple-unified", unified=True)
    assert reader_tiers.choose_tier(mac) is tier("7b")


# ================================================= every tier is fully pinned


def test_every_tier_field_is_populated():
    assert reader_tiers.TIERS, "the table must not be empty"
    for t in reader_tiers.TIERS:
        for f in fields(t):
            value = getattr(t, f.name)
            assert value not in ("", None), f"{t.name}.{f.name} is unset"


@pytest.mark.parametrize("t", reader_tiers.TIERS, ids=lambda t: t.name)
def test_tier_has_repo_revision_and_both_filenames(t):
    assert "/" in t.repo, "repo must be <org>/<name>"
    assert COMMIT_SHA_RE.match(t.revision), \
        f"{t.name} revision {t.revision!r} must be a 40-hex commit sha, not a branch"
    assert t.weights_file.endswith(".gguf")
    assert t.mmproj_file.endswith(".gguf")
    assert t.weights_file != t.mmproj_file


@pytest.mark.parametrize("t", reader_tiers.TIERS, ids=lambda t: t.name)
def test_tier_has_a_sha256_for_both_files(t):
    """Both files, not just the weights: an unverified projector is an unverified read."""
    assert SHA256_RE.match(t.weights_sha256), f"{t.name} weights sha256 malformed"
    assert SHA256_RE.match(t.mmproj_sha256), f"{t.name} mmproj sha256 malformed"
    assert t.weights_sha256 != t.mmproj_sha256


@pytest.mark.parametrize("t", reader_tiers.TIERS, ids=lambda t: t.name)
def test_tier_mmproj_is_a_real_projector(t):
    """--mmproj is mandatory: without it llama-server is text-only and ignores images."""
    assert "mmproj" in t.mmproj_file.lower()
    assert t.mmproj_bytes > 0


@pytest.mark.parametrize("t", reader_tiers.TIERS, ids=lambda t: t.name)
def test_tier_has_a_default_ctx_size(t):
    assert t.ctx_size > 0
    assert t.ctx_size % 1024 == 0


@pytest.mark.parametrize("t", reader_tiers.TIERS, ids=lambda t: t.name)
def test_tier_quant_is_the_pinned_q4_k_m(t):
    """The design doc pins Q4_K_M; a silent drift to Q8_0 doubles the download."""
    assert "Q4_K_M" in t.weights_file


@pytest.mark.parametrize("t", reader_tiers.TIERS, ids=lambda t: t.name)
def test_tier_download_size_fits_under_its_floor(t):
    """Weights + projector must leave room for KV cache and compute buffers."""
    assert t.total_download_bytes == t.weights_bytes + t.mmproj_bytes
    assert t.total_download_bytes < t.min_free_bytes, \
        f"{t.name} downloads {t.total_download_bytes} into a {t.min_free_bytes} floor"


def test_measured_sizes_match_the_design_doc_table():
    """3B: 1.80 + 1.25 GiB. 7B: ~4.4 + ~1.3 GiB. Guards a copy-paste of the wrong file."""
    three_b = tier("3b")
    assert round(three_b.weights_bytes / GIB, 2) == 1.80
    assert round(three_b.mmproj_bytes / GIB, 2) == 1.25
    seven_b = tier("7b")
    assert 4.0 < seven_b.weights_bytes / GIB < 5.0
    assert 1.2 < seven_b.mmproj_bytes / GIB < 1.4


def test_thresholds_are_the_design_doc_bands():
    assert tier("3b").min_free_bytes == 5 * GIB
    assert tier("7b").min_free_bytes == 10 * GIB
    assert reader_tiers.MIN_TIER_FREE_BYTES == 5 * GIB


def test_tier_names_are_unique():
    names = [t.name for t in reader_tiers.TIERS]
    assert len(names) == len(set(names))


def test_the_bigger_model_has_the_bigger_floor():
    assert tier("7b").min_free_bytes > tier("3b").min_free_bytes
    assert tier("7b").weights_bytes > tier("3b").weights_bytes


def test_tiers_are_immutable():
    """The table is a pinned manifest; nothing at runtime may edit a hash."""
    with pytest.raises(Exception):
        reader_tiers.TIERS[0].weights_sha256 = "0" * 64


def test_tier_by_name_round_trips():
    for t in reader_tiers.TIERS:
        assert reader_tiers.tier_by_name(t.name) is t
    assert reader_tiers.tier_by_name("qwen3-vl-500b") is None
