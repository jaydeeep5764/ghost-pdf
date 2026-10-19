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
try:
    sample_path = os.path.join(os.path.dirname(WEB_DIR), "sample_invoice.pdf")
    if os.path.exists(sample_path):
        with open(sample_path, "rb") as f:
            ACTIVE_PDF_BYTES = f.read()
except Exception as e:
    print(f"Sample load warning: {e}")

def get_page_payload(pdf_bytes, page_idx=0):
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if page_idx < 0 or page_idx >= len(doc):
        page_idx = 0
    page = doc[page_idx]

    pix = page.get_pixmap(dpi=150)
    img_base64 = base64.b64encode(pix.tobytes("png")).decode("ascii")

    text_data = page.get_text("dict")
    spans = []
    span_id = 0

    for block in text_data.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                for span in line["spans"]:
                    txt = span["text"].strip()
                    if txt:
                        c_int = span["color"]
                        r = ((c_int >> 16) & 0xFF)
                        g = ((c_int >> 8) & 0xFF)
                        b = (c_int & 0xFF)
                        hex_color = f"#{r:02x}{g:02x}{b:02x}"
                        
                        spans.append({
                            "id": span_id,
                            "text": span["text"],
                            "bbox": list(span["bbox"]),
                            "font": span["font"],
                            "size": round(span["size"], 2),
                            "color": hex_color,
                            "flags": span["flags"]
                        })
                        span_id += 1

    payload = {
        "page_index": page_idx,
        "total_pages": len(doc),
        "width": page.rect.width,
        "height": page.rect.height,
        "image": f"data:image/png;base64,{img_base64}",
        "spans": spans
    }
    doc.close()
    return payload

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
        global ACTIVE_PDF_BYTES
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

                # APPLY THE MASTER UNIVERSAL PIPELINE
                apply_universal_edit(doc, edit)

                out_buf = io.BytesIO()
                doc.save(out_buf, garbage=4, deflate=True)
                ACTIVE_PDF_BYTES = out_buf.getvalue()
                doc.close()

                # Re-render immediately so the page reflects live on screen
                new_data = get_page_payload(ACTIVE_PDF_BYTES, p_idx)
                self.send_json({"status": "success", "data": new_data})

            except Exception as e:
                self.send_json({"status": "error", "message": str(e)}, status=500)
            return

        if url.path == "/api/export":
            if ACTIVE_PDF_BYTES is None:
                self.send_json({"status": "error", "message": "No document active"}, status=400)
                return

            b64_res = base64.b64encode(ACTIVE_PDF_BYTES).decode("ascii")
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
