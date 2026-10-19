import pymupdf
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from universal_engine import apply_universal_edit

def test_file(path, search_text, replace_text, out_path):
    if not os.path.exists(path):
        print(f"Skipping {path} (not found)")
        return False
    doc = pymupdf.open(path)
    page = doc[0]
    rects = page.search_for(search_text)
    if not rects:
        print(f"Text '{search_text}' not found in {path}")
        doc.close()
        return False
    
    # Get font & size from span
    spans = [s for b in page.get_text("dict")["blocks"] if "lines" in b for l in b["lines"] for s in l["spans"] if search_text in s["text"]]
    font = spans[0]["font"] if spans else "helv"
    size = spans[0]["size"] if spans else 11.0
    flags = spans[0]["flags"] if spans else 0

    edit = {
        "page_index": 0,
        "type": "replace",
        "oldText": search_text,
        "newText": replace_text,
        "bbox": list(rects[0]),
        "font": font,
        "size": size,
        "flags": flags,
        "color": "#000000"
    }

    apply_universal_edit(doc, edit)
    doc.save(out_path, garbage=4, deflate=True)
    doc.close()

    # Re-open and check text
    doc_out = pymupdf.open(out_path)
    text = doc_out[0].get_text()
    doc_out.close()

    success = replace_text in text
    print(f"[*] {os.path.basename(path)}: Edit '{search_text}' -> '{replace_text}': {'PASS' if success else 'FAIL'}")
    return success

print("=== RUNNING UNIVERSAL ENGINE VERIFICATION TEST ===")
t1 = test_file("pdf_engine/sample_invoice.pdf", "$500.00", "$999.00", "pdf_engine/test_uni_invoice.pdf")
t2 = test_file(r"C:\Users\Admin\Downloads\compare\Ajay_Sharma_Resume.pdf", "Ajay Sharma", "Jaydeep Sapatale", "pdf_engine/test_uni_resume.pdf")
t3 = test_file(r"C:\Users\Admin\Downloads\compare\ANN_08 (1).pdf", "EXPERIMENT_NO : 08", "EXPERIMENT_NO : 10", "pdf_engine/test_uni_ann.pdf")

print(f"\nSummary: {sum([t1, t2, t3])}/3 Tests Passed!")
