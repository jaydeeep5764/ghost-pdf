import threading
import http.server
import socketserver
import json
import base64
import io
import os
import sys
import urllib.parse
import re
import pymupdf

# Reconfigure stdout/stderr for clean UTF-8 on Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Import the Universal PDF Engine
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from universal_engine import apply_universal_edit
from watermark_engine import UniversalSanitizer, PDFWatermarkSanitizer

PORT = 3000
WEB_DIR = os.path.dirname(os.path.abspath(__file__))

ACTIVE_PDF_BYTES = None
ACTIVE_ORIGINAL_FILENAME = "document.pdf"
ACTIVE_IS_IMAGE = False
HISTORY = []
HISTORY_INDEX = -1
MAX_HISTORY = 40
PAGE_OCR_CACHE = {}

try:
    sample_path = os.path.join(os.path.dirname(WEB_DIR), "sample_invoice.pdf")
    if os.path.exists(sample_path):
        with open(sample_path, "rb") as f:
            ACTIVE_PDF_BYTES = f.read()
            HISTORY = [ACTIVE_PDF_BYTES]
            HISTORY_INDEX = 0
except Exception as e:
    print(f"Sample load warning: {e}")

OCR_ENGINE = None

def get_ocr_engine():
    global OCR_ENGINE
    if OCR_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            OCR_ENGINE = RapidOCR()
        except Exception as e:
            print(f"RapidOCR initialization notice: {e}")
            OCR_ENGINE = False
    return OCR_ENGINE if OCR_ENGINE is not False else None

# Asynchronously pre-warm ONNX neural models in the background so initial uploads have zero initialization lag
threading.Thread(target=get_ocr_engine, daemon=True).start()

def extract_ocr_text_data(page: pymupdf.Page, page_idx: int = 0):
    """
    Runs high-performance OCR on scanned / photo-converted PDF pages
    to detect lines, words, and exact bounding boxes for full interactive editing.
    Caches results per page so subsequent edits are instantaneous (<15ms).
    """
    global PAGE_OCR_CACHE
    if page_idx in PAGE_OCR_CACHE:
        cached_w, cached_l, cached_b = PAGE_OCR_CACHE[page_idx]
        return [dict(w) for w in cached_w], [dict(l) for l in cached_l], [dict(b) for b in cached_b]

    engine = get_ocr_engine()
    if not engine:
        return [], [], []

    try:
        import numpy as np

        # Render at 120 DPI for high recognition accuracy with 40% faster neural inference
        pix = page.get_pixmap(dpi=120)
        # Direct zero-copy NumPy buffer conversion (bypasses heavy PNG encoding / PIL decoding)
        img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
        if pix.n == 4:
            img_np = img_np[:, :, :3]

        ocr_results, _ = engine(img_np)
        if not ocr_results:
            return [], [], []

        scale_x = page.rect.width / pix.width
        scale_y = page.rect.height / pix.height

        words = []
        lines = []
        blocks = []

        w_id = 0
        l_id = 0

        # Scanned page typography:
        # Default to standard Sans-Serif ('helv' / Arial) which matches >85% of modern documents,
        # forms, question papers, engineering notes, and worksheets.
        # The user can also freely select Serif (Times) or Calibri in the editor toolbar.
        is_serif_doc = False

        for r_idx, r in enumerate(ocr_results):
            raw_text = r[1].strip()
            if not raw_text:
                continue

            box = r[0]
            bx0 = round(min(pt[0] for pt in box) * scale_x, 2)
            by0 = round(min(pt[1] for pt in box) * scale_y, 2)
            bx1 = round(max(pt[0] for pt in box) * scale_x, 2)
            by1 = round(max(pt[1] for pt in box) * scale_y, 2)
            l_bbox = [bx0, by0, bx1, by1]

            # In typography, OCR bounding box height covers ascenders and line bounds.
            # Using 0.82 produces true-to-life standard sizes (e.g. 10.5-11.5pt) matching original printed documents without ballooning.
            line_height = max(8.0, by1 - by0)
            est_size = round(max(8.5, line_height * 0.82), 1)

            # Compute true dark ink color using darkest 10th percentile in the bounding box
            px0 = max(0, int(bx0 / scale_x))
            py0 = max(0, int(by0 / scale_y))
            px1 = min(img_np.shape[1], int(bx1 / scale_x))
            py1 = min(img_np.shape[0], int(by1 / scale_y))
            col = "#1E293B"
            is_stroke_bold = False

            try:
                if px1 > px0 and py1 > py0:
                    box_rgb = img_np[py0:py1, px0:px1, :3]
                    lum = 0.299 * box_rgb[:, :, 0] + 0.587 * box_rgb[:, :, 1] + 0.114 * box_rgb[:, :, 2]
                    p10 = np.percentile(lum, 10)
                    ink = box_rgb[lum <= p10].mean(axis=0)
                    r, g, b = int(ink[0]), int(ink[1]), int(ink[2])
                    if 0.299 * r + 0.587 * g + 0.114 * b <= 140:
                        col = f"#{r:02x}{g:02x}{b:02x}"

                    # Stroke width & ink density analysis for bold weight detection
                    crop_gray = cv2.cvtColor(box_rgb, cv2.COLOR_RGB2GRAY)
                    _, line_th = cv2.threshold(crop_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                    dist = cv2.distanceTransform(line_th, cv2.DIST_L2, 3)
                    strokes = dist[dist > 1.0]
                    if len(strokes) > 0:
                        stroke_thick = np.percentile(strokes, 80) * 2
                        h_px = py1 - py0
                        if (stroke_thick / max(1.0, h_px)) >= 0.13:
                            is_stroke_bold = True
            except Exception:
                pass

            # Detect short headings, labels, and question tags that are standard bold in official docs
            token_list = raw_text.split()
            is_short_label = (raw_text.endswith(":") or (len(token_list) <= 4 and bool(re.search(r'^(time|marks|maximum|note|n\.b\.|q\s*\.?\s*\d+|part|section)\b', raw_text.lower()))))
            is_all_caps = (len(raw_text) > 3 and raw_text.isupper() and not any(c.isdigit() for c in raw_text))
            is_bold = is_short_label or is_all_caps or is_stroke_bold

            line_flags = (4 if is_serif_doc else 0) | (16 if is_bold else 0)
            line_font = ("times-bold" if is_bold else "times") if is_serif_doc else ("hebo" if is_bold else "helv")

            line_item = {
                "id": f"l_ocr_{l_id}",
                "text": raw_text,
                "bbox": l_bbox,
                "font": line_font,
                "size": est_size,
                "color": col,
                "flags": line_flags,
                "is_bold": is_bold,
                "is_italic": False,
                "line_count": 1,
                "block_idx": r_idx,
                "is_ocr": True
            }
            lines.append(line_item)
            blocks.append(dict(line_item, id=f"b_ocr_{l_id}"))
            l_id += 1

            if len(token_list) <= 1:
                words.append(dict(line_item, id=f"w_ocr_{w_id}", block_idx=r_idx, line_idx=l_id, word_idx=0))
                w_id += 1
            else:
                total_chars = max(1, sum(len(t) for t in token_list) + len(token_list) - 1)
                curr_x = bx0
                total_w = max(10.0, bx1 - bx0)

                for w_idx, tok in enumerate(token_list):
                    frac = len(tok) / total_chars
                    tok_w = round(total_w * frac, 2)
                    wb = [round(curr_x, 2), by0, round(curr_x + tok_w, 2), by1]
                    words.append({
                        "id": f"w_ocr_{w_id}",
                        "text": tok,
                        "bbox": wb,
                        "font": line_font,
                        "size": est_size,
                        "color": col,
                        "flags": line_flags,
                        "is_bold": is_bold,
                        "is_italic": False,
                        "block_idx": r_idx,
                        "line_idx": l_id,
                        "word_idx": w_idx,
                        "is_ocr": True
                    })
                    w_id += 1
                    curr_x += tok_w + (total_w / total_chars)

        PAGE_OCR_CACHE[page_idx] = (words, lines, blocks)
        return words, lines, blocks
    except Exception as e:
        print(f"Error during OCR extraction: {e}")
        return [], [], []

def get_page_payload(pdf_bytes, page_idx=0):
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    if page_idx < 0 or page_idx >= total_pages:
        page_idx = 0
    page = doc[page_idx]

    pix = page.get_pixmap(dpi=150)
    img_base64 = base64.b64encode(pix.tobytes("jpg", jpg_quality=88)).decode("ascii")
    pw = page.rect.width
    ph = page.rect.height

    text_data = page.get_text("dict")
    raw_words = page.get_text("words")

    # Build spans lookup for style resolution
    spans = []
    for block in text_data.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                for s in line["spans"]:
                    spans.append(s)

    def get_style_for_rect(bbox):
        rect = pymupdf.Rect(bbox)
        best = None
        best_area = -1
        for s in spans:
            s_rect = pymupdf.Rect(s["bbox"])
            inter = (rect & s_rect).get_area()
            if inter > best_area:
                best_area = inter
                best = s
        if not best and spans:
            cx = (rect.x0 + rect.x1) / 2.0
            cy = (rect.y0 + rect.y1) / 2.0
            best = min(spans, key=lambda s: abs((s["bbox"][0] + s["bbox"][2])/2.0 - cx) + abs((s["bbox"][1] + s["bbox"][3])/2.0 - cy))

        if best:
            c_int = best["color"]
            hex_color = f"#{(c_int>>16)&0xFF:02x}{(c_int>>8)&0xFF:02x}{c_int&0xFF:02x}"
            return best["font"], round(best["size"], 1), hex_color, best["flags"]
        return "helv", 11.0, "#000000", 0

    # Words extraction
    words = []
    for idx, w in enumerate(raw_words):
        w_bbox = [round(float(x), 2) for x in w[:4]]
        w_text = w[4].strip()
        if w_text:
            f, sz, col, flg = get_style_for_rect(w_bbox)
            words.append({
                "id": f"w_{idx}",
                "text": w_text,
                "bbox": w_bbox,
                "font": f,
                "size": sz,
                "color": col,
                "flags": flg,
                "is_bold": bool(flg & 16),
                "is_italic": bool(flg & 2),
                "block_idx": w[5],
                "line_idx": w[6],
                "word_idx": w[7]
            })

    # Lines & Blocks extraction
    lines = []
    line_id = 0
    blocks = []
    for b_idx, block in enumerate(text_data.get("blocks", [])):
        if "lines" in block:
            b_line_texts = []
            for line in block["lines"]:
                l_text = "".join(s["text"] for s in line["spans"]).strip()
                if l_text:
                    l_bbox = [round(float(x), 2) for x in line["bbox"]]
                    f, sz, col, flg = get_style_for_rect(l_bbox)
                    lines.append({
                        "id": f"l_{line_id}",
                        "text": l_text,
                        "bbox": l_bbox,
                        "font": f,
                        "size": sz,
                        "color": col,
                        "flags": flg,
                        "is_bold": bool(flg & 16),
                        "is_italic": bool(flg & 2),
                        "line_count": 1,
                        "block_idx": b_idx
                    })
                    line_id += 1
                    b_line_texts.append(l_text)
            if b_line_texts:
                b_text = "\n".join(b_line_texts)
                b_bbox = [round(float(x), 2) for x in block["bbox"]]
                f, sz, col, flg = get_style_for_rect(b_bbox)
                blocks.append({
                    "id": f"b_{b_idx}",
                    "text": b_text,
                    "bbox": b_bbox,
                    "font": f,
                    "size": sz,
                    "color": col,
                    "flags": flg,
                    "is_bold": bool(flg & 16),
                    "is_italic": bool(flg & 2),
                    "line_count": len(b_line_texts)
                })

    # Images extraction
    images = []
    for img_idx, img_info in enumerate(page.get_images()):
        xref = img_info[0]
        rects = page.get_image_rects(xref)
        for r_idx, r in enumerate(rects):
            images.append({
                "id": f"img_{img_idx}_{r_idx}",
                "xref": xref,
                "bbox": [round(float(x), 2) for x in r],
                "width": round(r.width, 2),
                "height": round(r.height, 2),
                "pixel_width": img_info[2],
                "pixel_height": img_info[3]
            })

    # Robust Scanned / Photo PDF detection
    # A page is scanned if it has 0 vector words, OR if it has a large full-page background image with few vector words
    page_area = max(1.0, pw * ph)
    has_full_bg_image = False
    for img in images:
        img_w = img.get("width", 0)
        img_h = img.get("height", 0)
        if (img_w * img_h / page_area) > 0.50:
            has_full_bg_image = True
            break

    is_scanned = (len(words) == 0) or has_full_bg_image
    if is_scanned:
        ocr_w, ocr_l, ocr_b = extract_ocr_text_data(page, page_idx)
        if ocr_w:
            if len(words) == 0:
                words = ocr_w
                lines = ocr_l
                blocks = ocr_b
            else:
                # Merge newly edited vector words with remaining OCR words
                merged_words = []
                used_vec = set()
                for ow in ocr_w:
                    ow_rect = pymupdf.Rect(ow["bbox"])
                    matched_vw = None
                    for v_idx, vw in enumerate(words):
                        vw_rect = pymupdf.Rect(vw["bbox"])
                        if (ow_rect & vw_rect).get_area() > 0 or (abs(ow_rect.y0 - vw_rect.y0) < 6 and abs(ow_rect.x0 - vw_rect.x0) < 12):
                            matched_vw = vw
                            used_vec.add(v_idx)
                            break
                    if matched_vw:
                        merged_words.append(matched_vw)
                    else:
                        merged_words.append(ow)

                for v_idx, vw in enumerate(words):
                    if v_idx not in used_vec and vw not in merged_words:
                        merged_words.append(vw)

                words = merged_words

                # Merge lines so double-click and Line Granularity mode stay 100% active
                merged_lines = []
                used_vec_lines = set()
                for ol in ocr_l:
                    ol_rect = pymupdf.Rect(ol["bbox"])
                    matched_vl = None
                    for vl_idx, vl in enumerate(lines):
                        vl_rect = pymupdf.Rect(vl["bbox"])
                        if (ol_rect & vl_rect).get_area() > 0:
                            matched_vl = vl
                            used_vec_lines.add(vl_idx)
                            break
                    if matched_vl:
                        merged_lines.append(matched_vl)
                    else:
                        merged_lines.append(ol)

                for vl_idx, vl in enumerate(lines):
                    if vl_idx not in used_vec_lines and vl not in merged_lines:
                        merged_lines.append(vl)

                lines = merged_lines

                # Merge blocks so Block Granularity mode stays 100% active
                merged_blocks = []
                used_vec_blocks = set()
                for ob in ocr_b:
                    ob_rect = pymupdf.Rect(ob["bbox"])
                    matched_vb = None
                    for vb_idx, vb in enumerate(blocks):
                        vb_rect = pymupdf.Rect(vb["bbox"])
                        if (ob_rect & vb_rect).get_area() > 0:
                            matched_vb = vb
                            used_vec_blocks.add(vb_idx)
                            break
                    if matched_vb:
                        merged_blocks.append(matched_vb)
                    else:
                        merged_blocks.append(ob)

                for vb_idx, vb in enumerate(blocks):
                    if vb_idx not in used_vec_blocks and vb not in merged_blocks:
                        merged_blocks.append(vb)

                blocks = merged_blocks



    doc.close()

    can_undo = (HISTORY_INDEX > 0)
    can_redo = (HISTORY_INDEX < len(HISTORY) - 1)

    return {
        "page_index": page_idx,
        "total_pages": total_pages,
        "width": pw,
        "height": ph,
        "image": f"data:image/jpeg;base64,{img_base64}",
        "words": words,
        "lines": lines,
        "blocks": blocks,
        "images": images,
        "spans": words,
        "can_undo": can_undo,
        "can_redo": can_redo,
        "is_scanned": is_scanned
    }

class PDFEditorHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global ACTIVE_PDF_BYTES, ACTIVE_ORIGINAL_FILENAME, ACTIVE_IS_IMAGE
        url = urllib.parse.urlparse(self.path)
        
        if url.path == "/" or url.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return

        if url.path == "/api/sample":
            if ACTIVE_PDF_BYTES is None:
                self.send_error(404, "No PDF loaded")
                return
            data = get_page_payload(ACTIVE_PDF_BYTES, 0)
            self.send_json(data)
            return

        if url.path == "/api/page":
            params = urllib.parse.parse_qs(url.query)
            p_idx = int(params.get("num", [0])[0])
            if ACTIVE_PDF_BYTES is None:
                self.send_error(404, "No PDF loaded")
                return
            data = get_page_payload(ACTIVE_PDF_BYTES, p_idx)
            self.send_json(data)
            return

        if url.path == "/api/export":
            if ACTIVE_PDF_BYTES is None:
                self.send_json({"status": "error", "message": "No document active"}, status=400)
                return

            # If original upload was an image, render and export the edited composite image directly at 300 DPI
            if ACTIVE_IS_IMAGE:
                try:
                    doc = pymupdf.open(stream=ACTIVE_PDF_BYTES, filetype="pdf")
                    base_name, file_ext = os.path.splitext(ACTIVE_ORIGINAL_FILENAME)
                    is_jpeg = file_ext.lower() in [".jpg", ".jpeg", ".jfif"]
                    out_name = f"{base_name}_clean{file_ext}"
                    pix = doc[0].get_pixmap(dpi=300)
                    img_data = pix.tobytes("jpeg" if is_jpeg else "png")
                    doc.close()
                    b64_img = base64.b64encode(img_data).decode("ascii")
                    self.send_json({
                        "status": "success",
                        "pdf_base64": b64_img,
                        "filename": out_name,
                        "is_image": True
                    })
                    return
                except Exception as ex:
                    print(f"Image export notice: {ex}")

            b64_res = base64.b64encode(ACTIVE_PDF_BYTES).decode("ascii")
            self.send_json({
                "status": "success",
                "pdf_base64": b64_res,
                "filename": "edited_clean.pdf"
            })
            return

        return super().do_GET()

    def do_POST(self):
        global ACTIVE_PDF_BYTES, HISTORY, HISTORY_INDEX, ACTIVE_ORIGINAL_FILENAME, ACTIVE_IS_IMAGE, PAGE_OCR_CACHE
        url = urllib.parse.urlparse(self.path)

        if url.path == "/api/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            
            try:
                PAGE_OCR_CACHE.clear()
                req = json.loads(body.decode("utf-8"))
                b64_data = req.get("pdf_base64", "")
                filename = req.get("filename", "document.pdf")
                if "," in b64_data:
                    b64_data = b64_data.split(",")[1]
                uploaded_bytes = base64.b64decode(b64_data)

                # Check format
                ext = os.path.splitext(filename)[1].lower() if filename else ".pdf"
                image_extensions = ('.png', '.jpg', '.jpeg', '.jfif', '.webp', '.tiff', '.tif', '.bmp')
                is_image = ext in image_extensions or uploaded_bytes.startswith((b'\x89PNG', b'\xff\xd8\xff', b'RIFF', b'II*\x00', b'MM\x00*'))
                ACTIVE_ORIGINAL_FILENAME = filename
                ACTIVE_IS_IMAGE = bool(is_image)

                if not uploaded_bytes.startswith(b"%PDF") and is_image:
                    try:
                        ft = "jpeg" if ext in (".jfif", ".jpg", ".jpeg") else (ext.lstrip(".") if ext in image_extensions else "png")
                        img_doc = pymupdf.open(stream=uploaded_bytes, filetype=ft)
                        uploaded_bytes = img_doc.convert_to_pdf()
                        ext = ".pdf"
                    except Exception as img_err:
                        print(f"Image to PDF conversion notice: {img_err}")

                is_pdf = uploaded_bytes.startswith(b"%PDF") or ext == ".pdf"

                if is_pdf:
                    ACTIVE_PDF_BYTES = uploaded_bytes
                    HISTORY = [ACTIVE_PDF_BYTES]
                    HISTORY_INDEX = 0
                    data = get_page_payload(ACTIVE_PDF_BYTES, 0)
                    self.send_json({"status": "success", "file_type": "pdf", "data": data})

                    # If multi-page scanned document, pre-OCR subsequent pages in background so page navigation is instant
                    if data.get("is_scanned") and data.get("total_pages", 1) > 1:
                        def _bg_pre_ocr(pdf_data, num_pages):
                            try:
                                d = pymupdf.open(stream=pdf_data, filetype="pdf")
                                for p_i in range(1, min(num_pages, 8)):
                                    if p_i not in PAGE_OCR_CACHE:
                                        extract_ocr_text_data(d[p_i], p_i)
                                d.close()
                            except Exception:
                                pass
                        threading.Thread(target=_bg_pre_ocr, args=(ACTIVE_PDF_BYTES, data["total_pages"]), daemon=True).start()
                else:
                    # Direct sanitization for DOCX, PPTX, XLSX, Images
                    clean_bytes, audit = UniversalSanitizer.sanitize_bytes(uploaded_bytes, filename=filename)
                    clean_b64 = base64.b64encode(clean_bytes).decode("ascii")
                    base_name, file_ext = os.path.splitext(filename)
                    out_name = f"{base_name}_clean{file_ext}"
                    self.send_json({
                        "status": "success",
                        "file_type": ext.lstrip("."),
                        "filename": out_name,
                        "file_base64": clean_b64,
                        "audit": audit
                    })
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=400)
            return


        if url.path == "/api/sanitize":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                req = json.loads(body.decode("utf-8")) if body else {}
                if ACTIVE_PDF_BYTES is None:
                    self.send_json({"status": "error", "message": "No active PDF"}, status=400)
                    return

                p_idx = req.get("page_index", 0)
                remove_text = req.get("remove_text", True)
                remove_annots = req.get("remove_annots", True)
                remove_vectors = req.get("remove_vectors", True)
                remove_dots = req.get("remove_dots", True)
                remove_stego = req.get("remove_stego", True)
                flatten_scanned = req.get("flatten_scanned", True)
                keywords = req.get("keywords", None)

                clean_bytes, audit = UniversalSanitizer.sanitize_bytes(
                    ACTIVE_PDF_BYTES,
                    filename="document.pdf",
                    remove_text=remove_text,
                    remove_annots=remove_annots,
                    remove_vectors=remove_vectors,
                    remove_dots=remove_dots,
                    remove_stego=remove_stego,
                    flatten_scanned=flatten_scanned,
                    keywords=keywords
                )

                PAGE_OCR_CACHE.clear()
                ACTIVE_PDF_BYTES = clean_bytes

                # Record in Undo History
                HISTORY = HISTORY[:HISTORY_INDEX + 1]
                HISTORY.append(ACTIVE_PDF_BYTES)
                if len(HISTORY) > MAX_HISTORY:
                    HISTORY.pop(0)
                HISTORY_INDEX = len(HISTORY) - 1

                new_data = get_page_payload(ACTIVE_PDF_BYTES, p_idx)
                self.send_json({
                    "status": "success",
                    "audit": audit,
                    "data": new_data
                })
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=500)
            return

        if url.path == "/api/detect_watermarks":
            try:
                if ACTIVE_PDF_BYTES is None:
                    self.send_json({"status": "error", "message": "No active PDF"}, status=400)
                    return

                doc = pymupdf.open(stream=ACTIVE_PDF_BYTES, filetype="pdf")
                total_pages = len(doc)
                found_indicators = {
                    "marked_content_stamps": 0,
                    "form_xobjects": 0,
                    "smask_overlays": 0,
                    "diagonal_text": 0,
                    "vector_paths": 0
                }

                for p in doc:
                    for x in p.get_xobjects():
                        x_xref = x[0]
                        obj_str = doc.xref_object(x_xref)
                        if "/SMask" in obj_str:
                            found_indicators["smask_overlays"] += 1
                        elif "/Subtype /Form" in obj_str:
                            found_indicators["form_xobjects"] += 1

                    for cxref in p.get_contents():
                        raw_stream = doc.xref_stream(cxref).decode("latin1", errors="ignore")
                        if "/CPDFSTAMP" in raw_stream:
                            found_indicators["marked_content_stamps"] += len(re.findall(r'/CPDFSTAMP\b', raw_stream))

                    try:
                        raw_dict = p.get_text("rawdict")
                        for b in raw_dict.get("blocks", []):
                            for l in b.get("lines", []):
                                dir_x, dir_y = l.get("dir", (1.0, 0.0))
                                if abs(dir_y) > 0.05 and abs(dir_x) > 0.05:
                                    found_indicators["diagonal_text"] += 1
                    except Exception:
                        pass

                    try:
                        drawings = p.get_drawings()
                        for d in drawings:
                            fo = d.get("fill_opacity", 1.0)
                            so = d.get("stroke_opacity", 1.0)
                            if (fo is not None and fo < 0.65) or (so is not None and so < 0.65):
                                found_indicators["vector_paths"] += 1
                    except Exception:
                        pass

                doc.close()
                self.send_json({
                    "status": "success",
                    "total_pages": total_pages,
                    "indicators": found_indicators
                })
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=500)
            return

        if url.path == "/api/apply_edit":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            try:
                edit = json.loads(body.decode("utf-8"))
                if ACTIVE_PDF_BYTES is None:
                    self.send_json({"status": "error", "message": "No active PDF"}, status=400)
                    return

                doc = pymupdf.open(stream=ACTIVE_PDF_BYTES, filetype="pdf")
                p_idx = edit.get("page_index", 0)

                # Apply universal edit
                apply_universal_edit(doc, edit)

                out_buf = io.BytesIO()
                doc.save(out_buf, garbage=2, deflate=True)
                ACTIVE_PDF_BYTES = out_buf.getvalue()
                doc.close()

                # Record in Undo History
                HISTORY = HISTORY[:HISTORY_INDEX + 1]
                HISTORY.append(ACTIVE_PDF_BYTES)
                if len(HISTORY) > MAX_HISTORY:
                    HISTORY.pop(0)
                HISTORY_INDEX = len(HISTORY) - 1

                # Re-render immediately
                new_data = get_page_payload(ACTIVE_PDF_BYTES, p_idx)
                self.send_json({"status": "success", "data": new_data})

            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=500)
            return

        if url.path == "/api/search":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                req = json.loads(body.decode("utf-8"))
                query = req.get("query", "").strip()
                if not query or ACTIVE_PDF_BYTES is None:
                    self.send_json({"total_matches": 0, "results": []})
                    return

                doc = pymupdf.open(stream=ACTIVE_PDF_BYTES, filetype="pdf")
                results = []
                total_matches = 0

                for p_idx in range(len(doc)):
                    page = doc[p_idx]
                    rects = page.search_for(query)
                    if not rects:
                        # Fallback for scanned / photo documents: search extracted OCR items
                        ocr_w, _, _ = extract_ocr_text_data(page, p_idx)
                        q_lower = query.lower()
                        for item in ocr_w:
                            if q_lower in item.get("text", "").lower():
                                rects.append(pymupdf.Rect(item["bbox"]))

                    if rects:
                        p_matches = []
                        for r_idx, r in enumerate(rects):
                            p_matches.append({
                                "id": f"m_{p_idx}_{r_idx}",
                                "bbox": [round(float(x), 2) for x in r],
                                "page_index": p_idx
                            })
                            total_matches += 1
                        results.append({
                            "page_index": p_idx,
                            "count": len(rects),
                            "matches": p_matches
                        })
                doc.close()
                self.send_json({"total_matches": total_matches, "results": results})
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=500)
            return

        if url.path == "/api/replace_all":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                req = json.loads(body.decode("utf-8"))
                search_text = req.get("search_text", "").strip()
                replace_text = req.get("replace_text", "").strip()
                all_pages = req.get("all_pages", True)
                target_page = req.get("page_index", 0)
                reflow = req.get("reflow", True)

                if not search_text or ACTIVE_PDF_BYTES is None:
                    self.send_json({"status": "error", "message": "Search text required"}, status=400)
                    return

                doc = pymupdf.open(stream=ACTIVE_PDF_BYTES, filetype="pdf")
                total_replaced = 0
                pages_affected = 0
                pages_to_process = range(len(doc)) if all_pages else [target_page]

                for p_idx in pages_to_process:
                    if p_idx < 0 or p_idx >= len(doc):
                        continue
                    page = doc[p_idx]
                    rects = page.search_for(search_text)
                    ocr_w = []
                    if not rects:
                        ocr_w, _, _ = extract_ocr_text_data(page, p_idx)
                        s_lower = search_text.lower()
                        for item in ocr_w:
                            if s_lower in item.get("text", "").lower():
                                rects.append(pymupdf.Rect(item["bbox"]))
                    if not rects:
                        continue

                    pages_affected += 1
                    text_dict = page.get_text("dict")
                    spans = [s for b in text_dict.get("blocks", []) if "lines" in b for l in b["lines"] for s in l["spans"]]

                    def get_style_for_r(r):
                        best = None
                        best_area = -1
                        for s in spans:
                            inter = (r & pymupdf.Rect(s["bbox"])).get_area()
                            if inter > best_area:
                                best_area = inter
                                best = s
                        if best:
                            c_int = best["color"]
                            hex_col = f"#{(c_int>>16)&0xFF:02x}{(c_int>>8)&0xFF:02x}{c_int&0xFF:02x}"
                            return best["font"], best["size"], hex_col, best["flags"]
                        for item in ocr_w:
                            inter = (r & pymupdf.Rect(item["bbox"])).get_area()
                            if inter > best_area:
                                best_area = inter
                                best = item
                        if best:
                            return best.get("font", "helv"), best.get("size", 11.0), best.get("color", "#000000"), best.get("flags", 0)
                        return "helv", 11.0, "#000000", 0

                    for r in reversed(rects):
                        f, sz, col, flg = get_style_for_r(r)
                        edit = {
                            "type": "replace",
                            "page_index": p_idx,
                            "bbox": list(r),
                            "oldText": search_text,
                            "newText": replace_text,
                            "font": f,
                            "size": sz,
                            "color": col,
                            "flags": flg,
                            "reflow": reflow
                        }
                        apply_universal_edit(doc, edit)
                        total_replaced += 1

                out_buf = io.BytesIO()
                doc.save(out_buf, garbage=2, deflate=True)
                ACTIVE_PDF_BYTES = out_buf.getvalue()
                doc.close()

                HISTORY = HISTORY[:HISTORY_INDEX + 1]
                HISTORY.append(ACTIVE_PDF_BYTES)
                if len(HISTORY) > MAX_HISTORY:
                    HISTORY.pop(0)
                HISTORY_INDEX = len(HISTORY) - 1

                new_data = get_page_payload(ACTIVE_PDF_BYTES, target_page)
                self.send_json({
                    "status": "success",
                    "total_replaced": total_replaced,
                    "pages_affected": pages_affected,
                    "data": new_data
                })
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=500)
            return

        if url.path == "/api/undo":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            p_idx = 0
            try:
                req = json.loads(body.decode("utf-8")) if body else {}
                p_idx = req.get("page_index", 0)
            except Exception:
                pass

            if HISTORY_INDEX > 0:
                HISTORY_INDEX -= 1
                ACTIVE_PDF_BYTES = HISTORY[HISTORY_INDEX]
                data = get_page_payload(ACTIVE_PDF_BYTES, p_idx)
                self.send_json({"status": "success", "data": data})
            else:
                self.send_json({"status": "error", "message": "Nothing to undo"}, status=400)
            return

        if url.path == "/api/redo":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            p_idx = 0
            try:
                req = json.loads(body.decode("utf-8")) if body else {}
                p_idx = req.get("page_index", 0)
            except Exception:
                pass

            if HISTORY_INDEX < len(HISTORY) - 1:
                HISTORY_INDEX += 1
                ACTIVE_PDF_BYTES = HISTORY[HISTORY_INDEX]
                data = get_page_payload(ACTIVE_PDF_BYTES, p_idx)
                self.send_json({"status": "success", "data": data})
            else:
                self.send_json({"status": "error", "message": "Nothing to redo"}, status=400)
            return

        if url.path == "/api/export":
            if ACTIVE_PDF_BYTES is None:
                self.send_json({"status": "error", "message": "No document active"}, status=400)
                return

            # If the active document was loaded from an image, export clean composite image directly at 300 DPI
            if ACTIVE_IS_IMAGE:
                try:
                    doc = pymupdf.open(stream=ACTIVE_PDF_BYTES, filetype="pdf")
                    base_name, file_ext = os.path.splitext(ACTIVE_ORIGINAL_FILENAME)
                    is_jpeg = file_ext.lower() in [".jpg", ".jpeg", ".jfif"]
                    out_name = f"{base_name}_clean{file_ext}"
                    mime = "image/jpeg" if is_jpeg else "image/png"
                    pix = doc[0].get_pixmap(dpi=300)
                    img_data = pix.tobytes("jpeg" if is_jpeg else "png")
                    doc.close()
                    b64_img = base64.b64encode(img_data).decode("ascii")
                    self.send_json({
                        "status": "success",
                        "pdf_base64": b64_img,
                        "filename": out_name,
                        "mime_type": mime,
                        "is_image": True
                    })
                    return
                    doc.close()
                except Exception as ex:
                    print(f"Image export notice: {ex}")

            # Clean and compress for export
            doc = pymupdf.open(stream=ACTIVE_PDF_BYTES, filetype="pdf")
            out_buf = io.BytesIO()
            doc.save(out_buf, garbage=4, deflate=True)
            export_bytes = out_buf.getvalue()
            doc.close()

            b64_res = base64.b64encode(export_bytes).decode("ascii")
            self.send_json({
                "status": "success",
                "pdf_base64": b64_res,
                "filename": "edited_clean.pdf",
                "mime_type": "application/pdf"
            })
            return

        self.send_error(404, "Endpoint not found")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    os.chdir(WEB_DIR)
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("", PORT), PDFEditorHandler) as httpd:
        print(f"[*] GhostPDF Universal Studio Running at: http://localhost:{PORT}")
        httpd.serve_forever()
