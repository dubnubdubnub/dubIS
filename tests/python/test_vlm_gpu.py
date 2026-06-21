"""GPU integration test for the local VLM (Ollama) image-recognition backend.

Marked ``gpu`` and deselected by default (see pyproject ``addopts``). Runs only
on a node with a GPU + Ollama serving the auto-selected qwen2.5vl model — i.e.
the self-hosted ``gpu`` runner (y740 / RTX 2060). No mocks: this is the
end-to-end proof that the node's GPU/Ollama/model stack and the VRAM-based model
auto-selection actually work together.
"""
import pytest

import vlm_extract


@pytest.mark.gpu
def test_vlm_backend_live_on_gpu_node():
    # Auto-selection (no DUBIS_VLM_MODEL override) must land on a qwen2.5vl tag
    # sized to this node's GPU — e.g. :3b on the 6 GB RTX 2060.
    model = vlm_extract.model_name()
    assert model.startswith("qwen2.5vl"), f"auto-selected model unexpected: {model!r}"

    # Real Ollama: reachable AND the auto-selected model is pulled on this node.
    assert vlm_extract.available(), (
        f"VLM backend unavailable on this GPU node — Ollama at "
        f"{vlm_extract._base_url()} must be up and serving {model!r}."
    )
