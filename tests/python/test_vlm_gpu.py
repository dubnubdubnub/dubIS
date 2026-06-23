"""GPU integration test for the local VLM (Ollama) image-recognition backend.

Marked ``gpu`` and deselected by default (see pyproject ``addopts``). Runs only
on a node with a GPU + Ollama serving a qwen2.5vl model — i.e. the self-hosted
``gpu`` runner (y740 / RTX 2060). No mocks: end-to-end proof that the node's
GPU/Ollama/model stack works and that ``available()`` selects a usable installed
model.
"""
import pytest

import vlm_extract


@pytest.mark.gpu
def test_vlm_backend_live_on_gpu_node():
    # Real Ollama on this GPU node (no mocks). available() probes Ollama and
    # selects the best installed qwen2.5vl model — :3b is what's pulled on the
    # 6 GB RTX 2060.
    assert vlm_extract.available(), (
        f"VLM backend unavailable on this GPU node — Ollama at "
        f"{vlm_extract._base_url()} must be up with a qwen2.5vl model pulled."
    )
    # After a successful probe, the selected model is an installed qwen2.5vl tag.
    assert vlm_extract.model_name().startswith("qwen2.5vl"), vlm_extract.model_name()
