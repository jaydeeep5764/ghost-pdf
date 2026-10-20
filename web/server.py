import http.server
import socketserver
import json
import base64
import io
import os
import sys
import urllib.parse
import pymupdf

# Import the Universal PDF Engine
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from universal_engine import apply_universal_edit

PORT = 3000
WEB_DIR = os.path.dirname(os.path.abspath(__file__))

ACTIVE_PDF_BYTES = None
HISTORY = []
HISTORY_INDEX = -1
MAX_HISTORY = 40

try:
    sample_path = os.path.join(os.path.dirname(WEB_DIR), "sample_invoice.pdf")
    if os.path.exists(sample_path):
        with open(sample_path, "rb") as f:
            ACTIVE_PDF_BYTES = f.read()
            HISTORY = [ACTIVE_PDF_BYTES]
            HISTORY_INDEX = 0
except Exception as e:
    print(f"Sample load warning: {e}")

def get_page_payload(pdf_bytes, page_idx=0):
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    if page_idx < 0 or page_idx >= total_pages:
        page_idx = 0
    page = doc[page_idx]

    pix = page.get_pixmap(dpi=150)
    img_base64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
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
                    "flags": flg
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

    doc.close()

    can_undo = (HISTORY_INDEX > 0)
    can_redo = (HISTORY_INDEX < len(HISTORY) - 1)

    return {
        "page_index": page_idx,
        "total_pages": total_pages,
        "width": pw,
        "height": ph,
        "image": f"data:image/png;base64,{img_base64}",
        "words": words,
        "lines": lines,
        "blocks": blocks,
        "images": images,
        "spans": words,
        "can_undo": can_undo,
        "can_redo": can_redo
    }

class PDFEditorHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        
        if url.path == "/" or url.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return

        if url.path == "/api/sample":
            global ACTIVE_PDF_BYTES
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

        return super().do_GET()

    def do_POST(self):
        global ACTIVE_PDF_BYTES, HISTORY, HISTORY_INDEX
        url = urllib.parse.urlparse(self.path)

        if url.path == "/api/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            
            try:
                req = json.loads(body.decode("utf-8"))
                b64_data = req.get("pdf_base64", "")
                if "," in b64_data:
                    b64_data = b64_data.split(",")[1]
                pdf_data = base64.b64decode(b64_data)
                
                ACTIVE_PDF_BYTES = pdf_data
                HISTORY = [ACTIVE_PDF_BYTES]
                HISTORY_INDEX = 0

                data = get_page_payload(ACTIVE_PDF_BYTES, 0)
                self.send_json({"status": "success", "data": data})
            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=400)
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
                "filename": "edited_clean.pdf"
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
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), PDFEditorHandler) as httpd:
        print(f"[*] GhostPDF Universal Studio Running at: http://localhost:{PORT}")
        httpd.serve_forever()
