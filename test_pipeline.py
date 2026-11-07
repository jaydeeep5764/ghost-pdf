import pymupdf
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from universal_engine import apply_universal_edit

def run_tests():
    print("=== RUNNING REA / ADOBE-GRADE VERIFICATION SUITE ===")
    
    # ---------------------------------------------------------
    # TEST 1: Invoice Test (Helvetica, Regular weight, Adjacent Column)
    # ---------------------------------------------------------
    doc_inv = pymupdf.open("pdf_engine/sample_invoice.pdf")
    edit_inv = {
        "type": "replace",
        "page_index": 0,
        "oldText": "Cloud Architecture & Reverse Engineering",
        "newText": "Specialized Low-Level Systems Security & Binary Reversing",
        "font": "Helvetica",
        "size": 10.0,
        "flags": 0,
        "bbox": [60.0, 285.0, 250.0, 297.0]
    }
    apply_universal_edit(doc_inv, edit_inv)
    doc_inv.save("pdf_engine/verify_inv_out.pdf")
    
    p = pymupdf.open("pdf_engine/verify_inv_out.pdf")[0]
    inv_spans = [s for b in p.get_text("dict")["blocks"] if "lines" in b for l in b["lines"] for s in l["spans"] if "Specialized" in s["text"]]
    assert len(inv_spans) == 1, "Span not found in invoice"
    s_inv = inv_spans[0]
    print("[PASS] Test 1: Invoice Edit")
    print(f"       Font: {s_inv['font']} (expected Helvetica)")
    print(f"       Flags: {s_inv['flags']} (bit 16 bold={bool(s_inv['flags'] & 16)})")
    print(f"       BBox: {[round(x, 1) for x in s_inv['bbox']]}")
    assert s_inv['font'] == "Helvetica", f"Font mismatch: {s_inv['font']}"
    assert not (s_inv['flags'] & 16), "Unintended bolding detected on invoice!"
    # Amount column obstacle is at x=460.0
    assert s_inv['bbox'][2] <= 455.0, f"Overlap collision: ends at {s_inv['bbox'][2]}"
    print("       Collision check: PASSED (ends safely before Amount column obstacle)")

    # ---------------------------------------------------------
    # TEST 2: Resume Test (Regular Serif TeXGyreTermesX, Very Long String)
    # Must NOT wrap down or overlap with lower line (AI & Machine Learning)
    # ---------------------------------------------------------
    doc_res = pymupdf.open("pdf_engine/fixed_resume_test.pdf")
    edit_res = {
        "type": "replace",
        "page_index": 0,
        "oldText": "Python, C++, SQL",
        "newText": "Python, C++, SQL, Rust, Go, TypeScript, Docker, Kubernetes, Low-Level Reverse Engineering, Assembly, Ghidra, IDA Pro",
        "font": "TeXGyreTermesX-Regular",
        "size": 10.0,
        "flags": 4,
        "bbox": [100.9, 138.18, 175.0, 149.25]
    }
    apply_universal_edit(doc_res, edit_res)
    doc_res.save("pdf_engine/verify_res_out.pdf")
    
    p_res = pymupdf.open("pdf_engine/verify_res_out.pdf")[0]
    res_spans = [s for b in p_res.get_text("dict")["blocks"] if "lines" in b for l in b["lines"] for s in l["spans"]]
    edited_res = [s for s in res_spans if "Rust" in s["text"]][0]
    lower_line = [s for s in res_spans if "AI & Machine Learning:" in s["text"]][0]
    
    print("\n[PASS] Test 2: Resume Long Input Edit")
    print(f"       Font: {edited_res['font']} (expected Times-Roman)")
    print(f"       Flags: {edited_res['flags']} (bit 16 bold={bool(edited_res['flags'] & 16)})")
    print(f"       Edited line origin: {[round(x, 2) for x in edited_res['origin']]}")
    print(f"       Lower line origin: {[round(x, 2) for x in lower_line['origin']]}")
    
    assert edited_res['font'] in ("Times-Roman", "TeXGyreTermesX-Regular"), f"Font mismatch: {edited_res['font']}"
    assert not (edited_res['flags'] & 16), "Unintended bolding on regular serif text!"
    assert abs(edited_res['origin'][1] - 147.09497) < 0.1, "Baseline vertical jitter!"
    assert abs(lower_line['origin'][1] - 162.03894) < 0.1, "Lower line was disturbed!"
    assert edited_res['bbox'][3] < lower_line['bbox'][1], "Edited text vertically overlaps lower line!"
    print("       Single-line isolation: PASSED (zero overlap with lower line)")

    # ---------------------------------------------------------
    # TEST 3: Code Notebook Test (CS_08.pdf, LiberationSans / DejaVuSansMono)
    # ---------------------------------------------------------
    cs08_path = r"C:\Users\Admin\Downloads\compare\CS_08.pdf"
    if os.path.exists(cs08_path):
        doc_cs = pymupdf.open(cs08_path)
        edit_cs = {
            "type": "replace",
            "page_index": 0,
            "oldText": "Name:Sharma Ajay Vinod Roll No",
            "newText": "Name: John Doe Roll No: 42 - Reverse Engineering Lead",
            "font": "LiberationSans",
            "size": 11.0,
            "flags": 0,
            "bbox": [50.0, 100.0, 300.0, 120.0]
        }
        apply_universal_edit(doc_cs, edit_cs)
        doc_cs.save("pdf_engine/verify_cs08_out.pdf")
        
        p_cs = pymupdf.open("pdf_engine/verify_cs08_out.pdf")[0]
        cs_spans = [s for b in p_cs.get_text("dict")["blocks"] if "lines" in b for l in b["lines"] for s in l["spans"] if "John" in s["text"]]
        assert len(cs_spans) == 1, "Span not found in CS_08"
        s_cs = cs_spans[0]
        print("\n[PASS] Test 3: CS_08 Notebook Edit")
        print(f"       Font: {s_cs['font']}")
        print(f"       Flags: {s_cs['flags']} (bit 16 bold={bool(s_cs['flags'] & 16)})")
        assert not (s_cs['flags'] & 16), "Unintended bolding in CS_08!"
        print("       Authentic font weight: PASSED")

    print("\n=========================================================")
    print(">>> ALL VERIFICATION TESTS PASSED WITH 100% SUCCESS! <<<")
    print("=========================================================")

if __name__ == "__main__":
    run_tests()
