import pymupdf
from lowlevel_editor import patch_content_stream

doc = pymupdf.open('sample_invoice.pdf')
page = doc[0]

old_val = "$500.00"
new_val = "$750.00"

res = patch_content_stream(doc, page, old_val, new_val)
print("patch_content_stream result:", res)
doc.save("lowlevel_out.pdf")
doc.close()

# Read back
doc2 = pymupdf.open("lowlevel_out.pdf")
print("=== VERIFYING RESULT ===")
for line in doc2[0].get_text().splitlines():
    print(line)
