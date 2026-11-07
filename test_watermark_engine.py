"""
Test Suite for Ghost Universal Sanitizer (watermark_engine.py).
Tests:
1. PDF Watermark & Annotation Removal
2. Scanned Paper Yellow Tracking Dot Neutralization (MIC)
3. Invisible Steganography & Frequency Dithering
4. Content-Aware Texture Inpainting
5. Word / DOCX Watermark Removal
6. PowerPoint / PPTX Watermark Removal
7. Excel / XLSX Background Watermark Removal
8. Vector Drawing Watermark Removal
9. Scanned Page Watermark Bleaching
"""

import os
import io
import unittest
import zipfile
import numpy as np
from PIL import Image
import pymupdf

from watermark_engine import (
    PaperSanitizer,
    SteganographySanitizer,
    InpaintingSanitizer,
    ExemplarPatchInpainter,
    AutoWatermarkDetector,
    BatchImageAlphaInverter,
    PreflightStructuralDetector,
    ChrominanceDeltaSanitizer,
    MultiPageAlphaInverter,
    PDFWatermarkSanitizer,
    OfficeSanitizer,
    UniversalSanitizer
)


class TestGhostSanitizer(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = os.path.join(os.path.dirname(__file__), "tmp_test_sanitizer")
        os.makedirs(self.tmp_dir, exist_ok=True)

    def tearDown(self):
        for f in os.listdir(self.tmp_dir):
            try:
                os.remove(os.path.join(self.tmp_dir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.tmp_dir)
        except Exception:
            pass

    def test_pdf_watermark_and_metadata_stripping(self):
        """Creates a PDF with metadata, text watermark ('CONFIDENTIAL') and verifies removal."""
        pdf_path = os.path.join(self.tmp_dir, "test_watermarked.pdf")
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        
        page.insert_text((72, 100), "Quarterly Financial Report", fontsize=18)
        page.insert_text((72, 140), "Total Revenue: $1,250,000", fontsize=12)

        pt = pymupdf.Point(150, 400)
        page.insert_text(pt, "CONFIDENTIAL - DO NOT COPY", fontsize=36, morph=(pt, pymupdf.Matrix(45)), color=(0.8, 0.8, 0.8))

        doc.set_metadata({
            "title": "Confidential Internal Audit",
            "author": "Analyst 007",
            "producer": "Adobe Acrobat Pro Tracking v9"
        })
        doc.save(pdf_path)
        doc.close()

        clean_pdf_path = os.path.join(self.tmp_dir, "test_clean.pdf")
        report = UniversalSanitizer.process_file(pdf_path, clean_pdf_path)

        self.assertEqual(report["type"], "pdf")
        self.assertGreaterEqual(report["audit"]["text_watermarks_removed"], 1)
        self.assertTrue(report["audit"]["metadata_cleared"])

        clean_doc = pymupdf.open(clean_pdf_path)
        clean_text = clean_doc[0].get_text()
        meta = clean_doc.metadata

        self.assertIn("Quarterly Financial Report", clean_text)
        self.assertIn("Total Revenue", clean_text)
        self.assertNotIn("CONFIDENTIAL", clean_text)
        self.assertEqual(meta.get("author", ""), "")
        self.assertEqual(meta.get("producer", ""), "Ghost PDF Sanitizer")
        clean_doc.close()

    def test_cpdfstamp_header_preservation_vs_watermark_removal(self):
        """Verifies that marked-content headers ('Paper / Subject Code') are preserved while tracking hashes are excised."""
        sanitizer = PDFWatermarkSanitizer()
        
        header_block = "/CPDFSTAMP BMC 1 0 0 1 170 821 cm BT /F26 12 Tf(Paper / Subject Code: 58505 / Applied Physics - I.)Tj ET EMC"
        tracking_block = "/CPDFSTAMP BMC 1 0 0 1 204 10 cm BT /F27 10 Tf(A6F0A9D803DFCE12B37858D9EE4A6EDE)Tj ET EMC"
        repeating_block = "/CPDFSTAMP BMC BT (37858D9EE4A6EDEA6F0A9D803DFCE12B37858D9EE4A6EDEA6F0)Tj ET EMC"

        self.assertFalse(sanitizer.is_watermark_marked_content(header_block))
        self.assertTrue(sanitizer.is_watermark_marked_content(tracking_block))
        self.assertTrue(sanitizer.is_watermark_marked_content(repeating_block))

    def test_printer_tracking_dot_neutralization(self):
        """Simulates microscopic yellow tracking dots (MIC) and verifies detection & erasure."""
        w, h = 200, 200
        img_arr = np.full((h, w, 3), 250, dtype=np.uint8)
        img_arr[50:54, 20:180] = 20

        dot_coords = [
            (25, 25), (45, 30), (70, 28), (95, 32), (120, 27),
            (150, 80), (175, 85), (20, 150), (60, 160), (110, 170)
        ]
        for y, x in dot_coords:
            img_arr[y:y+2, x:x+2] = [255, 255, 40]

        img = Image.fromarray(img_arr, mode="RGB")
        cleaned_img, dots_removed = PaperSanitizer.remove_printer_tracking_dots(img)

        self.assertGreater(dots_removed, 0)
        cleaned_arr = np.array(cleaned_img)

        for y, x in dot_coords:
            self.assertGreaterEqual(cleaned_arr[y, x, 2], 200)

        self.assertLessEqual(cleaned_arr[52, 100, 0], 30)

    def test_frequency_steganography_neutralization(self):
        """Verifies 2D FFT phase dithering alters frequency domain without visible distortion."""
        w, h = 128, 128
        test_arr = np.random.randint(50, 200, (h, w, 3), dtype=np.uint8)
        img = Image.fromarray(test_arr, mode="RGB")

        sanitized_img = SteganographySanitizer.neutralize_frequency_watermarks(img, strength=0.04)
        san_arr = np.array(sanitized_img)

        self.assertFalse(np.array_equal(test_arr, san_arr))
        max_diff = np.max(np.abs(test_arr.astype(int) - san_arr.astype(int)))
        self.assertLessEqual(max_diff, 15)

    def test_lsb_payload_clearing(self):
        """Verifies LSB payloads are randomized/cleared."""
        img_arr = np.full((64, 64, 3), 101, dtype=np.uint8)
        img = Image.fromarray(img_arr, mode="RGB")

        sanitized = SteganographySanitizer.clear_lsb_payload(img, bits=2)
        san_arr = np.array(sanitized)

        self.assertTrue(np.all((san_arr >> 2) == (img_arr >> 2)))

    def test_content_aware_inpainting(self):
        """Verifies inpainting fills masked box with surrounding gradient."""
        img_arr = np.full((100, 100, 3), 200, dtype=np.uint8)
        img_arr[40:60, 40:60] = [10, 10, 10]

        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[40:60, 40:60] = 255

        img = Image.fromarray(img_arr, mode="RGB")
        inpainted = InpaintingSanitizer.inpaint(img, mask, iterations=20)
        inpainted_arr = np.array(inpainted)

        center_color = inpainted_arr[50, 50, 0]
        self.assertGreater(center_color, 180)

    def test_office_docx_sanitization(self):
        """Verifies WordArt / PowerPlusWatermark removal from DOCX."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', '<Types></Types>')
            sample_header = (
                '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:v="urn:schemas-microsoft-com:vml">\n'
                '  <w:p><w:r>\n'
                '    <w:pict>\n'
                '      <v:shape id="PowerPlusWatermarkObject357476642" type="#_x0000_t136">\n'
                '        <v:textpath string="CONFIDENTIAL"/>\n'
                '      </v:shape>\n'
                '    </w:pict>\n'
                '    <w:t>Confidential Report Header</w:t>\n'
                '  </w:r></w:p>\n'
                '</w:hdr>'
            )
            zf.writestr('word/header1.xml', sample_header)

        docx_clean, audit = UniversalSanitizer.sanitize_bytes(buf.getvalue(), "report.docx")
        self.assertGreaterEqual(audit.get("docx_watermarks_removed", 0), 1)

        with zipfile.ZipFile(io.BytesIO(docx_clean), 'r') as zf_clean:
            cleaned_hdr = zf_clean.read('word/header1.xml').decode('utf-8')
            self.assertNotIn("PowerPlusWatermarkObject", cleaned_hdr)
            self.assertIn("Confidential Report Header", cleaned_hdr)

    def test_office_pptx_sanitization(self):
        """Verifies watermark shape removal from PowerPoint presentation."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', '<Types></Types>')
            sample_slide = (
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">\n'
                '  <p:cSld><p:spTree>\n'
                '    <p:sp>\n'
                '      <p:nvSpPr><p:cNvPr id="2" name="Watermark Shape 1"/></p:nvSpPr>\n'
                '      <p:txBody><p:p><p:r><p:t>CONFIDENTIAL DRAFT</p:t></p:r></p:p></p:txBody>\n'
                '    </p:sp>\n'
                '    <p:sp>\n'
                '      <p:nvSpPr><p:cNvPr id="3" name="Title Box"/></p:nvSpPr>\n'
                '      <p:txBody><p:p><p:r><p:t>Executive Summary</p:t></p:r></p:p></p:txBody>\n'
                '    </p:sp>\n'
                '  </p:spTree></p:cSld>\n'
                '</p:sld>'
            )
            zf.writestr('ppt/slides/slide1.xml', sample_slide)

        pptx_clean, audit = UniversalSanitizer.sanitize_bytes(buf.getvalue(), "presentation.pptx")
        self.assertGreaterEqual(audit.get("pptx_watermarks_removed", 0), 1)

        with zipfile.ZipFile(io.BytesIO(pptx_clean), 'r') as zf_clean:
            cleaned_sld = zf_clean.read('ppt/slides/slide1.xml').decode('utf-8')
            self.assertNotIn("Watermark Shape 1", cleaned_sld)
            self.assertIn("Executive Summary", cleaned_sld)

    def test_office_xlsx_sanitization(self):
        """Verifies worksheet background picture watermark removal from Excel."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', '<Types></Types>')
            sample_sheet = (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
                '  <sheetData><row r="1"><c r="A1"><v>99999</v></c></row></sheetData>\n'
                '  <picture r:id="rId1"/>\n'
                '</worksheet>'
            )
            zf.writestr('xl/worksheets/sheet1.xml', sample_sheet)

        xlsx_clean, audit = UniversalSanitizer.sanitize_bytes(buf.getvalue(), "sheet.xlsx")
        self.assertGreaterEqual(audit.get("xlsx_watermarks_removed", 0), 1)

        with zipfile.ZipFile(io.BytesIO(xlsx_clean), 'r') as zf_clean:
            cleaned_sht = zf_clean.read('xl/worksheets/sheet1.xml').decode('utf-8')
            self.assertNotIn("<picture", cleaned_sht)
            self.assertIn("99999", cleaned_sht)

    def test_vector_drawing_watermark_removal(self):
        """Verifies removal of vector drawing watermark paths with transparency."""
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=800)
        page.insert_text((50, 100), "Project Blueprint 2026", fontsize=16)

        shape = page.new_shape()
        shape.draw_rect(pymupdf.Rect(40, 180, 560, 620))
        shape.finish(color=(0.85, 0.85, 0.85), fill=(0.9, 0.9, 0.9), fill_opacity=0.2, stroke_opacity=0.3)
        shape.commit()

        buf = io.BytesIO()
        doc.save(buf)
        doc.close()

        clean_pdf, audit = UniversalSanitizer.sanitize_bytes(buf.getvalue(), "blueprint.pdf")
        self.assertGreaterEqual(audit.get("vector_paths_removed", 0), 1)

        doc_clean = pymupdf.open(stream=clean_pdf, filetype="pdf")
        text = doc_clean[0].get_text()
        self.assertIn("Project Blueprint 2026", text)
        doc_clean.close()

    def test_scanned_page_watermark_filtering(self):
        """Verifies that scanned pages with baked-in watermarks get bleached without corrupting black text."""
        w, h = 400, 500
        arr = np.full((h, w, 3), 245, dtype=np.uint8)
        arr[80:84, 50:350] = [15, 15, 15]
        arr[200:300, 80:320] = [235, 185, 190]
        arr[250:254, 50:350] = [18, 18, 18]

        img = Image.fromarray(arr, mode="RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        doc = pymupdf.open()
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=buf.getvalue())
        pdf_bytes = doc.tobytes()
        doc.close()

        clean_pdf, audit = UniversalSanitizer.sanitize_bytes(pdf_bytes, "scan.pdf")
        self.assertGreaterEqual(audit.get("images_sanitized", 0), 1)

        doc_clean = pymupdf.open(stream=clean_pdf, filetype="pdf")
        img_clean_data = doc_clean.extract_image(doc_clean[0].get_images()[0][0])
        clean_arr = np.array(Image.open(io.BytesIO(img_clean_data["image"])))

        self.assertTrue(np.all(clean_arr[220:240, 150:250] == 255))
        self.assertTrue(np.all(clean_arr[81:83, 100:200, 0] <= 30))
        doc_clean.close()

    def test_preflight_structural_zero_keyword_diagonal_removal(self):
        """Verifies Enfocus PitStop-inspired structural preflight removes diagonal watermarks with NO keywords."""
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "Important Quarterly Report", fontsize=16)

        # Arbitrary string with NO watermark keywords
        pt = pymupdf.Point(150, 400)
        page.insert_text(pt, "ArbitraryStudentCodeX999", fontsize=32, morph=(pt, pymupdf.Matrix(45)), color=(0.85, 0.85, 0.85))
        pdf_bytes = doc.tobytes()
        doc.close()

        # Sanitize with strictly empty keyword list
        clean_pdf, audit = UniversalSanitizer.sanitize_bytes(pdf_bytes, "doc.pdf", keywords=[])
        self.assertGreaterEqual(audit.get("structural_watermarks_removed", 0) + audit.get("text_watermarks_removed", 0), 1)

        doc_clean = pymupdf.open(stream=clean_pdf, filetype="pdf")
        text = doc_clean[0].get_text()
        self.assertIn("Important Quarterly Report", text)
        self.assertNotIn("ArbitraryStudentCodeX999", text)
        doc_clean.close()

    def test_chrominance_delta_color_stamp_neutralization(self):
        """Verifies that high-saturation color stamps (red/blue) are neutralized while black ink is untouched."""
        w, h = 120, 120
        arr = np.full((h, w, 3), 255, dtype=np.uint8)
        # Black text line
        arr[30:35, 20:100] = [15, 15, 15]
        # Red 'COPY' stamp
        arr[50:80, 40:90] = [225, 45, 50]

        img = Image.fromarray(arr, mode="RGB")
        cleaned_img, count = ChrominanceDeltaSanitizer.neutralize_color_stamps(img)

        self.assertGreater(count, 0)
        clean_arr = np.array(cleaned_img)
        # Stamp area should now be bleached to paper white
        self.assertTrue(np.all(clean_arr[55:75, 45:85] == 255))
        # Text line should still be dark ink
        self.assertTrue(np.all(clean_arr[31:34, 25:95, 0] <= 30))

    def test_google_research_multipage_alpha_inversion(self):
        """Verifies Google Research multi-page exact alpha inversion on synthetic multi-page document."""
        w, h = 100, 100
        paper = 245
        wm_color = 120
        wm_alpha = 0.30

        # Page 1: Heading at top
        p1 = np.full((h, w, 3), paper, dtype=np.float32)
        p1[15:25, 20:80] = 20.0

        # Page 2: Text at bottom
        p2 = np.full((h, w, 3), paper, dtype=np.float32)
        p2[70:80, 20:80] = 20.0

        # Page 3: Blank paper
        p3 = np.full((h, w, 3), paper, dtype=np.float32)

        # Apply static watermark across all 3 pages
        pages = [p1, p2, p3]
        for p in pages:
            # Watermark band across the middle
            p[40:55, 15:85] = (1.0 - wm_alpha) * p[40:55, 15:85] + wm_alpha * wm_color

        pil_pages = [Image.fromarray(p.astype(np.uint8), mode="RGB") for p in pages]
        cleaned_pages, inv_pixels = MultiPageAlphaInverter.invert_scanned_pages(pil_pages)

        self.assertGreater(inv_pixels, 0)
        clean_p3_arr = np.array(cleaned_pages[2], dtype=np.float32)
        clean_p1_arr = np.array(cleaned_pages[0], dtype=np.float32)

        # On Page 3, the watermark band should be restored to paper background (~245)
        self.assertAlmostEqual(float(np.mean(clean_p3_arr[42:52, 20:80])), paper, delta=4.0)
        # On Page 1, original text at top should remain sharp (~20)
        self.assertAlmostEqual(float(np.mean(clean_p1_arr[16:24, 25:75])), 20.0, delta=2.0)

    def test_text_over_watermark_preservation(self):
        """Verifies that legitimate text overlapping a watermark is 100% preserved."""
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)

        # 1. Background watermark text
        pt_wm = pymupdf.Point(150, 400)
        page.insert_text(pt_wm, "CONFIDENTIAL DRAFT COPY", fontsize=36, morph=(pt_wm, pymupdf.Matrix(45)), color=(0.85, 0.85, 0.85))

        # 2. Legitimate paragraph text rendered OVER the watermark
        page.insert_text((72, 390), "Total Balance Due: $14,500.00", fontsize=14, color=(0, 0, 0))
        page.insert_text((72, 420), "Student Evaluation Summary: Pass with Distinction", fontsize=12, color=(0, 0, 0))

        pdf_bytes = doc.tobytes()
        doc.close()

        clean_bytes, audit = UniversalSanitizer.sanitize_bytes(pdf_bytes, "invoice.pdf")
        self.assertGreaterEqual(audit.get("text_watermarks_removed", 0) + audit.get("structural_watermarks_removed", 0), 1)

        doc_clean = pymupdf.open(stream=clean_bytes, filetype="pdf")
        text = doc_clean[0].get_text()

        # Legitimate text directly over the watermark MUST be preserved
        self.assertIn("Total Balance Due: $14,500.00", text)
        self.assertIn("Student Evaluation Summary: Pass with Distinction", text)
        # Watermark MUST be removed
        self.assertNotIn("CONFIDENTIAL DRAFT COPY", text)
        doc_clean.close()

    def test_auto_watermark_detector_corner(self):
        """Verifies zero-click detection of localized corner watermark stamps."""
        w, h = 300, 300
        arr = np.full((h, w, 3), 190, dtype=np.uint8)
        # Corner badge at bottom right
        arr[260:290, 210:290] = [30, 30, 30]

        img = Image.fromarray(arr, mode="RGB")
        corner_mask = AutoWatermarkDetector.detect_corner_watermarks(img)
        self.assertGreater(np.sum(corner_mask > 0), 100)
        # Verify it detected pixels specifically in the bottom right corner
        self.assertTrue(np.any(corner_mask[260:290, 210:290] > 0))
        # Verify top left corner is clean
        self.assertEqual(np.sum(corner_mask[:100, :100]), 0)

    def test_auto_watermark_detector_stock_grid(self):
        """Verifies detection of diagonal stock photo watermark lattices."""
        w, h = 200, 200
        arr = np.full((h, w), 180, dtype=np.uint8)
        yy, xx = np.mgrid[0:h, 0:w]
        grid = ((yy - xx) % 40 < 3)
        arr[grid] = 120

        img = Image.fromarray(arr, mode="L")
        grid_mask = AutoWatermarkDetector.detect_stock_grid_watermarks(img)
        self.assertGreater(np.sum(grid_mask > 0), 200)

    def test_exemplar_patch_inpainting_textured(self):
        """Verifies that exemplar patch inpainting restores micro-texture variance."""
        w, h = 80, 80
        np.random.seed(42)
        base = np.full((h, w, 3), 140, dtype=np.float32)
        noise = np.random.normal(0, 14, (h, w, 3)).astype(np.float32)
        textured = np.clip(base + noise, 0, 255).astype(np.uint8)

        mask = np.zeros((h, w), dtype=np.uint8)
        mask[30:50, 30:50] = 255

        img = Image.fromarray(textured, mode="RGB")
        clean_img = ExemplarPatchInpainter.inpaint(img, mask)
        clean_arr = np.array(clean_img, dtype=np.float32)

        # Repaired patch should maintain authentic texture variance
        repaired_std = float(np.std(clean_arr[32:48, 32:48]))
        self.assertGreater(repaired_std, 6.0)

    def test_batch_image_alpha_inversion(self):
        """Verifies Google Research exact alpha inversion across an image batch."""
        w, h = 100, 100
        p1 = np.full((h, w, 3), [190, 120, 120], dtype=np.uint8)
        p2 = np.full((h, w, 3), [120, 190, 120], dtype=np.uint8)
        p3 = np.full((h, w, 3), [120, 120, 190], dtype=np.uint8)

        # Add static watermark across all 3
        alpha = 0.35
        wm_color = np.array([255, 255, 255], dtype=np.float32)
        imgs = []
        for p in [p1, p2, p3]:
            pf = p.astype(np.float32)
            pf[40:60, 20:80] = (1.0 - alpha) * pf[40:60, 20:80] + alpha * wm_color
            imgs.append(Image.fromarray(pf.astype(np.uint8), mode="RGB"))

        cleaned_bytes, audit = UniversalSanitizer.sanitize_image_batch(imgs)
        self.assertEqual(audit["status"], "success")
        self.assertEqual(len(cleaned_bytes), 3)

    def test_image_zero_click_auto_removal(self):
        """Verifies end-to-end zero-click watermark removal on a single standalone image."""
        w, h = 250, 250
        arr = np.full((h, w, 3), 210, dtype=np.uint8)
        # Add corner badge at bottom right
        arr[200:235, 170:235] = [20, 20, 20]

        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")

        clean_bytes, audit = UniversalSanitizer.sanitize_bytes(buf.getvalue(), "photo.png")
        self.assertTrue(audit.get("inpainting_applied", False))
        self.assertTrue(audit.get("auto_detected_watermark", False))

        clean_img = Image.open(io.BytesIO(clean_bytes))
        clean_arr = np.array(clean_img)
        # Corner badge should be cleanly replaced by background (~210)
        self.assertGreater(float(np.mean(clean_arr[205:230, 175:230])), 170.0)


if __name__ == "__main__":
    unittest.main()
