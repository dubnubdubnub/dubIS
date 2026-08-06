"""GPU integration test for the local VLM image-recognition backend.

Marked ``gpu`` and deselected by default (see pyproject ``addopts``). Runs only
on a node with a GPU and a reachable VLM server holding a Qwen2.5-VL model —
i.e. the self-hosted ``gpu`` runner (y740 / RTX 2060), which reaches the
in-cluster **llama.cpp** server
(``llamacpp.win-runners.svc.cluster.local:8080``). No mocks: end-to-end proof
that the node's GPU/server/model stack works and that ``available()`` selects a
usable model.
"""
import pytest

import vlm_extract


@pytest.mark.gpu
def test_vlm_backend_live_on_gpu_node():
    # Real model server on this GPU node (no mocks). available() probes
    # /v1/models and selects the best served Qwen2.5-VL model — the 3B is what
    # fits the 6 GB RTX 2060.
    assert vlm_extract.available(), (
        f"VLM backend unavailable on this GPU node — the model server at "
        f"{vlm_extract._base_url()} must be up (GET /v1/models) with a "
        f"Qwen2.5-VL model loaded."
    )
    # After a successful probe, the selected model is a served Qwen2.5-VL id.
    # Matched by marker, not prefix: llama.cpp reports its --alias
    # ("qwen2.5-vl-3b") while other /v1 servers report a tag ("qwen2.5vl:3b").
    selected = vlm_extract.model_name()
    assert vlm_extract._is_qwen_vl(selected), selected
