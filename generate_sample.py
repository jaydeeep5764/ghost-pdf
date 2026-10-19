import pymupdf

def generate_sample_invoice(filename="pdf_engine/sample_invoice.pdf"):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4 size

    # Register fonts
    helv = pymupdf.Font("helv")
    page.insert_font(fontname="helv", fontbuffer=helv.buffer)

    # Subtle cream background to test background color sampling
    cream_color = (0.98, 0.98, 0.96)
    page.draw_rect(page.rect, color=None, fill=cream_color)

    # Header
    page.insert_text(pymupdf.Point(50, 80), "ACME CORPORATION", fontsize=20, fontname="helv", color=(0.1, 0.15, 0.3))
    page.insert_text(pymupdf.Point(50, 105), "123 Business Avenue, Suite 400", fontsize=10, fontname="helv", color=(0.4, 0.4, 0.4))

    # Divider line
    page.draw_line(pymupdf.Point(50, 120), pymupdf.Point(545, 120), color=(0.8, 0.8, 0.8), width=1)

    # Invoice details
    page.insert_text(pymupdf.Point(50, 160), "INVOICE #4029", fontsize=14, fontname="helv", color=(0.1, 0.1, 0.1))
    page.insert_text(pymupdf.Point(50, 185), "Date: October 19, 2026", fontsize=10.5, fontname="helv", color=(0.2, 0.2, 0.2))
    page.insert_text(pymupdf.Point(50, 205), "Customer: John Doe Enterprise", fontsize=10.5, fontname="helv", color=(0.2, 0.2, 0.2))

    # Items table
    page.draw_rect(pymupdf.Rect(50, 240, 545, 270), fill=(0.92, 0.93, 0.95), color=(0.8, 0.8, 0.8))
    page.insert_text(pymupdf.Point(60, 258), "Description", fontsize=10, fontname="helv", color=(0.2, 0.2, 0.2))
    page.insert_text(pymupdf.Point(460, 258), "Amount", fontsize=10, fontname="helv", color=(0.2, 0.2, 0.2))

    # Row 1
    page.insert_text(pymupdf.Point(60, 295), "Cloud Architecture & Reverse Engineering", fontsize=10.5, fontname="helv", color=(0.2, 0.2, 0.2))
    page.insert_text(pymupdf.Point(460, 295), "$500.00", fontsize=10.5, fontname="helv", color=(0.2, 0.2, 0.2))

    # Total Box
    page.draw_line(pymupdf.Point(50, 330), pymupdf.Point(545, 330), color=(0.8, 0.8, 0.8), width=1)
    page.insert_text(pymupdf.Point(340, 360), "Total Due: $500.00", fontsize=13.5, fontname="helv", color=(0.8, 0.1, 0.1))

    doc.save(filename)
    doc.close()
    print(f"Sample invoice created at: {filename}")

if __name__ == "__main__":
    generate_sample_invoice()
