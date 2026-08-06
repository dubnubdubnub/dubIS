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
[llama.cpp](https://github.com/ggml-org/llama.cpp) (what the CI GPU node runs),
Ollama, vLLM, LM Studio. Two ways to get one:

```
# A. llama.cpp — one binary, downloads the GGUF + its vision projector for you.
llama-server -hf ggml-org/Qwen2.5-VL-7B-Instruct-GGUF:Q4_K_M --port 8080
#   …or, on a smaller GPU (e.g. a 6 GB RTX 2060):
llama-server -hf ggml-org/Qwen2.5-VL-3B-Instruct-GGUF:Q4_K_M --port 8080
#   ⚠️ vision needs the mmproj projector. `-hf` fetches it automatically; if you
#   pass `--model` by hand you must also pass `--mmproj <mmproj-*.gguf>`, or the
#   server loads text-only and every image is silently ignored.

# B. Ollama — set DUBIS_VLM_URL=http://127.0.0.1:11434 (it serves /v1 too).
winget install Ollama.Ollama      # or https://ollama.com/download
ollama pull qwen2.5vl:7b          # best quality; ~6 GB VRAM at 4-bit
ollama pull qwen2.5vl:3b          # fits ~4 GB; gets MPNs + quantities, but may
                                  # miss faint LCSC C-numbers (drag those in)
```

That's it — `extract_pages` auto-detects a reachable server and **picks the best
model it serves** (prefers 7B, falls back to 3B), so a low-VRAM node just serves
the 3B with no further config. Per-node environment overrides:

- `DUBIS_VLM_MODEL` — force a specific model id, overriding the auto-pick.
- `DUBIS_VLM_URL` — server base URL (default `http://127.0.0.1:8080`,
  llama-server's port); point at another node's GPU if this one has none. The
  older `DUBIS_OLLAMA_URL` is still read as a fallback, so nodes configured
  before the 2026-08-05 llama.cpp swap keep working unchanged.
- `DUBIS_VLM_DISABLE` — set to any value to force the backend off.

Nodes without a VLM server/GPU are unaffected — they use the Tesseract pipeline.
