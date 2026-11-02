"""
Test Suite for Ghost Sanitizer (watermark_engine.py).
Tests all 4 engines:
1. PDF Watermark & Annotation Removal
2. Scanned Paper Yellow Tracking Dot Neutralization (MIC)
3. Invisible Steganography & Frequency Dithering
4. Content-Aware Texture Inpainting
"""

import os
import unittest
import numpy as np
from PIL import Image
import pymupdf

from watermark_engine import (
    PaperSanitizer,
    SteganographySanitizer,
    InpaintingSanitizer,
    PDFWatermarkSanitizer,
    UniversalSanitizer
)


class TestGhostSanitizer(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = os.path.join(os.path.dirname(__file__), "tmp_test_sanitizer")
        os.makedirs(self.tmp_dir, exist_ok=True)

    def tearDown(self):
        # Cleanup temp directory
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
        
        # Insert normal content
        page.insert_text((72, 100), "Quarterly Financial Report", fontsize=18)
        page.insert_text((72, 140), "Total Revenue: $1,250,000", fontsize=12)

        # Insert a diagonal watermark using morph matrix
        pt = pymupdf.Point(150, 400)
        page.insert_text(pt, "CONFIDENTIAL - DO NOT COPY", fontsize=36, morph=(pt, pymupdf.Matrix(45)), color=(0.8, 0.8, 0.8))

        # Add tracking metadata
        doc.set_metadata({
            "title": "Confidential Internal Audit",
            "author": "Analyst 007",
            "producer": "Adobe Acrobat Pro Tracking v9"
        })
        doc.save(pdf_path)
        doc.close()

        # Sanitize
        clean_pdf_path = os.path.join(self.tmp_dir, "test_clean.pdf")
        report = UniversalSanitizer.process_file(pdf_path, clean_pdf_path)

        self.assertEqual(report["type"], "pdf")
        self.assertGreaterEqual(report["audit"]["text_watermarks_removed"], 1)
        self.assertTrue(report["audit"]["metadata_cleared"])

        # Inspect cleaned document
        clean_doc = pymupdf.open(clean_pdf_path)
        clean_text = clean_doc[0].get_text()
        meta = clean_doc.metadata

        # Normal text remains
        self.assertIn("Quarterly Financial Report", clean_text)
        self.assertIn("Total Revenue", clean_text)

        # Watermark is gone
        self.assertNotIn("CONFIDENTIAL", clean_text)

        # Tracking metadata is scrubbed
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
        # White paper background
        img_arr = np.full((h, w, 3), 250, dtype=np.uint8)

        # Add black text lines
        img_arr[50:54, 20:180] = 20  # black text bar

        # Add 10 simulated laser printer yellow tracking dots (R=255, G=255, B=40)
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

        # Check that the blue channel at dot coordinates was raised to neutral background
        for y, x in dot_coords:
            self.assertGreaterEqual(cleaned_arr[y, x, 2], 200)

        # Verify black text bar is untouched
        self.assertLessEqual(cleaned_arr[52, 100, 0], 30)

    def test_frequency_steganography_neutralization(self):
        """Verifies 2D FFT phase dithering alters frequency domain without visible distortion."""
        w, h = 128, 128
        test_arr = np.random.randint(50, 200, (h, w, 3), dtype=np.uint8)
        img = Image.fromarray(test_arr, mode="RGB")

        sanitized_img = SteganographySanitizer.neutralize_frequency_watermarks(img, strength=0.04)
        san_arr = np.array(sanitized_img)

        # Must not be identical (frequencies dithered)
        self.assertFalse(np.array_equal(test_arr, san_arr))

        # Peak visual difference must remain very small (imperceptible)
        max_diff = np.max(np.abs(test_arr.astype(int) - san_arr.astype(int)))
        self.assertLessEqual(max_diff, 15)

    def test_lsb_payload_clearing(self):
        """Verifies LSB payloads are randomized/cleared."""
        img_arr = np.full((64, 64, 3), 101, dtype=np.uint8) # 101 = 0b01100101
        img = Image.fromarray(img_arr, mode="RGB")

        sanitized = SteganographySanitizer.clear_lsb_payload(img, bits=2)
        san_arr = np.array(sanitized)

        # Upper 6 bits must be unchanged (100 or 101)
        self.assertTrue(np.all((san_arr >> 2) == (img_arr >> 2)))

    def test_content_aware_inpainting(self):
        """Verifies inpainting fills masked box with surrounding gradient."""
        img_arr = np.full((100, 100, 3), 200, dtype=np.uint8)
        # Create a dark watermark stamp in the center
        img_arr[40:60, 40:60] = [10, 10, 10]

        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[40:60, 40:60] = 255

        img = Image.fromarray(img_arr, mode="RGB")
        inpainted = InpaintingSanitizer.inpaint(img, mask, iterations=20)
        inpainted_arr = np.array(inpainted)

        # The center area should now be restored to surrounding gray (~200), not dark (10)
        center_color = inpainted_arr[50, 50, 0]
        self.assertGreater(center_color, 180)


if __name__ == "__main__":
    unittest.main()
