"""Pinned model table for the local picture/PDF reader, and tier selection.

One tier per memory band. Every field is pinned and auditable in the same style as the
init container in ``win-runners/gpu/llamacpp.yaml``: exact repo, exact revision, exact
quant filename, sha256 for both files. Nothing here is resolved at install time from a
floating ``main`` — a model repo that force-pushes must fail the checksum, not silently
hand the operator different weights.

Both sha256 values in each tier were verified against the live HuggingFace API two
independent ways before being written down: ``/api/models/<repo>?blobs=true`` (the LFS
``sha256`` in each sibling entry) and ``HEAD /<repo>/resolve/<revision>/<file>`` (the
``x-linked-etag`` header, which is the LFS oid). A wrong hash here only surfaces at
install time on the operator's machine after a multi-GiB download, so guessing one is
strictly worse than shipping no tier at all.

Selection reads **free** memory, never total — see the policy rules in
``reader_memory``. A budget whose headroom could not be determined declines every tier:
unknown is never a pass, the same rule ``domain/predicates`` applies to substitutions.
"""

from __future__ import annotations

from dataclasses import dataclass

_GIB = 1024 ** 3


@dataclass(frozen=True)
class Tier:
    """One installable reader configuration.

    ``min_free_bytes`` is a floor on *free* memory, and it is well above
    ``total_download_bytes``: weights + projector are only part of the resident cost.
    The KV cache at ``ctx_size``, the vision encoder's activations for a full page of
    image tokens, and llama.cpp's own CUDA/Metal compute buffers all have to fit too.
    """

    name: str
    min_free_bytes: int
    repo: str
    # Pinned to a commit sha, not a branch: a re-quantised upload under the same
    # filename would otherwise change the bytes under a passing install.
    revision: str
    weights_file: str
    weights_sha256: str
    weights_bytes: int
    # NOT optional. Without --mmproj, llama-server loads the text tower only, answers
    # every request as if the image were absent, and reports no error at all — the
    # extraction just quietly hallucinates from the prompt. docs/install.md already
    # warns about this failure mode; it is the single most expensive way to get this
    # wrong, because everything looks like it works.
    mmproj_file: str
    mmproj_sha256: str
    mmproj_bytes: int
    ctx_size: int

    @property
    def total_download_bytes(self) -> int:
        return self.weights_bytes + self.mmproj_bytes

    @property
    def min_free_gib(self) -> float:
        return self.min_free_bytes / _GIB


# Descending by floor: choose_tier returns the first tier the budget clears, which is
# therefore the largest model that fits.
#
# ctx_size is 8192 for both tiers. Qwen2.5-VL's KV cache at f16 is 2 (K+V) x layers x
# kv_heads x head_dim x 2 bytes per token: the 3B (36 layers, 2 KV heads, head_dim 128)
# costs 36 KiB/token -> 288 MiB at 8192, and the 7B (28 layers, 4 KV heads, head_dim
# 128) costs 56 KiB/token -> 448 MiB. Both are comfortable at their tier's *floor*,
# which is the case that has to work — not on a 24 GiB card. A packing-list page plus
# the extraction prompt fits well inside 8192 tokens once the raster is capped for the
# VLM path (the design doc flags pdf_raster._MAX_EDGE = 2600 as tesseract-era and
# unmeasured for a VLM: a 2600px page is thousands of vision tokens).
TIERS: tuple[Tier, ...] = (
    Tier(
        name="qwen2.5-vl-7b-q4_k_m",
        min_free_bytes=10 * _GIB,
        repo="ggml-org/Qwen2.5-VL-7B-Instruct-GGUF",
        revision="508edd0afaa66bb9e9f40587acc2184f02daf1f6",
        weights_file="Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
        weights_sha256="9258bf05b12686d097ff3b6b18d968ab393649780aa2b3cd67fec43d50554392",
        weights_bytes=4683072032,
        mmproj_file="mmproj-Qwen2.5-VL-7B-Instruct-f16.gguf",
        mmproj_sha256="c24a7f5fcfc68286f0a217023b6738e73bea4f11787a43e8238d4bb1b8604cde",
        mmproj_bytes=1354162912,
        ctx_size=8192,
    ),
    Tier(
        name="qwen2.5-vl-3b-q4_k_m",
        min_free_bytes=5 * _GIB,
        repo="ggml-org/Qwen2.5-VL-3B-Instruct-GGUF",
        revision="5037fcf163dd95d1e41d1974465f0898ed108ca2",
        weights_file="Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf",
        weights_sha256="d02fe9b69ad8cadbbd228e387667af66612c44bed29ffc8eb1e7caf9ac486c12",
        weights_bytes=1929901056,
        mmproj_file="mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf",
        mmproj_sha256="b9160fe9d814d1fadf68395677468534778b39ac33c2e7561b7b218626e60d5e",
        mmproj_bytes=1338428128,
        ctx_size=8192,
    ),
)

# Below this there is no tier: the reader stays `off` and image/PDF import keeps using
# the existing tesseract/flat path rather than installing a model that cannot load.
MIN_TIER_FREE_BYTES = min(t.min_free_bytes for t in TIERS)


def choose_tier(budget) -> Tier | None:
    """The largest tier the budget's **free** memory clears, or ``None``.

    ``budget`` is a ``reader_memory.BudgetInfo``, a plain byte count, or ``None``.
    Passing a ``BudgetInfo`` is the intended use: reading ``.free_bytes`` off it is what
    keeps a 24 GiB card with only 4 GiB actually free from being handed the 7B.

    Returns ``None`` — meaning "stay off, keep tesseract" — when there is no budget,
    when headroom is unknown, or when free memory is below the smallest tier's floor.
    """
    free = _free_bytes(budget)
    if free is None:
        return None
    for tier in sorted(TIERS, key=lambda t: t.min_free_bytes, reverse=True):
        if free >= tier.min_free_bytes:
            return tier
    return None


def _free_bytes(budget) -> int | None:
    if budget is None:
        return None
    if isinstance(budget, bool):
        return None
    if isinstance(budget, int):
        free = budget
    else:
        free = getattr(budget, "free_bytes", None)
        # Deliberately no fallback to total_bytes: a probe that reported capacity
        # without headroom must decline, not be silently upgraded to optimism.
        if free is None:
            return None
        if not isinstance(free, int) or isinstance(free, bool):
            return None
    return free if free >= 0 else None


def tier_by_name(name: str) -> Tier | None:
    """Look a tier up by its stable ``name``, e.g. to resume an interrupted install."""
    for tier in TIERS:
        if tier.name == name:
            return tier
    return None
