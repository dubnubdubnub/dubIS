# Installation

## Python dependencies

```
pip install -r requirements.txt
```

## OCR (optional, for direct-from-mfg image imports)

Image OCR uses Tesseract. Install the system binary:

- **Windows**: https://github.com/UB-Mannheim/tesseract/wiki (add `tesseract.exe` to PATH)
- **macOS**: `brew install tesseract`
- **Linux**: `apt-get install tesseract-ocr`

## AI OCR backend (optional, GPU — best for photographed packing lists)

For phone photos of packing lists (faint print, folds, perspective) a local
vision-language model reads the table far better than Tesseract. It runs entirely
locally — no document data leaves the machine — and is used automatically *when
available*, falling back to Tesseract otherwise.

dubIS talks the **OpenAI-compatible** API (`GET /v1/models`, `POST
/v1/chat/completions` with an image), so any local server that speaks it will do:
vLLM, LM Studio, or [llama.cpp](https://github.com/ggml-org/llama.cpp) — which
is what the CI GPU node runs and the easiest way to get started: one binary that
downloads the GGUF *and* its vision projector for you.

```
# Best quality; ~6 GB VRAM at 4-bit.
llama-server -hf ggml-org/Qwen2.5-VL-7B-Instruct-GGUF:Q4_K_M --port 8080

# …or, on a smaller GPU (e.g. a 6 GB RTX 2060): fits ~4 GB and still gets MPNs +
# quantities, but may miss faint LCSC C-numbers (drag those in by hand).
llama-server -hf ggml-org/Qwen2.5-VL-3B-Instruct-GGUF:Q4_K_M --port 8080
```

⚠️ Vision needs the mmproj projector. `-hf` fetches it automatically; if you pass
`--model` by hand you must also pass `--mmproj <mmproj-*.gguf>`, or the server
loads text-only and every image is silently ignored.

That's it — `extract_pages` auto-detects a reachable server and **picks the best
model it serves** (prefers 7B, falls back to 3B), so a low-VRAM node just serves
the 3B with no further config. Per-node environment overrides:

- `DUBIS_VLM_MODEL` — force a specific model id, overriding the auto-pick.
- `DUBIS_VLM_URL` — server base URL (default `http://127.0.0.1:8080`,
  llama-server's port); point at another node's GPU, another port, or a
  different `/v1`-speaking server — nothing else changes.
- `DUBIS_VLM_DISABLE` — set to any value to force the backend off.

Nodes without a VLM server/GPU are unaffected — they use the Tesseract pipeline.
