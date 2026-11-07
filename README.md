# Ghost PDF (Pixel-Perfect In-Place PDF Editor)

> 🔒 **Ghost Mode:** 100% In-Memory Local Processing | Zero Watermarks | Pixel-Perfect Typography & Stream Editing

**Ghost PDF** is a high-fidelity, local-first PDF modification engine designed to perform seamless text replacements while preserving original document layout, typography archetypes, background colors, and font metrics.

---

## 🌟 Key Features

- **Digital Vector & Scanned Photo PDF Editing:**
  - **Native Digital PDFs:** Edits content streams in-place with zero quality loss and no artificial rasterization.
  - **Scanned Documents & Exam Papers:** Neural AI OCR detects words, lines, and bounding boxes on scanned/photo PDFs for full point-and-click editing.
  - **Asynchronous Pre-OCR & Zero-Copy Buffers:** Fast page loading with background pre-processing across multi-page documents.
- **Advanced Typography & Weight Matching:**
  - Sans-Serif (Arial, Helvetica, Trebuchet, Segoe UI)
  - Authentic Serif (Times New Roman, Georgia, Garamond, Palatino, Minion)
  - Clean Sans (Calibri)
  - Monospace (Consolas, DejaVu Sans Mono, Courier)
  - Accurate stroke thickness analysis for bold weights and calibrated font sizing.
- **✍️ Handwriting & Script Engine:**
  - **Cursive Script:** Flowing connected loops (*Segoe Script*, *Lucida Handwriting*)
  - **Casual Ballpoint:** Natural handwritten strokes (*Ink Free*)
  - **Neat Hand Print:** Separated hand-drawn letters (*Segoe Print*)
  - **Ruled-Paper Inpainting:** OpenCV Navier-Stokes inpainting erases handwritten ink without cutting notebook horizontal ruling lines or grid margins.
  - **Dynamic Ink Tone Sampling:** Samples real pen ink colors (ballpoint blue, fountain black, red marking, pencil graphite).
- **Background Color & Line-Aware Inpainting:** Automatically inspects bounding box neighborhoods to seamlessly redact and patch backgrounds without leaving white halo artifacts.
- **Zero Watermarks & 100% Privacy:** Runs entirely on your local machine with zero external API calls or third-party tracking.
- **Interactive Web Canvas:** Visual point-and-click browser editor (`web/server.py` on `http://localhost:3000`).

---

## 🚀 Quick Start

### 1. Installation

Ensure you have Python 3.9+ installed, then install the dependencies:

```bash
pip install -r requirements.txt
```

### 2. Launch the Web Interface

For an interactive browser UI where you can click any text span and edit it live:

```bash
python web/server.py
```
Then open [http://localhost:3000](http://localhost:3000) in your browser.

---

### 3. Universal Watermark & Steganography Remover (CLI)

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
├── universal_engine.py      # Core typography, handwriting font resolution & stream replacement
├── watermark_engine.py      # Universal Watermark, MIC Tracking Dot & Steganography Sanitizer
├── smart_typography.py     # Font metric resolver & background color sampling
├── font_resolver.py        # System font locator and fallback matching
├── lowlevel_editor.py      # Low-level PDF stream operator rewiring
├── app.py                  # Streamlit application (Dual Mode: Editor + Universal Sanitizer)
├── web/
│   ├── index.html          # Interactive HTML5/Canvas UI with handwriting styles & live controls
│   └── server.py           # High-performance HTTP server backend with neural OCR caching
├── sample_invoice.pdf      # Sample document for verification & testing
├── requirements.txt        # Python package dependencies
└── README.md
```

---

## 📜 License

MIT License. Local and offline-first.
