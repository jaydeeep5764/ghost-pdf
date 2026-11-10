---
title: Ghost PDF Studio
emoji: 👻
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Ghost PDF (Pixel-Perfect In-Place PDF Editor & Watermark Remover)

> 🔒 **Ghost Mode:** 100% In-Memory Processing | 2-in-1 Studio (Watermark Remover + PDF Text Editor) | Pixel-Perfect Typography & Stream Editing

**Ghost PDF** is a high-fidelity, dual-mode PDF modification engine combining a **Universal Watermark / Sanitizer** and a **Pixel-Perfect PDF Text & Handwriting Editor** in one web workspace.

---

## 🌟 Key Features

- **2-in-1 Dual Mode Studio (`web/index.html` + `web/server.py`):**
  - **Mode 1 — Watermark Remover & Sanitizer:** Strips transparent Form XObject stamps, SMask overlays, diagonal watermarks, vector Bézier paths, laser printer tracking dots (MIC), and steganography across PDF, DOCX, PPTX, XLSX, and images.
  - **Mode 2 — PDF Text Editor:** Click any word, line, or text block to edit in-place, resize, recolor, move, search/replace, or insert new text.
- **Digital Vector & Scanned Photo PDF Editing:**
  - **Native Digital PDFs:** Edits content streams in-place with zero quality loss and no artificial rasterization.
  - **Scanned Documents & Exam Papers:** Neural AI OCR detects words, lines, and bounding boxes on scanned/photo PDFs for full point-and-click editing.
  - **Per-Tab Isolated Sessions:** Safe multi-user session isolation (`X-Session-Id`) with independent undo/redo history and page OCR caches.
- **Advanced Typography & Weight Matching:**
  - Cross-platform font mapping for Sans-Serif (Arial / Liberation Sans / Helvetica), Serif (Times New Roman / Liberation Serif), Calibri (Carlito), and Monospace (DejaVu Sans Mono / Courier).
  - Accurate stroke thickness analysis for bold weights and calibrated font sizing.
- **✍️ Handwriting & Script Engine:**
  - **Cursive Script:** Flowing connected loops (*Segoe Script*, *Dancing Script*, *Lucida Handwriting*)
  - **Casual Ballpoint:** Natural handwritten strokes (*Ink Free*)
  - **Neat Hand Print:** Separated hand-drawn letters (*Segoe Print*)

---

## 🚀 Quick Start

### 1. Local Installation

Ensure you have Python 3.9+ installed, then install the dependencies:

```bash
pip install -r requirements.txt
```

### 2. Launch the Web Interface Locally

```bash
python web/server.py
```
Then open [http://localhost:3000](http://localhost:3000) in your browser.

---

### 3. Deploy 24/7 on Hugging Face Spaces (Docker)

1. Create a new Space on [huggingface.co/new-space](https://huggingface.co/new-space) and select **Docker** as the SDK.
2. Push this repository (with `Dockerfile`, `requirements.txt`, and the YAML header in `README.md`) to your Space's Git remote (or mirror from GitHub).
3. Hugging Face Spaces builds the container automatically on port `7860` with both **Watermark Remover** and **PDF Editor** modes live 24/7.

---

### 4. Universal Watermark & Steganography Remover (CLI)

Sanitize any PDF or scanned image (removes text watermarks, publisher banners, laser printer tracking dots, and invisible steganography):

```bash
# Sanitize a PDF (purges watermarks, annotations, metadata, and embedded image tracking)
python watermark_engine.py document.pdf -o document_clean.pdf

# Sanitize a scanned paper or image (neutralizes yellow tracking dots & steganography)
python watermark_engine.py scan.jpg -o scan_clean.jpg --all

# Specify additional custom watermark keywords to strip:
python watermark_engine.py paper.pdf -o paper_clean.pdf --keywords "MyWatermark" "CompanyConfidential"
```

---

## 📂 Project Architecture

```
ghost-pdf/
├── Dockerfile               # Hugging Face Spaces / Docker container image (port 7860)
├── .dockerignore            # Keeps test scratch artifacts out of container builds
├── universal_engine.py      # Core typography, Linux/Windows font resolution & stream replacement
├── watermark_engine.py      # Universal Watermark, MIC Tracking Dot & Steganography Sanitizer
├── smart_typography.py      # Font metric resolver & background color sampling
├── font_resolver.py         # System font locator and fallback matching
├── lowlevel_editor.py       # Low-level PDF stream operator rewiring
├── app.py                   # Streamlit application (Dual Mode: Editor + Universal Sanitizer)
├── web/
│   ├── index.html           # 2-in-1 UI (Watermark Remover + PDF Editor) with per-tab session IDs
│   └── server.py            # Multi-session HTTP server backend with neural OCR caching
├── sample_invoice.pdf       # Sample document for verification & testing
├── requirements.txt         # Full Python package dependencies
└── README.md
```

---

## 📜 License

MIT License. Local and offline-first.
