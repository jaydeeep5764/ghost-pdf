import pymupdf

doc = pymupdf.open('pdf_engine/test_scanned_sample.pdf')
p = doc[0]

# User wants to replace "RECEIPT#5544" with "TAX INVOICE #8899"
# BBox from OCR: [59.5, 60.4, 133.0, 71.5]
target_rect = pymupdf.Rect(59.5, 60.4, 133.0, 71.5)

# 1. Sample background color around the target
pix = p.get_pixmap(clip=pymupdf.Rect(target_rect.x0 - 5, target_rect.y0 - 5, target_rect.x0, target_rect.y0))
bg_color = (pix.pixel(0, 0)[0]/255, pix.pixel(0, 0)[1]/255, pix.pixel(0, 0)[2]/255)
print('Sampled bg_color:', bg_color)

# 2. Erase the scanned text pixels by drawing a filled color-matched rectangle
erase_rect = pymupdf.Rect(target_rect.x0 - 2, target_rect.y0 - 2, target_rect.x1 + 2, target_rect.y1 + 2)
p.draw_rect(erase_rect, color=None, fill=bg_color)

# 3. Insert the new vector text
new_text = "TAX INVOICE #8899"
p.insert_text((target_rect.x0, target_rect.y1 - 1.5), new_text, fontsize=10.0, color=(0, 0, 0), fontname="helv")

doc.save('pdf_engine/test_scanned_edited.pdf')
doc.close()

# Verify text is now present in the PDF!
doc2 = pymupdf.open('pdf_engine/test_scanned_edited.pdf')
print('Text extracted after edit on scanned PDF:\n', doc2[0].get_text())
