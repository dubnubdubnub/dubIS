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

Enable it on a node with a capable GPU:

```
winget install Ollama.Ollama      # or https://ollama.com/download
ollama pull qwen2.5vl:7b          # ~6 GB; needs ~6 GB VRAM at 4-bit
```

That's it — `extract_pages` auto-detects a reachable Ollama with the model and
prefers it. Configuration (per node, via environment):

- `DUBIS_VLM_MODEL` — model tag (default `qwen2.5vl:7b`). Use a smaller tag on
  low-VRAM GPUs.
- `DUBIS_OLLAMA_URL` — Ollama base URL (default `http://127.0.0.1:11434`); point
  at another node running Ollama if this one has no GPU.
- `DUBIS_VLM_DISABLE` — set to any value to force the backend off.

Nodes without Ollama/GPU are unaffected — they use the Tesseract pipeline.
