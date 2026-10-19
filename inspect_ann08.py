import pymupdf

doc = pymupdf.open(r"C:\Users\Admin\Downloads\compare\ANN_08 (1).pdf")
page = doc[0]

print("=== SPANS ON PAGE 1 ===")
for block in page.get_text("dict")["blocks"][:6]:
    if "lines" in block:
        for line in block["lines"]:
            for span in line["spans"]:
                print(f"Text: '{span['text']}' | Font: {span['font']} | Size: {span['size']:.2f} | BBox: {span['bbox']}")
