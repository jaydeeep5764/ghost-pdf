import pymupdf
import binascii
import re

def text_to_hex(s: str) -> bytes:
    try:
        return binascii.hexlify(s.encode('latin1')).lower()
    except Exception:
        return binascii.hexlify(s.encode('utf-8')).lower()

def escape_pdf_literal(s: str) -> str:
    return s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')

def patch_content_stream(doc: pymupdf.Document, page: pymupdf.Page, old_text: str, new_text: str) -> bool:
    """
    True low-level PostScript content stream editor.
    Directly mutates page content streams without any redaction overlays.
    """
    if not old_text:
        return False

    old_hex = text_to_hex(old_text)
    new_hex = text_to_hex(new_text)
    
    old_literal = old_text.encode('latin1', errors='ignore')
    new_literal = new_text.encode('latin1', errors='ignore')

    patched = False

    for xref in page.get_contents():
        raw_stream = doc.xref_stream(xref)
        if not raw_stream:
            continue

        modified = False

        # 1. Check for hex-encoded representation in <hex>
        if old_hex in raw_stream.lower():
            # Replace case-insensitively or exact
            lower_stream = raw_stream.lower()
            idx = lower_stream.find(old_hex)
            while idx != -1:
                raw_stream = raw_stream[:idx] + new_hex + raw_stream[idx+len(old_hex):]
                lower_stream = raw_stream.lower()
                idx = lower_stream.find(old_hex, idx + len(new_hex))
                modified = True
                patched = True

        # 2. Check for literal (string) Tj / TJ representations
        old_bracket = b'(' + old_literal + b')'
        new_bracket = b'(' + new_literal + b')'
        if old_bracket in raw_stream:
            raw_stream = raw_stream.replace(old_bracket, new_bracket)
            modified = True
            patched = True

        if modified:
            doc.update_stream(xref, raw_stream)

    return patched

def add_new_text_stream(doc: pymupdf.Document, page: pymupdf.Page, text: str, x: float, y: float, size: float = 12.0, color: tuple = (0, 0, 0), fontname: str = "helv"):
    """
    Directly injects a new BT...ET PostScript text stream at coordinate (x, y).
    """
    font_obj = pymupdf.Font(fontname)
    font_id = f"font_{fontname}"
    page.insert_font(fontname=font_id, fontbuffer=font_obj.buffer)

    r, g, b = color
    # Convert web top-down coordinates to PDF bottom-up coordinates
    pdf_y = page.rect.height - y
    
    # Insert cleanly via pymupdf low-level operator insertion
    page.insert_text(
        pymupdf.Point(x, y),
        text,
        fontsize=size,
        color=(r, g, b),
        fontname=font_id
    )
