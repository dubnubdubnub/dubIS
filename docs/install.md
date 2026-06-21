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
locally via [Ollama](https://ollama.com) — no document data leaves the machine —
and is used automatically *when available*, falling back to Tesseract otherwise.

Enable it by pulling a model that fits your GPU:

```
winget install Ollama.Ollama      # or https://ollama.com/download
ollama pull qwen2.5vl:7b          # best quality; ~6 GB VRAM at 4-bit
#   …or, on a smaller GPU (e.g. a 6 GB RTX 2060):
ollama pull qwen2.5vl:3b          # fits ~4 GB; gets MPNs + quantities, but may
                                  # miss faint LCSC C-numbers (drag those in)
```

That's it — `extract_pages` auto-detects a reachable Ollama and **picks the best
model you've pulled** (prefers 7B, falls back to 3B), so a low-VRAM node just
pulls the 3B with no further config. Per-node environment overrides:

- `DUBIS_VLM_MODEL` — force a specific model tag, overriding the auto-pick.
- `DUBIS_OLLAMA_URL` — Ollama base URL (default `http://127.0.0.1:11434`); point
  at another node running Ollama if this one has no GPU.
- `DUBIS_VLM_DISABLE` — set to any value to force the backend off.

Nodes without Ollama/GPU are unaffected — they use the Tesseract pipeline.
