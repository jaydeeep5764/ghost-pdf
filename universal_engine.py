"""
Clean, robust PDF text and image editing engine using PyMuPDF native redaction & vector injection.
Includes Dynamic Content Reflow and Anti-Overlap layout engine.
Guarantees clean erasure, automatic word shifting, line-wrapping, and vertical content shifting.
"""

import pymupdf
import os
import base64

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


def shift_content_vertically(page: pymupdf.Page, cutoff_y: float, delta_y: float):
    """
    Shifts all text lines, blocks, and images whose y0 >= cutoff_y - 2 by delta_y.
    Positive delta_y shifts downward (expansion); negative delta_y shifts upward (contraction).
    """
    if abs(delta_y) < 0.5:
        return

    doc = page.parent

    # 1. Collect all text spans below cutoff_y
    text_dict = page.get_text("dict")
    spans_to_shift = []
    for b in text_dict.get("blocks", []):
        if "lines" in b:
            for l in b["lines"]:
                if l["bbox"][1] >= cutoff_y - 2:
                    for s in l["spans"]:
                        spans_to_shift.append({
                            "text": s["text"],
                            "bbox": list(s["bbox"]),
                            "size": s["size"],
                            "font": s["font"],
                            "color": s["color"],
                            "flags": s["flags"]
                        })
                        page.add_redact_annot(pymupdf.Rect(s["bbox"]), fill=sample_background_color(page, pymupdf.Rect(s["bbox"])))

    # 2. Collect all images below cutoff_y
    images_to_shift = []
    for img_info in page.get_images():
        xref = img_info[0]
        for r in page.get_image_rects(xref):
            if r.y0 >= cutoff_y - 2:
                try:
                    img_data = doc.extract_image(xref)
                    images_to_shift.append({
                        "rect": pymupdf.Rect(r),
                        "bytes": img_data["image"],
                        "ext": img_data.get("ext", "png")
                    })
                    page.add_redact_annot(r, fill=sample_background_color(page, r))
                except Exception:
                    pass

    # Apply redactions to erase from original positions
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE)

    # Re-insert text spans at y + delta_y
    for s in spans_to_shift:
        c_int = s["color"]
        col = (((c_int >> 16) & 0xFF) / 255.0, ((c_int >> 8) & 0xFF) / 255.0, (c_int & 0xFF) / 255.0)
        base_y = s["bbox"][3] + delta_y - (s["size"] * 0.2)
        font_info = resolve_font(s["font"], s["flags"])
        page.insert_text(
            pymupdf.Point(s["bbox"][0], base_y),
            s["text"],
            fontsize=s["size"],
            color=col,
            fontname=font_info.get("name", "helv")
        )

    # Re-insert images at y + delta_y
    for im in images_to_shift:
        new_rect = pymupdf.Rect(im["rect"].x0, im["rect"].y0 + delta_y, im["rect"].x1, im["rect"].y1 + delta_y)
        try:
            page.insert_image(new_rect, stream=im["bytes"])
        except Exception as e:
            print(f"Shift image error: {e}")


def reflow_and_replace_text(doc: pymupdf.Document, page: pymupdf.Page, edit: dict):
    """
    Intelligent Dynamic Reflow Engine:
    - Replaces word(s) or paragraph
    - Shifts subsequent words on the same line to prevent collisions
    - Word-wraps long text across multiple lines
    - Shifts all content below downward (or upward) to guarantee ZERO overlapping
    """
    old_text = edit.get("oldText", "")
    new_text = edit.get("newText", edit.get("text", old_text)).strip()
    bbox = edit.get("bbox")
    font_name = edit.get("font", "helv")
    size = float(edit.get("size", 11.0))
    color_val = edit.get("color", "#000000")
    flags = int(edit.get("flags", 0))

    if not bbox:
        return

    target_rect = pymupdf.Rect(bbox)
    raw_words = page.get_text("words")

    # Match target word in raw_words
    target_word = None
    best_overlap = -1.0
    for w in raw_words:
        w_rect = pymupdf.Rect(*w[:4])
        overlap = (w_rect & target_rect).get_area()
        if overlap > best_overlap:
            best_overlap = overlap
            target_word = w

    same_line_following = []
    if target_word and best_overlap > 0:
        for w in raw_words:
            # Same block & line, following word_idx
            if (w[5] == target_word[5] and w[6] == target_word[6] and w[7] > target_word[7]) or \
               (abs((w[1]+w[3])/2 - (target_rect.y0+target_rect.y1)/2) < 5 and w[0] >= target_rect.x1 - 2):
                if w not in same_line_following:
                    same_line_following.append(w)
    else:
        for w in raw_words:
            if abs((w[1]+w[3])/2 - (target_rect.y0+target_rect.y1)/2) < 5 and w[0] >= target_rect.x1 - 2:
                same_line_following.append(w)

    same_line_following.sort(key=lambda w: w[0])

    font_info = resolve_font(font_name, flags)
    font_obj = pymupdf.Font(font_info.get("name", "helv"))
    line_height = size * 1.25

    # Right page margin for wrap boundary
    margin_right = page.rect.width - 45.0
    max_line_width = max(120.0, margin_right - target_rect.x0)

    # 1. Redact target
    bg_color = sample_background_color(page, target_rect)
    page.add_redact_annot(target_rect, fill=bg_color)

    # 2. Redact same_line_following words so they can be re-flowed
    for fw in same_line_following:
        fw_rect = pymupdf.Rect(*fw[:4])
        page.add_redact_annot(fw_rect, fill=sample_background_color(page, fw_rect))

    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

    # 3. Flow words
    new_words = new_text.split()
    following_word_texts = [w[4] for w in same_line_following]
    all_tokens = new_words + following_word_texts

    wrapped_lines = []
    curr_line = []
    curr_w = 0.0

    for token in all_tokens:
        tok_w = font_obj.text_length(token + " ", size)
        if curr_w + tok_w > max_line_width and curr_line:
            wrapped_lines.append(" ".join(curr_line))
            curr_line = [token]
            curr_w = tok_w
        else:
            curr_line.append(token)
            curr_w += tok_w

    if curr_line:
        wrapped_lines.append(" ".join(curr_line))

    # Calculate vertical shift delta
    num_lines = max(1, len(wrapped_lines))
    new_h = num_lines * line_height
    delta_y = (num_lines - 1) * line_height

    # Shift all content below target_rect.y1 if new lines were added
    if abs(delta_y) > 0.5:
        shift_content_vertically(page, target_rect.y1, delta_y)

    # Insert wrapped lines
    start_x = target_rect.x0
    start_base_y = target_rect.y1 - (size * 0.20)

    for i, line_str in enumerate(wrapped_lines):
        line_txt = line_str.strip()
        if line_txt:
            pt = pymupdf.Point(start_x, start_base_y + (i * line_height))
            page.insert_text(
                pt,
                line_txt,
                fontsize=size,
                color=parse_color(color_val),
                fontname=font_info.get("name", "helv")
            )


def replace_image_with_reflow(doc: pymupdf.Document, page: pymupdf.Page, edit: dict):
    """
    Replaces an image on the page and automatically reflows/shifts all content below it.
    If the new image is taller (e.g. 784x784 vs 512x512), all content below shifts down by delta_y.
    If the new image is shorter, all content below shifts up by delta_y towards the image.
    """
    bbox = edit.get("bbox")
    if not bbox:
        return

    old_rect = pymupdf.Rect(*bbox)
    b64_data = edit.get("image_base64", "")
    if "," in b64_data:
        b64_data = b64_data.split(",")[1]
    img_bytes = base64.b64decode(b64_data)

    pix = pymupdf.Pixmap(img_bytes)
    pixel_w = pix.width
    pixel_h = pix.height

    target_w = float(edit.get("target_width", old_rect.width))
    target_h = edit.get("target_height")
    if target_h is not None:
        target_h = float(target_h)
    else:
        # Scale proportionally to match target_w
        target_h = target_w * (pixel_h / max(1, pixel_w))

    delta_y = target_h - old_rect.height
    do_reflow = edit.get("reflow", True)

    # 1. Redact old image
    bg_color = sample_background_color(page, old_rect)
    page.add_redact_annot(old_rect, fill=bg_color)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE)

    # 2. Shift all content below if reflow is requested
    if do_reflow and abs(delta_y) > 0.5:
        shift_content_vertically(page, old_rect.y1, delta_y)

    # 3. Insert new image
    new_rect = pymupdf.Rect(old_rect.x0, old_rect.y0, old_rect.x0 + target_w, old_rect.y0 + target_h)
    page.insert_image(new_rect, stream=img_bytes)


def delete_image_with_reflow(doc: pymupdf.Document, page: pymupdf.Page, edit: dict):
    """
    Deletes an image and shifts all content below upward.
    """
    bbox = edit.get("bbox")
    if not bbox:
        return
    old_rect = pymupdf.Rect(*bbox)
    delta_y = -old_rect.height
    do_reflow = edit.get("reflow", True)

    bg_color = sample_background_color(page, old_rect)
    page.add_redact_annot(old_rect, fill=bg_color)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE)

    if do_reflow and abs(delta_y) > 0.5:
        shift_content_vertically(page, old_rect.y1, delta_y)


def apply_universal_edit(doc: pymupdf.Document, edit: dict):
    """
    Master Dispatcher for PDF edits:
    - 'replace': with dynamic reflow & anti-overlap
    - 'replace_image': replaces image and reflows/shifts lower content
    - 'delete_image': removes image and shifts lower content upward
    - 'move': relocates text
    - 'add': inserts new draggable text
    - 'redact': whiteout / redaction
    """
    p_idx = edit.get("page_index", 0)
    if p_idx < 0 or p_idx >= len(doc):
        return

    page = doc[p_idx]
    e_type = edit.get("type", "replace")

    if e_type == "replace_image":
        replace_image_with_reflow(doc, page, edit)
        return

    if e_type == "delete_image":
        delete_image_with_reflow(doc, page, edit)
        return

    if e_type == "replace":
        has_new_pos = (edit.get("new_x") is not None and edit.get("new_y") is not None)
        do_reflow = edit.get("reflow", True)

        if not has_new_pos and do_reflow:
            reflow_and_replace_text(doc, page, edit)
            return

        # Direct in-place or moved replacement without full document reflow
        old_text = edit.get("oldText", "")
        new_text = edit.get("newText", edit.get("text", old_text))
        old_clean = old_text.strip()
        new_clean = new_text.strip()

        bbox = edit.get("bbox")
        font_name = edit.get("font", "helv")
        size = float(edit.get("size", 11.0))
        color_val = edit.get("color", "#000000")
        flags = int(edit.get("flags", 0))

        target_rect = None
        if old_clean:
            rects = page.search_for(old_clean)
            if bbox:
                click_rect = pymupdf.Rect(bbox)
                best_overlap = -1.0
                for r in rects:
                    overlap = (r & click_rect).get_area()
                    if overlap > best_overlap:
                        best_overlap = overlap
                        target_rect = r

                if (target_rect is None or best_overlap <= 0) and rects:
                    target_rect = min(
                        rects,
                        key=lambda r: abs((r.y0 + r.y1)/2 - (click_rect.y0 + click_rect.y1)/2)
                                    + abs((r.x0 + r.x1)/2 - (click_rect.x0 + click_rect.x1)/2)
                    )

            if target_rect is None and rects:
                target_rect = rects[0]

        if target_rect is None:
            if bbox:
                target_rect = pymupdf.Rect(bbox)
            else:
                return

        bg_color = sample_background_color(page, target_rect)
        pad = 0.5
        erase_rect = pymupdf.Rect(
            target_rect.x0 - pad,
            target_rect.y0 - pad,
            target_rect.x1 + pad,
            target_rect.y1 + pad
        )
        page.add_redact_annot(erase_rect, fill=bg_color)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

        if not new_clean:
            return

        font_info = resolve_font(font_name, flags)
        active_font = font_info.get("name", "helv")

        lines = new_clean.split("\n")
        line_height = size * 1.25

        if has_new_pos:
            start_x = float(edit.get("new_x"))
            start_baseline_y = float(edit.get("new_y")) + (size * 0.82)
        else:
            start_x = target_rect.x0
            start_baseline_y = target_rect.y1 - (size * 0.20)

        for idx, line_str in enumerate(lines):
            line_txt = line_str.strip()
            if line_txt:
                insert_pt = pymupdf.Point(start_x, start_baseline_y + (idx * line_height))
                page.insert_text(
                    insert_pt,
                    line_txt,
                    fontsize=size,
                    color=parse_color(color_val),
                    fontname=active_font
                )

    elif e_type == "move":
        # Relocate text
        bbox = edit.get("bbox")
        if not bbox:
            return
        target_rect = pymupdf.Rect(bbox)
        new_clean = edit.get("newText", edit.get("text", "")).strip()

        bg_color = sample_background_color(page, target_rect)
        page.add_redact_annot(target_rect, fill=bg_color)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

        if not new_clean:
            return

        new_x = float(edit.get("new_x", target_rect.x0))
        new_y = float(edit.get("new_y", target_rect.y0))
        size = float(edit.get("size", 11.0))
        color_val = edit.get("color", "#000000")
        font_name = edit.get("font", "helv")
        flags = int(edit.get("flags", 0))

        font_info = resolve_font(font_name, flags)
        active_font = font_info.get("name", "helv")

        lines = new_clean.split("\n")
        line_height = size * 1.25
        baseline_y = new_y + (size * 0.82)

        for idx, line_str in enumerate(lines):
            line_txt = line_str.strip()
            if line_txt:
                insert_pt = pymupdf.Point(new_x, baseline_y + (idx * line_height))
                page.insert_text(
                    insert_pt,
                    line_txt,
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
        flags = int(edit.get("flags", 0))
        font_info = resolve_font(font_name, flags)
        active_font = font_info.get("name", "helv")

        lines = new_text.split("\n")
        line_height = size * 1.25
        baseline_y = y + (size * 0.82)

        for idx, line_str in enumerate(lines):
            line_txt = line_str.strip()
            if line_txt:
                insert_pt = pymupdf.Point(x, baseline_y + (idx * line_height))
                page.insert_text(
                    insert_pt,
                    line_txt,
                    fontsize=size,
                    color=parse_color(color_val),
                    fontname=active_font
                )

    elif e_type == "redact":
        bbox_list = []
        if edit.get("bboxes"):
            bbox_list = edit["bboxes"]
        elif edit.get("bbox"):
            bbox_list = [edit["bbox"]]

        for bb in bbox_list:
            rect = pymupdf.Rect(*bb)
            bg_color = sample_background_color(page, rect)
            pad = 0.5
            erase_rect = pymupdf.Rect(
                rect.x0 - pad,
                rect.y0 - pad,
                rect.x1 + pad,
                rect.y1 + pad
            )
            page.add_redact_annot(erase_rect, fill=bg_color)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
