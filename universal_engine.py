"""
Clean, robust PDF text editing engine using PyMuPDF native redaction & vector text injection.
Guarantees clean erasure of original text (no ghosting, no superimposed text, no duplicate boxes).
"""

import pymupdf
import os

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

def resolve_font(font_name: str, flags: int = 0) -> dict:
    """
    Resolves font styling and returns either a local TTF font file or standard PDF font name.
    """
    clean_name = font_name.lower().replace("+", " ").replace("-", " ")
    is_bold = bool(flags & 2) or any(k in clean_name for k in ["bold", "heavy", "black"])
    is_italic = bool(flags & 1) or any(k in clean_name for k in ["italic", "oblique"])

    # Monospace family (Python / code / terminal)
    if any(k in clean_name for k in ["dejavu", "consolas", "menlo", "fira", "sourcecode", "courier", "mono"]):
        bold_ttf = os.path.join(FONTS_DIR, "DejaVuSansMono-Bold.ttf")
        reg_ttf = os.path.join(FONTS_DIR, "DejaVuSansMono.ttf")
        if is_bold and os.path.exists(bold_ttf):
            return {"file": bold_ttf, "name": "DejaVuSansMono-Bold"}
        if os.path.exists(reg_ttf):
            return {"file": reg_ttf, "name": "DejaVuSansMono"}
        # Fallback to standard courier
        if is_bold and is_italic:
            return {"name": "courier-boldoblique"}
        if is_bold:
            return {"name": "courier-bold"}
        if is_italic:
            return {"name": "courier-oblique"}
        return {"name": "courier"}

    # Serif family (Times, Roman, Cambria, Georgia)
    if any(k in clean_name for k in ["times", "roman", "georgia", "cambria", "serif", "minion", "baskerville"]):
        if is_bold and is_italic:
            return {"name": "times-bolditalic"}
        if is_bold:
            return {"name": "times-bold"}
        if is_italic:
            return {"name": "times-italic"}
        return {"name": "times-roman"}

    # Sans-serif family (Helvetica, Arial, LiberationSans, etc. - default)
    if is_bold and is_italic:
        return {"name": "hebi"}
    if is_bold:
        return {"name": "hebo"}
    if is_italic:
        return {"name": "heit"}
    return {"name": "helv"}


def sample_background_color(page: pymupdf.Page, rect: pymupdf.Rect) -> tuple:
    """
    Samples background color behind text to ensure invisible, clean redaction.
    """
    try:
        pix = page.get_pixmap(clip=rect)
        if pix.width > 2 and pix.height > 2:
            px = pix.pixel(0, 0)
            return (px[0] / 255.0, px[1] / 255.0, px[2] / 255.0)
    except Exception:
        pass
    return (1.0, 1.0, 1.0)


def parse_color(color_val) -> tuple:
    """
    Parses hex string (#008000) or RGB tuple into (r, g, b) float tuple in [0.0, 1.0].
    """
    if isinstance(color_val, (list, tuple)) and len(color_val) >= 3:
        return tuple(float(c) for c in color_val[:3])
    if isinstance(color_val, str):
        c_str = color_val.lstrip("#")
        if len(c_str) == 6:
            return (
                int(c_str[0:2], 16) / 255.0,
                int(c_str[2:4], 16) / 255.0,
                int(c_str[4:6], 16) / 255.0,
            )
    return (0.0, 0.0, 0.0)


def apply_universal_edit(doc: pymupdf.Document, edit: dict):
    """
    Applies clean, atomic edits to a PDF document.
    """
    p_idx = edit.get("page_index", 0)
    if p_idx < 0 or p_idx >= len(doc):
        return

    page = doc[p_idx]
    e_type = edit.get("type", "replace")

    if e_type == "replace":
        old_text = edit.get("oldText", "")
        new_text = edit.get("newText", "")
        old_clean = old_text.strip()
        new_clean = new_text.strip()

        # Guard: If text is unchanged or empty, do nothing
        if not old_clean or old_clean == new_clean:
            return

        bbox = edit.get("bbox")
        font_name = edit.get("font", "helv")
        size = float(edit.get("size", 11.0))
        color_val = edit.get("color", "#000000")
        flags = int(edit.get("flags", 0))

        # 1. Target Rectangle Identification:
        # Search for occurrences of old_clean on this page
        rects = page.search_for(old_clean)
        target_rect = None

        if bbox:
            click_rect = pymupdf.Rect(bbox)
            best_overlap = -1.0
            for r in rects:
                overlap = (r & click_rect).get_area()
                if overlap > best_overlap:
                    best_overlap = overlap
                    target_rect = r

            # If no strict overlap (e.g. leading spaces in click bbox), pick closest by distance
            if (target_rect is None or best_overlap <= 0) and rects:
                target_rect = min(
                    rects,
                    key=lambda r: abs((r.y0 + r.y1)/2 - (click_rect.y0 + click_rect.y1)/2)
                                + abs((r.x0 + r.x1)/2 - (click_rect.x0 + click_rect.x1)/2)
                )

        if target_rect is None:
            if rects:
                target_rect = rects[0]
            elif bbox:
                target_rect = pymupdf.Rect(bbox)
            else:
                return

        # 2. Sample background color for seamless erasure
        bg_color = sample_background_color(page, target_rect)

        # 3. Clean Redaction (C++ MuPDF native stream cleaning)
        pad = 0.5
        erase_rect = pymupdf.Rect(
            target_rect.x0 - pad,
            target_rect.y0 - pad,
            target_rect.x1 + pad,
            target_rect.y1 + pad
        )
        page.add_redact_annot(erase_rect, fill=bg_color)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

        # 4. Font Resolution & Registration
        font_info = resolve_font(font_name, flags)
        font_tag = f"font_{abs(hash(font_name))%100000}_{'b' if (flags&2) else 'r'}"

        if "file" in font_info and os.path.exists(font_info["file"]):
            try:
                f_obj = pymupdf.Font(fontfile=font_info["file"])
                page.insert_font(fontname=font_tag, fontbuffer=f_obj.buffer)
                active_font = font_tag
            except Exception:
                active_font = font_info.get("name", "helv")
        else:
            active_font = font_info.get("name", "helv")

        # 5. Insert New Text
        baseline_y = target_rect.y1 - (size * 0.20)
        insert_pt = pymupdf.Point(target_rect.x0, baseline_y)

        page.insert_text(
            insert_pt,
            new_clean,
            fontsize=size,
            color=parse_color(color_val),
            fontname=active_font
        )

    elif e_type == "add":
        x = float(edit.get("x", 50))
        y = float(edit.get("y", 50))
        new_text = edit.get("newText", "").strip()
        if not new_text or new_text == "New Text":
            return

        size = float(edit.get("size", 12.0))
        color_val = edit.get("color", "#000000")
        font_name = edit.get("font", "helv")
        font_info = resolve_font(font_name)

        if "file" in font_info and os.path.exists(font_info["file"]):
            f_obj = pymupdf.Font(fontfile=font_info["file"])
            font_tag = f"font_add_{abs(hash(font_name))%100000}"
            try:
                page.insert_font(fontname=font_tag, fontbuffer=f_obj.buffer)
                active_font = font_tag
            except Exception:
                active_font = font_info.get("name", "helv")
        else:
            active_font = font_info.get("name", "helv")

        page.insert_text(
            pymupdf.Point(x, y),
            new_text,
            fontsize=size,
            color=parse_color(color_val),
            fontname=active_font
        )

    elif e_type == "redact":
        bbox = edit.get("bbox")
        if bbox:
            rect = pymupdf.Rect(*bbox)
            bg_color = sample_background_color(page, rect)
            page.add_redact_annot(rect, fill=bg_color)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
