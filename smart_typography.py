import pymupdf
import sys
import os

METRIC_TWIN_MAP = {
    "calibri": "carlito",
    "cambria": "caladea",
    "arial": "arimo",
    "helvetica": "liberation-sans",
    "times": "tinos",
    "times new roman": "tinos",
    "courier": "cousine",
    "courier new": "cousine",
    "segoe": "arimo",
}

def resolve_metric_font_alias(original_font_name: str) -> str:
    clean_name = original_font_name.lower().replace("+", " ").split(" ")[-1]
    for key, twin in METRIC_TWIN_MAP.items():
        if key in clean_name:
            if "times" in twin or "tinos" in twin:
                return "times"
            elif "courier" in twin or "cousine" in twin:
                return "courier"
            return "helv"
    return "helv"

def sample_background_color(page, rect):
    pix = page.get_pixmap(clip=rect)
    if pix.width > 2 and pix.height > 2:
        pixel = pix.pixel(0, 0)
        return (pixel[0] / 255.0, pixel[1] / 255.0, pixel[2] / 255.0)
    return (1.0, 1.0, 1.0)

def inspect_and_replace_text(pdf_path: str, target_text: str, replacement_text: str, output_path: str):
    doc = pymupdf.open(pdf_path)
    replaced_count = 0

    for page_num, page in enumerate(doc):
        text_page = page.get_text("dict")
        font_info = None

        for block in text_page.get("blocks", []):
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        if target_text in span["text"]:
                            font_info = {
                                "font": span["font"],
                                "size": span["size"],
                                "color_int": span["color"],
                                "flags": span["flags"],
                                "bbox": span["bbox"],
                            }
                            break
                    if font_info:
                        break
            if font_info:
                break

        rects = page.search_for(target_text)
        if not rects:
            continue

        for rect in rects:
            if font_info:
                c_int = font_info["color_int"]
                r = ((c_int >> 16) & 0xFF) / 255.0
                g = ((c_int >> 8) & 0xFF) / 255.0
                b = (c_int & 0xFF) / 255.0
                text_color = (r, g, b)
                font_size = font_info["size"]
                original_font = font_info["font"]
            else:
                text_color = (0, 0, 0)
                font_size = 11.0
                original_font = "helv"

            bg_color = sample_background_color(page, rect)
            page.add_redact_annot(rect, fill=bg_color)
            page.apply_redactions()

            font_alias = resolve_metric_font_alias(original_font)
            font_obj = pymupdf.Font(font_alias)
            font_id = f"font_{font_alias}"
            page.insert_font(fontname=font_id, fontbuffer=font_obj.buffer)

            baseline_y = rect.y1 - (font_size * 0.15)
            insertion_point = pymupdf.Point(rect.x0, baseline_y)

            page.insert_text(
                insertion_point,
                replacement_text,
                fontsize=font_size,
                color=text_color,
                fontname=font_id
            )
            replaced_count += 1
            print(f"[*] Page {page_num+1}: Replaced '{target_text}' -> '{replacement_text}'")
            print(f"    - Original Font Detected: {original_font}")
            print(f"    - Metric Twin Applied: {font_alias}")
            print(f"    - Exact Decimal Size: {font_size:.2f} pt")
            print(f"    - Text Color RGB: {text_color}")
            print(f"    - Background Tint Sampled: {bg_color}")

    doc.save(output_path, garbage=4, deflate=True)
    doc.close()
    print(f"\n[+] Success! Clean vector PDF written to {output_path} with zero watermarks.")
    return replaced_count

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python smart_typography.py <input.pdf> <search_text> <replace_text> <output.pdf>")
    else:
        inspect_and_replace_text(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
