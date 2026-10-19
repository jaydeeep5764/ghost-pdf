import pymupdf

doc = pymupdf.open(r"C:\Users\Admin\Downloads\compare\CS_08.pdf")
page = doc[0]

print("Contents xrefs:", page.get_contents())
for xref in page.get_contents():
    raw = doc.xref_stream(xref)
    text = raw.decode("latin1", errors="ignore")
    print(f"--- XREF {xref} (len {len(raw)}) ---")
    for line in text.splitlines()[:30]:
        if "BT" in line or "ET" in line or "Tf" in line or "Tj" in line or "TJ" in line:
            print("  ", line)
