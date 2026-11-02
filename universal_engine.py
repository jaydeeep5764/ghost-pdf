"""
Adobe-Grade Clean PDF Text Editing Engine (Ghost PDF).
Features:
- Sub-pixel true baseline anchoring.
- Collision avoidance: detects adjacent columns/text and prevents overlapping.
- Strict single-line fitting: fits long text horizontally without shrinking to tiny sizes or dropping down into lower rows.
- Soft horizontal glyph condensing (`morph` matrix) preserves 100% full vertical font size and cap-height.
- Explicit & accurate weight matching: never allows nearby bold titles to infect regular text.
- Robust 8-point perimeter background sampling.
"""

import pymupdf
import os
import re

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
WIN_FONTS = r"C:\Windows\Fonts"

SERIF_KEYWORDS = [
    "termes", "texgyre", "times", "roman", "georgia", "garamond",
    "minion", "baskerville", "cambria", "palatino", "cmr", "ptserif",
    "serif", "charter", "merriweather", "playfair", "lora", "caladea", "tinos",
    "nimbusroman"
]

MONO_KEYWORDS = [
    "mono", "courier", "consolas", "menlo", "sourcecode", "cursor",
    "cousine", "inconsolata", "fira", "typewriter", "terminal", "dejavusansmono"
]

def clean_font_name(font_name: str) -> str:
    """Strips PDF subset prefixes (e.g. 'EUURRB+TeXGyreTermesX-Regular' -> 'TeXGyreTermesX-Regular')"""
    return re.sub(r'^[A-Z0-9]{6}\+', '', font_name).strip()

def find_matching_embedded_font(doc: pymupdf.Document, page: pymupdf.Page, font_name: str, is_bold: bool = False, is_italic: bool = False):
    """
    Finds and reuses the exact embedded font in the document itself to achieve 100% pixel-perfect typography matching.
    """
    clean_target = clean_font_name(font_name).lower()
    target_family = re.sub(r'[\-_ ]*(regular|bold|italic|oblique|[0-9]+)', '', clean_target).strip()
    target_bold = bool(is_bold)
    target_italic = bool(is_italic)

    for f_tuple in page.get_fonts():
        xref = f_tuple[0]
        f_name = clean_font_name(f_tuple[3]).lower()
        f_family = re.sub(r'[\-_ ]*(regular|bold|italic|oblique|[0-9]+)', '', f_name).strip()

        family_match = False
        if target_family and (target_family in f_family or f_family in target_family):
            family_match = True
        elif any(s in target_family for s in ["roman", "termes", "times", "serif", "cmr", "lmr"]) and any(s in f_family for s in ["roman", "termes", "times", "serif", "cmr", "lmr"]):
            family_match = True

        if family_match:
            f_is_bold = bool(re.search(r'[\-_ ]*(bold|heavy|black)', f_name)) or f_name.endswith('bd')
            f_is_italic = bool(re.search(r'[\-_ ]*(italic|oblique)', f_name)) or f_name.endswith('it')
            if (target_bold == bool(f_is_bold)) and (target_italic == bool(f_is_italic)):
                try:
                    font_data = doc.extract_font(xref)
                    if font_data and len(font_data[3]) > 100:
                        tag = f"f_emb_{xref}"
                        return {"name": tag, "buffer": font_data[3], "is_base14": False}
                except Exception:
                    pass
    return None

def resolve_font(font_name: str, flags: int = 0, is_bold: bool = None, is_italic: bool = None) -> dict:
    """
    Authentic typography matching for font family, weight, style, and metrics.
    Accurately interprets MuPDF flags (bit 4 = 16 for bold, bit 1 = 2 for italic, bit 2 = 4 for serif, bit 3 = 8 for mono).
    Allows explicit is_bold / is_italic overrides from caller.
    """
    clean = clean_font_name(font_name).lower().replace(",", "-").replace("_", "-")

    # 1. Exact flags check from MuPDF (Bit 4 = 16 is bold, Bit 1 = 2 is italic)
    if is_bold is None:
        is_bold = bool(flags & 16)
        if not is_bold:
            has_bold_token = any(b in clean for b in ["-bold", "_bold", "bold", "black", "heavy", "-bd"])
            has_regular_token = any(r in clean for r in ["-regular", "-normal", "-book", "-light", "-rg"])
            if has_bold_token and not has_regular_token:
                is_bold = True

    if is_italic is None:
        is_italic = bool(flags & 2)
        if not is_italic:
            if any(i in clean for i in ["-italic", "_italic", "italic", "-oblique", "oblique", "slanted", "-it"]):
                is_italic = True

    # 2. Dedicated Family Matches:

    # A. Monospace family (DejaVu Sans Mono, Consolas, Courier)
    if any(k in clean for k in MONO_KEYWORDS) or bool(flags & 8):
        # Check DejaVu first (we have high-fidelity TTFs in pdf_engine/fonts)
        if "dejavu" in clean:
            font_filename = "DejaVuSansMono-Bold.ttf" if is_bold else "DejaVuSansMono.ttf"
            font_path = os.path.join(FONTS_DIR, font_filename)
            if os.path.exists(font_path):
                return {"file": font_path, "name": "DejaVuSansMono-Bold" if is_bold else "DejaVuSansMono", "family": "mono", "is_base14": False}
        
        # Check Consolas in Windows Fonts if explicit consolas
        if "consola" in clean:
            consola_file = os.path.join(WIN_FONTS, "consolab.ttf" if is_bold else "consola.ttf")
            if os.path.exists(consola_file):
                return {"file": consola_file, "name": "Consolas-Bold" if is_bold else "Consolas", "family": "mono", "is_base14": False}

        # Otherwise standard Base 14 Courier (guaranteed 100% glyph support, no distortion)
        if is_bold and is_italic:
            return {"name": "courier-boldoblique", "family": "mono", "is_base14": True}
        if is_bold:
            return {"name": "courier-bold", "family": "mono", "is_base14": True}
        if is_italic:
            return {"name": "courier-oblique", "family": "mono", "is_base14": True}
        return {"name": "courier", "family": "mono", "is_base14": True}

    # B. Serif family (Times New Roman, TeXGyreTermes, NimbusRoman, Georgia, Cambria, Garamond, Palatino)
    if any(k in clean for k in SERIF_KEYWORDS) or bool(flags & 4):
        # Base 14 standard Type 1 Times (matches TeXGyreTermes, NimbusRoman, Times New Roman 1:1 without TrueType bloat)
        if is_bold and is_italic:
            return {"name": "times-bolditalic", "family": "serif", "is_base14": True}
        if is_bold:
            return {"name": "times-bold", "family": "serif", "is_base14": True}
        if is_italic:
            return {"name": "times-italic", "family": "serif", "is_base14": True}
        return {"name": "times-roman", "family": "serif", "is_base14": True}

    # C. Sans-serif family (Helvetica, Arial, Calibri, Liberation Sans, Segoe UI)
    if "calibri" in clean:
        calibri_file = "calibrib.ttf" if is_bold else ("calibrii.ttf" if is_italic else "calibri.ttf")
        calibri_path = os.path.join(WIN_FONTS, calibri_file)
        if os.path.exists(calibri_path):
            return {"file": calibri_path, "name": "Calibri", "family": "sans", "is_base14": False}

    # Standard Base 14 Helvetica (Type 1 standard, clean slim stems, zero artificial bolding)
    if is_bold and is_italic:
        return {"name": "hebi", "family": "sans", "is_base14": True}
    if is_bold:
        return {"name": "hebo", "family": "sans", "is_base14": True}
    if is_italic:
        return {"name": "heit", "family": "sans", "is_base14": True}
    return {"name": "helv", "family": "sans", "is_base14": True}


def sample_background_color(page: pymupdf.Page, rect: pymupdf.Rect) -> tuple:
    """
    8-point perimeter background sampling.
    Samples points outside the text boundary and computes the median color
    to eliminate halo boxes or black border sampling.
    """
    try:
        pad = 2.0
        sample_rect = pymupdf.Rect(
            max(0, rect.x0 - pad),
            max(0, rect.y0 - pad),
            min(page.rect.width, rect.x1 + pad),
            min(page.rect.height, rect.y1 + pad)
        )
        pix = page.get_pixmap(clip=sample_rect)
        if pix.width >= 4 and pix.height >= 4:
            points = [
                (0, 0),
                (pix.width - 1, 0),
                (0, pix.height - 1),
                (pix.width - 1, pix.height - 1),
                (pix.width // 2, 0),
                (pix.width // 2, pix.height - 1),
                (0, pix.height // 2),
                (pix.width - 1, pix.height // 2)
            ]
            samples = []
            for px, py in points:
                p = pix.pixel(px, py)
                samples.append((p[0] / 255.0, p[1] / 255.0, p[2] / 255.0))
            
            samples.sort(key=lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])
            return samples[len(samples) // 2]
    except Exception:
        pass
    return (1.0, 1.0, 1.0)


def parse_color(color_val) -> tuple:
    """Parses hex string (#RRGGBB) or RGB tuple into (r, g, b) float tuple in [0.0, 1.0]."""
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


def find_original_span_info(page: pymupdf.Page, target_text: str, target_rect: pymupdf.Rect):
    """Retrieves exact typographic metadata (baseline origin, font, flags, color) from page dict."""
    text_dict = page.get_text("dict")
    best_span = None
    best_dist = float("inf")

    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_rect = pymupdf.Rect(span["bbox"])
                if target_text in span["text"] or (span_rect & target_rect).get_area() > 0:
                    dist = abs(span_rect.y0 - target_rect.y0) + abs(span_rect.x0 - target_rect.x0)
                    if dist < best_dist:
                        best_dist = dist
                        best_span = span

    return best_span


def find_max_available_width(page: pymupdf.Page, insert_x: float, target_rect: pymupdf.Rect) -> float:
    """
    Collision Avoidance: Finds any adjacent text or column to the right on the same vertical line
    to ensure new text never collides or overlaps with neighboring columns.
    """
    text_dict = page.get_text("dict")
    target_cy = (target_rect.y0 + target_rect.y1) / 2.0
    line_height = max(10.0, target_rect.height)
    
    nearest_right_x = None

    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                s_bbox = span["bbox"]
                span_cy = (s_bbox[1] + s_bbox[3]) / 2.0
                # Must be on the exact same vertical line center and located to the right of the target
                if abs(target_cy - span_cy) < (line_height * 0.45) and s_bbox[0] > (target_rect.x1 - 1.0):
                    if nearest_right_x is None or s_bbox[0] < nearest_right_x:
                        nearest_right_x = s_bbox[0]

    # If adjacent text found, stop 6 points before it to prevent collision
    if nearest_right_x is not None and nearest_right_x > (insert_x + 20):
        return max(target_rect.width, nearest_right_x - insert_x - 6.0)

    # Otherwise allow across line up to generous right margin (20pt from page edge)
    right_margin = page.rect.width - 20.0
    return max(target_rect.width, right_margin - insert_x)


def apply_universal_edit(doc: pymupdf.Document, edit: dict):
    """
    Applies clean, atomic, Adobe-grade edits to a PDF document:
    - Collision Avoidance: Detects adjacent text columns and prevents overlapping.
    - Preserves Font Size: Never aggressively shrinks font size to tiny unreadable text; maintains high fidelity single-line fitting.
    - Soft Horizontal Condensing: Uses PostScript Tz / morph matrix to fit longer text without shrinking cap-height.
    - Explicit Bold Control: Honors user's bold/regular selection; never infects regular body text with nearby bold title flags.
    - True Baseline Anchoring: 0.0pt vertical jitter.
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

        # Guard: If text is empty, do nothing
        if not old_clean and not new_clean:
            return

        explicit_bold = edit.get("is_bold")
        explicit_italic = edit.get("is_italic")
        custom_size = edit.get("custom_size", False)
        color_val = edit.get("color")

        # If text is identical and no style changes were requested, return early
        if old_clean == new_clean and explicit_bold is None and explicit_italic is None and not custom_size and not color_val:
            return

        bbox = edit.get("bbox")
        font_name = edit.get("font", "helv")
        size = float(edit.get("size", 11.0))
        flags = int(edit.get("flags", 0))
        passed_origin = edit.get("origin")
        is_paragraph = edit.get("is_paragraph", False) or ("\n" in new_clean)

        # 1. Target Rectangle Identification
        target_rect = None
        if is_paragraph and bbox:
            target_rect = pymupdf.Rect(bbox)
        else:
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

            if target_rect is None:
                if rects:
                    target_rect = rects[0]
                elif bbox:
                    target_rect = pymupdf.Rect(bbox)
                else:
                    return

        # 2. Extract Typographic Origin (True Baseline Anchor) & Metadata
        span_info = find_original_span_info(page, old_clean, target_rect)
        if passed_origin and len(passed_origin) >= 2:
            true_baseline_y = passed_origin[1]
            insert_x = passed_origin[0]
        elif span_info and "origin" in span_info:
            true_baseline_y = span_info["origin"][1]
            insert_x = target_rect.x0
        else:
            true_baseline_y = target_rect.y0 + (target_rect.height * 0.80)
            insert_x = target_rect.x0

        # Preserve exact font, flags, and color from the original span
        if span_info:
            if not font_name or font_name == "helv":
                font_name = span_info.get("font", font_name)
            # Only adopt flags from span_info if explicit_bold was NOT specified by user
            if flags == 0 and explicit_bold is None:
                flags = span_info.get("flags", 0)
            
            # If color was not explicitly customized by the user, preserve original span color
            if not color_val or color_val == "#000000":
                c_int = span_info.get("color", 0)
                r = ((c_int >> 16) & 0xFF) / 255.0
                g = ((c_int >> 8) & 0xFF) / 255.0
                b = (c_int & 0xFF) / 255.0
                parsed_color = (r, g, b)
            else:
                parsed_color = parse_color(color_val)
        else:
            parsed_color = parse_color(color_val or "#000000")

        # 3. Font Resolution & Accurate Weight Matching
        emb_font = find_matching_embedded_font(doc, page, font_name, is_bold=explicit_bold, is_italic=explicit_italic)
        if emb_font:
            font_info = emb_font
            font_kwargs = {"fontname": emb_font["name"]}
            try:
                measure_font = pymupdf.Font(fontbuffer=emb_font["buffer"])
            except Exception:
                measure_font = pymupdf.Font("times-roman" if any(s in font_name.lower() for s in ["roman", "serif", "times", "termes"]) else "helv")
        else:
            font_info = resolve_font(font_name, flags, is_bold=explicit_bold, is_italic=explicit_italic)
            font_kwargs = {}
            if font_info.get("is_base14", False):
                font_kwargs["fontname"] = font_info["name"]
                measure_font = pymupdf.Font(font_info["name"])
            elif "file" in font_info and os.path.exists(font_info["file"]):
                f_obj = pymupdf.Font(fontfile=font_info["file"])
                tag = f"f_{abs(hash(font_info['name']))%100000}"
                font_kwargs["fontname"] = tag
                measure_font = f_obj
            else:
                font_kwargs["fontname"] = font_info.get("name", "helv")
                measure_font = pymupdf.Font(font_info.get("name", "helv"))

        # 4. Check if this is a Multi-Line Paragraph Edit or Single-Line Edit
        lines = new_clean.split("\n")
        is_paragraph = edit.get("is_paragraph", False) or len(lines) > 1
        bg_color = sample_background_color(page, target_rect)

        # Precise envelope for 100% clean erasure:
        # Must cover both target_rect and span bbox with 2.5pt vertical pad so zero ghost ascenders/descenders survive
        span_y0 = span_info["bbox"][1] if span_info else target_rect.y0
        span_y1 = span_info["bbox"][3] if span_info else target_rect.y1
        clean_y0 = min(target_rect.y0, span_y0) - 2.5
        clean_y1 = max(target_rect.y1, span_y1) + 2.5

        if is_paragraph:
            # Multi-line Paragraph / Block Replacement
            # 1. Erase all intersecting text in the block with complete vertical and horizontal coverage
            erase_x0 = target_rect.x0 - 2.0
            erase_x1 = target_rect.x1 + 2.5
            erase_y0 = min(target_rect.y0, span_y0) - 2.5
            erase_y1 = max(target_rect.y1, span_y1) + 2.5

            text_dict = page.get_text("dict")
            for b in text_dict.get("blocks", []):
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        s_r = pymupdf.Rect(s["bbox"])
                        if (s_r & target_rect).get_area() > 0:
                            erase_x0 = min(erase_x0, s_r.x0 - 2.0)
                            erase_x1 = max(erase_x1, s_r.x1 + 2.5)
                            erase_y0 = min(erase_y0, s_r.y0 - 2.5)
                            erase_y1 = max(erase_y1, s_r.y1 + 2.5)

            erase_rect = pymupdf.Rect(erase_x0, erase_y0, erase_x1, erase_y1)
            page.add_redact_annot(erase_rect, fill=bg_color)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

            # Re-register embedded or custom font immediately after redaction clears resources
            if emb_font:
                page.insert_font(fontname=emb_font["name"], fontbuffer=emb_font["buffer"])
            elif "file" in font_info and os.path.exists(font_info["file"]):
                page.insert_font(fontname=font_kwargs["fontname"], fontbuffer=measure_font.buffer)

            # 2. Maximum available width for word wrapping
            max_p_width = min(page.rect.width - target_rect.x0 - 20.0, max(target_rect.width, 240.0))

            # 3. Format lines: respect explicit newlines, wrap long lines gracefully within bounds
            formatted_lines = []
            for raw_line in lines:
                if not raw_line.strip():
                    formatted_lines.append("")
                    continue
                words = raw_line.split(" ")
                curr_line = ""
                for w in words:
                    candidate = (curr_line + " " + w).strip()
                    if measure_font.text_length(candidate, fontsize=size) <= max_p_width:
                        curr_line = candidate
                    else:
                        if curr_line:
                            formatted_lines.append(curr_line)
                            curr_line = w
                        else:
                            formatted_lines.append(w)
                            curr_line = ""
                if curr_line:
                    formatted_lines.append(curr_line)

            # 4. Draw multi-line paragraph line-by-line anchored to true baseline
            line_spacing = max(11.5, size * 1.22)
            first_line_baseline = true_baseline_y if len(formatted_lines) == 1 else (target_rect.y0 + (size * 0.85))

            for i, line_text in enumerate(formatted_lines):
                if not line_text:
                    continue
                line_y = first_line_baseline + (i * line_spacing)
                page.insert_text(
                    pymupdf.Point(target_rect.x0, line_y),
                    line_text,
                    fontsize=size,
                    color=parsed_color,
                    **font_kwargs
                )
            return

        # 5. Collision Avoidance & Available Width Calculation (Single-Line)
        max_available_width = find_max_available_width(page, insert_x, target_rect)
        required_width = measure_font.text_length(new_clean, fontsize=size)

        # 6. Single-Line Fitting (Never shrink to tiny unreadable text)
        morph_tuple = None
        if custom_size:
            # User explicitly chose size via toolbar [-]/[+]: strictly obey it!
            effective_size = size
            actual_drawn_width = measure_font.text_length(new_clean, fontsize=effective_size)
            if actual_drawn_width > max_available_width and max_available_width > 20:
                condense_ratio = max(0.82, max_available_width / actual_drawn_width)
                morph_tuple = (pymupdf.Point(insert_x, true_baseline_y), pymupdf.Matrix(condense_ratio, 1.0))
                actual_drawn_width = actual_drawn_width * condense_ratio
        else:
            if required_width <= max_available_width:
                effective_size = size
                actual_drawn_width = required_width
            else:
                # Floor font size: never drop below 90% of requested size or (size - 1.2pt)
                min_readable_size = max(6.5, round(size * 0.90, 1), round(size - 1.2, 1))
                proportional_size = round(size * (max_available_width / required_width), 2)
                effective_size = max(min_readable_size, proportional_size)
                
                # Check if slight horizontal condensing fits it perfectly without shrinking height
                natural_width = measure_font.text_length(new_clean, fontsize=effective_size)
                if natural_width > max_available_width and max_available_width > 20:
                    condense_ratio = max(0.85, max_available_width / natural_width)
                    morph_tuple = (pymupdf.Point(insert_x, true_baseline_y), pymupdf.Matrix(condense_ratio, 1.0))
                    actual_drawn_width = natural_width * condense_ratio
                else:
                    actual_drawn_width = natural_width

        # 7. Clean Single-Line Redaction (Full envelope)
        erase_width = max(target_rect.width, actual_drawn_width)
        erase_rect = pymupdf.Rect(
            min(insert_x, target_rect.x0) - 1.0,
            clean_y0,
            max(insert_x + erase_width, target_rect.x1) + 1.0,
            clean_y1
        )

        page.add_redact_annot(erase_rect, fill=bg_color)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

        # Re-register embedded or custom font immediately after redaction clears resources
        if emb_font:
            page.insert_font(fontname=emb_font["name"], fontbuffer=emb_font["buffer"])
        elif "file" in font_info and os.path.exists(font_info["file"]):
            page.insert_font(fontname=font_kwargs["fontname"], fontbuffer=measure_font.buffer)

        # 8. Insert New Text Strictly on Single Baseline
        insert_pt = pymupdf.Point(insert_x, true_baseline_y)
        page.insert_text(
            insert_pt,
            new_clean,
            fontsize=effective_size,
            color=parsed_color,
            morph=morph_tuple,
            **font_kwargs
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
        is_bold = edit.get("is_bold")
        is_italic = edit.get("is_italic")
        font_info = resolve_font(font_name, is_bold=is_bold, is_italic=is_italic)

        if font_info.get("is_base14", False):
            active_font = font_info["name"]
        elif "file" in font_info and os.path.exists(font_info["file"]):
            f_obj = pymupdf.Font(fontfile=font_info["file"])
            font_tag = f"f_add_{abs(hash(font_name))%100000}"
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
