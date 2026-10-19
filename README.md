# Ghost PDF (Pixel-Perfect In-Place PDF Editor)

> 🔒 **Ghost Mode:** 100% In-Memory Local Processing | Zero Watermarks | Pixel-Perfect Typography & Stream Editing

**Ghost PDF** is a high-fidelity, local-first PDF modification engine designed to perform seamless text replacements while preserving original document layout, typography archetypes, background colors, and font metrics.

---

## 🌟 Key Features

- **4-Archetype Typography Matcher:** Intelligently maps font descriptors to authentic system and fallback archetypes:
  - Programming Sans Monospace (Consolas, Cascadia, DejaVu Sans Mono)
  - Monospace Typewriter (Courier, Cousine, Inconsolata)
  - Authentic Serif (Times, Georgia, Garamond, Palatino, Minion)
  - Crisp Sans-Serif (Helvetica, Arial, Trebuchet, Segoe)
- **Background Color Sampling:** Automatically inspects bounding box neighborhoods to seamlessly redact and patch backgrounds without leaving halo artifacts.
- **Direct Stream & Canvas Editing:** Cleanly rewires text operators and coordinates without destructive rasterization or document bloat.
- **Zero Watermarks & 100% Privacy:** Runs entirely on your local machine with zero external API calls or third-party tracking.
- **Dual Interface:**
  - **Interactive Web Canvas:** Visual point-and-click browser editor (`web/server.py`).
  - **Streamlit Studio:** Fast parameter tuning and side-by-side inspection (`app.py`).

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

### 3. Launch the Streamlit Studio

For testing and visual comparison of stream redaction & font alias matching:

```bash
streamlit run app.py
```

---

## 📂 Project Architecture

```
ghost-pdf/
├── app.py                  # Streamlit application for side-by-side editing & inspection
├── universal_engine.py      # Core typography & in-place stream replacement engine
├── smart_typography.py     # Font metric resolver & background color sampling
├── font_resolver.py        # System font locator and fallback matching
├── lowlevel_editor.py      # Low-level PDF stream operator rewiring
├── web/
│   ├── index.html          # Interactive HTML5/Canvas UI
│   └── server.py           # Local HTTP server backend
├── sample_invoice.pdf      # Sample document for verification & testing
├── requirements.txt        # Python package dependencies
└── README.md
```

---

## 📜 License

MIT License. Local and offline-first.
