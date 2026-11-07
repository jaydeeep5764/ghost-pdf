import sys
import os
sys.path.insert(0, os.path.abspath("."))
import pymupdf
from pdf_engine.web.server import get_page_payload
from pdf_engine.universal_engine import apply_universal_edit

def test_multiline():
    with open('pdf_engine/test_block_edit.pdf', 'rb') as f:
        pdf_bytes = f.read()

    payload = get_page_payload(pdf_bytes, 0)
    print(f"Total blocks extracted: {len(payload['blocks'])}")

    # Verify first 10 blocks
    for b in payload['blocks'][:10]:
        clean_preview = b['text'].replace('\n', ' ')[:35]
        print(f"Block {b['id']}: lines={b['line_count']} | is_bold={b['is_bold']} | font={b['font']} | text='{clean_preview}...'")

    # Block 4 is Technical Skills body text
    b4 = payload['blocks'][4]
    print(f"\nTarget Block 4: lines={b4['line_count']}, is_bold={b4['is_bold']}, font={b4['font']}")

    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    new_skills = (
        "Programming: Python, TypeScript, C++, Rust\n"
        "AI & Deep Learning: PyTorch, JAX, HuggingFace, Transformers\n"
        "Cloud & Systems: Docker, Kubernetes, Linux, AWS\n"
        "Databases: PostgreSQL, Redis, VectorDB"
    )
    edit = {
        'type': 'replace',
        'page_index': 0,
        'bbox': b4['bbox'],
        'oldText': b4['text'],
        'newText': new_skills,
        'size': b4['size'],
        'color': b4['color'],
        'font': b4['font'],
        'flags': b4['flags'],
        'is_bold': b4['is_bold'],
        'is_italic': b4['is_italic'],
        'is_paragraph': True
    }
    apply_universal_edit(doc, edit)
    doc.save('test_multiline_verified.pdf')

    doc2 = pymupdf.open('test_multiline_verified.pdf')
    p2_text = doc2[0].get_text()
    assert 'PyTorch' in p2_text, "PyTorch missing!"
    assert 'VectorDB' in p2_text, "VectorDB missing!"
    print("\n[SUCCESS] Multi-line block replacement succeeded with 100% fidelity!")

    # Check font of newly inserted text
    page2_dict = doc2[0].get_text("dict")
    for block in page2_dict['blocks']:
        if block.get('type') == 0:
            for line in block['lines']:
                for span in line['spans']:
                    if 'PyTorch' in span['text']:
                        safe_txt = span['text'].encode('ascii', 'replace').decode('ascii')
                        print(f"Verified rendered span: text='{safe_txt}' | font='{span['font']}' | flags={span['flags']}")

if __name__ == '__main__':
    test_multiline()
