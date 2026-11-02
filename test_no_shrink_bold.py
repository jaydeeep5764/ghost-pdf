import pymupdf
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from universal_engine import apply_universal_edit

def test_suite():
    print("=== RUNNING ADVANCED VERIFICATION SUITE ===")

    # -------------------------------------------------------------
    # Test 1: Long text must NEVER shrink to tiny size (preserves >= 8.5pt on 10pt text)
    # -------------------------------------------------------------
    doc = pymupdf.open("pdf_engine/fixed_resume_test.pdf")
    edit_long = {
        "type": "replace",
        "page_index": 0,
        "oldText": "Python, C++, SQL",
        "newText": "Python, C++, SQL, Rust, Go, TypeScript, Docker, Kubernetes, Reverse Engineering, Assembly, Ghidra, IDA Pro",
        "font": "TeXGyreTermesX-Regular",
        "size": 10.0,
        "flags": 4,
        "bbox": [100.9, 138.18, 175.0, 149.25],
        "is_bold": False
    }
    apply_universal_edit(doc, edit_long)
    doc.save("pdf_engine/test_verified_long.pdf")

    p = pymupdf.open("pdf_engine/test_verified_long.pdf")[0]
    spans = [s for b in p.get_text("dict")["blocks"] if "lines" in b for l in b["lines"] for s in l["spans"] if "Rust" in s["text"]]
    assert len(spans) == 1, "Span not found"
    s = spans[0]
    print(f"[PASS] Test 1: Long text size = {s['size']}pt (Original was 10.0pt; NOT tiny!)")
    assert s['size'] >= 8.5, f"Font shrunk too much: {s['size']}pt"
    assert not (s['flags'] & 16), "Regular text became bold!"

    # -------------------------------------------------------------
    # Test 2: User explicitly wants text bold -> produces bold
    # -------------------------------------------------------------
    doc2 = pymupdf.open("pdf_engine/fixed_resume_test.pdf")
    edit_bold = {
        "type": "replace",
        "page_index": 0,
        "oldText": "Python, C++, SQL",
        "newText": "Python, C++, SQL",
        "font": "TeXGyreTermesX-Regular",
        "size": 10.0,
        "flags": 4,
        "bbox": [100.9, 138.18, 175.0, 149.25],
        "is_bold": True
    }
    apply_universal_edit(doc2, edit_bold)
    doc2.save("pdf_engine/test_verified_bold.pdf")
    p2 = pymupdf.open("pdf_engine/test_verified_bold.pdf")[0]
    spans2 = [s for b in p2.get_text("dict")["blocks"] if "lines" in b for l in b["lines"] for s in l["spans"] if "Python, C++" in s["text"]]
    s2 = spans2[0]
    print(f"[PASS] Test 2: Explicit Bold = True -> Font: {s2['font']}, Flags: {s2['flags']} (bit 16: {bool(s2['flags'] & 16)})")
    assert bool(s2['flags'] & 16) or "Bold" in s2['font'], "Should be bold!"

    # -------------------------------------------------------------
    # Test 3: User custom size 14.0pt via [+] button is honored
    # -------------------------------------------------------------
    doc3 = pymupdf.open("pdf_engine/fixed_resume_test.pdf")
    edit_custom = {
        "type": "replace",
        "page_index": 0,
        "oldText": "Python, C++, SQL",
        "newText": "Python, C++, SQL, Rust",
        "font": "TeXGyreTermesX-Regular",
        "size": 14.0,
        "flags": 4,
        "bbox": [100.9, 138.18, 175.0, 149.25],
        "is_bold": False,
        "custom_size": True
    }
    apply_universal_edit(doc3, edit_custom)
    doc3.save("pdf_engine/test_verified_custom.pdf")
    p3 = pymupdf.open("pdf_engine/test_verified_custom.pdf")[0]
    spans3 = [s for b in p3.get_text("dict")["blocks"] if "lines" in b for l in b["lines"] for s in l["spans"] if "Rust" in s["text"]]
    s3 = spans3[0]
    print(f"[PASS] Test 3: Custom size = {s3['size']}pt (Requested 14.0pt)")
    assert abs(s3['size'] - 14.0) < 0.1, f"Custom size not honored: {s3['size']}"

    print("\n>>> ALL ADVANCED TESTS PASSED 100%! <<<")

if __name__ == "__main__":
    test_suite()
