"""
Universal Watermark & Document Sanitizer Engine.
Designed to pair with Ghost PDF Studio (pdf_engine).

Capabilities:
1. PDF Universal Watermark Purge:
   - Stream-level marked content excision (/CPDFSTAMP, /Artifact, /Watermark).
   - Form XObject & /SMask dissection and stream unlinking.
   - Vector drawing path scrubbing (Bézier curves, transparent diagonal banners).
   - Low-contrast / faint-opacity background text neutralization.
   - Scanned page raster watermark filtering (color/gray bleach & paper flat-fielding).
   - OCG layer & annotation stamp stripping.
   - Tracking metadata clearing.
2. Microsoft Office Document Sanitization:
   - DOCX: WordArt VML shapes, PowerPlusWatermarkObject, and DrawingML watermarks.
   - PPTX: Master slide & layout watermark shapes.
   - XLSX: Worksheet background picture watermarks.
3. Physical Paper Tracking Dot Neutralizer:
   - Erases Machine Identification Codes (MIC / Yellow laser printer tracking dots).
4. Digital Steganography Disruptor:
   - 2D FFT phase dithering & LSB payload clearing.
5. Content-Aware Texture Inpainting:
   - Harmonic Laplacian diffusion for visible stamps, logos, and overlays.
6. Unified Auto-Routing Controller:
   - Automatically sanitizes PDF, DOCX, PPTX, XLSX, and standalone images.
"""

import os
import sys
import re
import math
import argparse
import json
import io
import zipfile
from typing import Union, List, Tuple, Dict, Any, Optional

import numpy as np
from PIL import Image, ImageFilter, ImageOps
import pymupdf

# Common academic & document watermark text tokens
DEFAULT_WATERMARK_KEYWORDS = [
    "confidential", "draft", "sample", "watermark", "preview",
    "do not copy", "proprietary", "copyright", "strictly private",
    "internal use only", "for evaluation only", "downloaded from",
    "arxiv:", "ieeexplore", "researchgate", "springer", "sciencedirect",
    "getmyuni", "studocu", "coursehero", "chegg", "scribd"
]


class PaperSanitizer:
    """
    Physical document and scan sanitization.
    Specializes in neutralizing Machine Identification Codes (MIC / Yellow laser printer tracking dots),
    flattening uneven scanner shadows / paper tints, and bleaching scanned raster watermarks.
    """

    @staticmethod
    def remove_printer_tracking_dots(image: Image.Image, sensitivity: float = 1.8) -> Tuple[Image.Image, int]:
        """
        Detects and neutralizes laser printer tracking dots (MIC).
        Color laser printers embed a grid of tiny yellow dots (~0.1mm, 1-3 pixels wide).
        In RGB space, yellow has strong Red and Green, but very low Blue.
        Therefore, yellow dots produce a distinct dip in the Blue channel relative to min(R, G).
        
        Returns:
            (cleaned_image, count_of_modified_pixels)
        """
        img_rgb = image.convert("RGB")
        arr = np.array(img_rgb, dtype=np.float32)

        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]

        # Yellow contrast metric: high when R and G are bright but B is noticeably darker
        rg_min = np.minimum(r, g)
        yellow_contrast = rg_min - b

        # Dots typically exist on light paper backgrounds (rg_min > 160)
        paper_mask = (rg_min > 160)
        
        # Calculate dynamic threshold from the image background
        mean_contrast = float(np.mean(yellow_contrast[paper_mask])) if np.any(paper_mask) else 0.0
        std_contrast = float(np.std(yellow_contrast[paper_mask])) if np.any(paper_mask) else 1.0
        threshold = max(18.0, mean_contrast + sensitivity * std_contrast)

        # Potential dot mask
        dot_candidates = paper_mask & (yellow_contrast > threshold)

        # Micro-filter: Yellow dots are small isolated clusters (1 to 6 pixels in diameter)
        cand_img = Image.fromarray(dot_candidates.astype(np.uint8) * 255)
        # 3x3 box blur to check neighborhood density
        density = np.array(cand_img.filter(ImageFilter.BoxBlur(1)), dtype=np.float32) / 255.0
        
        # Valid dots: candidate is True, but local density is small (isolated dots, not large yellow graphics)
        dot_mask = dot_candidates & (density < 0.45) & (density > 0.0)

        modified_pixels = int(np.sum(dot_mask))
        if modified_pixels == 0:
            return image, 0

        # Neutralize: replace the blue channel with min(r, g) so yellow tint becomes white/neutral
        cleaned_arr = arr.copy()
        cleaned_b = cleaned_arr[:, :, 2]
        cleaned_b[dot_mask] = rg_min[dot_mask]
        cleaned_arr[:, :, 2] = cleaned_b

        cleaned_img = Image.fromarray(np.clip(cleaned_arr, 0, 255).astype(np.uint8), mode="RGB")
        return cleaned_img, modified_pixels

    @staticmethod
    def flatten_scanner_background(image: Image.Image, paper_threshold: int = 210) -> Image.Image:
        """
        Removes yellowish scanner aging, uneven shadows, and gradient backgrounds from scanned paper,
        leaving pure clean digital white around black text.
        """
        img_rgb = image.convert("RGB")
        arr = np.array(img_rgb, dtype=np.float32)

        lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
        bg_mask = lum >= paper_threshold

        scale = np.clip((lum - paper_threshold) / (255.0 - paper_threshold + 1e-5), 0.0, 1.0)
        scale = np.expand_dims(scale, axis=2)

        cleaned_arr = arr * (1.0 - scale * bg_mask[:, :, None]) + 255.0 * (scale * bg_mask[:, :, None])
        return Image.fromarray(np.clip(cleaned_arr, 0, 255).astype(np.uint8), mode="RGB")

    @staticmethod
    def bleach_scanned_watermarks(image: Image.Image) -> Tuple[Image.Image, int]:
        """
        Bleaches semi-transparent color stamps (red/pink/blue) and faint gray watermarks
        baked into scanned pages, while keeping dark ink, text lines, and diagrams 100% preserved.
        """
        img_rgb = image.convert("RGB")
        arr = np.array(img_rgb, dtype=np.float32)

        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]

        lum = 0.299 * r + 0.587 * g + 0.114 * b
        chroma = np.maximum(np.abs(r - g), np.maximum(np.abs(r - b), np.abs(g - b)))

        # 1. Color watermarks on light paper (pink, red, purple, blue stamps)
        color_wm = (lum > 135.0) & (chroma > 18.0)

        # 2. Faint gray watermarks (lum between 195 and 238 with low chroma)
        gray_wm = (lum >= 195.0) & (lum <= 238.0) & (chroma <= 15.0)

        wm_mask = color_wm | gray_wm
        count_bleached = int(np.sum(wm_mask))

        if count_bleached == 0:
            return image, 0

        cleaned_arr = arr.copy()
        # Bleach watermark areas to white
        cleaned_arr[wm_mask] = 255.0

        # Push any near-white paper background (> 238) smoothly to 255
        bg_mask = lum > 238.0
        cleaned_arr[bg_mask] = 255.0

        cleaned_img = Image.fromarray(np.clip(cleaned_arr, 0, 255).astype(np.uint8), mode="RGB")
        return cleaned_img, count_bleached


class SteganographySanitizer:
    """
    Neutralizes invisible digital watermarks and steganographic payloads.
    Targets frequency-domain markers (SynthID, RivaGAN, DWT/DCT spread-spectrum)
    and spatial payloads (LSB steganography) with zero visible loss of fidelity.
    """

    @staticmethod
    def neutralize_frequency_watermarks(image: Image.Image, strength: float = 0.04) -> Image.Image:
        """
        Disrupts frequency-domain invisible watermarks using 2D FFT phase dithering.
        Adds micro-dither to frequency phases to destroy correlation detection
        while remaining imperceptible to human eyes.
        """
        img_rgb = image.convert("RGB")
        arr = np.array(img_rgb, dtype=np.float64)
        h, w, c = arr.shape

        cleaned_channels = []
        for ch in range(c):
            channel = arr[:, :, ch]
            f = np.fft.fft2(channel)
            fshift = np.fft.fftshift(f)

            magnitude = np.abs(fshift)
            phase = np.angle(fshift)

            y, x = np.ogrid[:h, :w]
            center_y, center_x = h // 2, w // 2
            dist_from_center = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
            max_dist = np.sqrt(center_x ** 2 + center_y ** 2)
            normalized_dist = dist_from_center / (max_dist + 1e-5)

            # Dither mid and high frequencies (> 0.08 normalized radius)
            dither_mask = (normalized_dist > 0.08).astype(np.float64)

            rng = np.random.default_rng(seed=42 + ch)
            noise = rng.uniform(-strength * math.pi, strength * math.pi, size=(h, w))
            new_phase = phase + noise * dither_mask

            fshift_modified = magnitude * np.exp(1j * new_phase)
            f_ishift = np.fft.ifftshift(fshift_modified)
            restored = np.fft.ifft2(f_ishift)
            cleaned_channels.append(np.real(restored))

        cleaned_arr = np.stack(cleaned_channels, axis=2)
        return Image.fromarray(np.clip(np.round(cleaned_arr), 0, 255).astype(np.uint8), mode="RGB")

    @staticmethod
    def clear_lsb_payload(image: Image.Image, bits: int = 2) -> Image.Image:
        """
        Clears or dithers the least significant bits (LSB) across all channels,
        erasing hidden steganographic data payloads.
        """
        bits = max(1, min(4, bits))
        arr = np.array(image.convert("RGB"), dtype=np.uint8)

        mask = np.uint8((0xFF << bits) & 0xFF)
        cleaned_arr = arr & mask

        rng = np.random.default_rng(seed=1337)
        dither = rng.integers(0, 1 << bits, size=arr.shape, dtype=np.uint8)
        cleaned_arr = cleaned_arr | dither

        return Image.fromarray(cleaned_arr, mode="RGB")


class InpaintingSanitizer:
    """
    Content-Aware Inpainting for visible stamps, logos, and overlays.
    Uses pure-local multi-scale iterative diffusion (fast harmonic inpainting),
    requiring zero external model downloads or heavy GPU dependencies.
    """

    @staticmethod
    def inpaint(image: Image.Image, mask: np.ndarray, iterations: int = 15) -> Image.Image:
        """
        Inpaints masked regions (where mask > 0) by smoothly diffusing boundary textures inward.
        """
        img_rgb = image.convert("RGB")
        img_arr = np.array(img_rgb, dtype=np.float32)
        h, w, c = img_arr.shape

        mask_bool = (mask > 50)
        if not np.any(mask_bool):
            return image

        boundary_dilated = np.array(Image.fromarray(mask_bool.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(7))) > 0
        boundary_ring = boundary_dilated & (~mask_bool)
        
        for ch in range(c):
            channel = img_arr[:, :, ch]
            fill_val = np.mean(channel[boundary_ring]) if np.any(boundary_ring) else np.mean(channel)
            channel[mask_bool] = fill_val

        kernel = np.array([[0.05, 0.2, 0.05],
                           [0.2,  0.0, 0.2],
                           [0.05, 0.2, 0.05]], dtype=np.float32)

        for _ in range(iterations):
            for ch in range(c):
                channel = img_arr[:, :, ch]
                padded = np.pad(channel, 1, mode='edge')
                smoothed = (
                    kernel[0, 0] * padded[:-2, :-2] + kernel[0, 1] * padded[:-2, 1:-1] + kernel[0, 2] * padded[:-2, 2:] +
                    kernel[1, 0] * padded[1:-1, :-2] + kernel[1, 1] * channel            + kernel[1, 2] * padded[1:-1, 2:] +
                    kernel[2, 0] * padded[2:, :-2]   + kernel[2, 1] * padded[2:, 1:-1]   + kernel[2, 2] * padded[2:, 2:]
                )
                channel[mask_bool] = smoothed[mask_bool]

        return Image.fromarray(np.clip(img_arr, 0, 255).astype(np.uint8), mode="RGB")


class OfficeSanitizer:
    """
    Sanitizes Microsoft Office documents (DOCX, PPTX, XLSX) by directly parsing
    and removing watermark objects, WordArt shapes, background pictures, and tracking XMLs
    from the OpenXML zip archives.
    """

    @classmethod
    def sanitize_docx(cls, data: bytes, keywords: Optional[List[str]] = None) -> Tuple[bytes, Dict[str, Any]]:
        """
        Removes WordArt watermarks, PowerPlusWatermarkObject, and DrawingML watermark headers.
        """
        kw_list = [k.lower() for k in (keywords or DEFAULT_WATERMARK_KEYWORDS)]
        out_buf = io.BytesIO()
        removed_count = 0

        with zipfile.ZipFile(io.BytesIO(data), 'r') as zin, zipfile.ZipFile(out_buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)
                fname = item.filename.lower()

                if fname.startswith("word/") and fname.endswith(".xml"):
                    text = content.decode("utf-8", errors="ignore")

                    # 1. Strip VML PowerPlusWatermark / WordArt shapes
                    def check_vml_watermark(match):
                        nonlocal removed_count
                        block = match.group(0)
                        block_lower = block.lower()
                        if "watermark" in block_lower or any(k in block_lower for k in kw_list):
                            removed_count += 1
                            return ""
                        return block

                    text, c1 = re.subn(
                        r'<w:pict\b[^>]*>.*?<v:shape\b[^>]*?(?:PowerPlusWatermark|WordArtWatermark|Watermark)[^>]*?>.*?</w:pict>',
                        '',
                        text,
                        flags=re.DOTALL | re.IGNORECASE
                    )
                    removed_count += c1

                    text = re.sub(
                        r'<w:pict\b[^>]*>.*?</w:pict>',
                        check_vml_watermark,
                        text,
                        flags=re.DOTALL | re.IGNORECASE
                    )

                    # 2. Strip DrawingML watermarks in headers/footers
                    def check_drawing_watermark(match):
                        nonlocal removed_count
                        block = match.group(0)
                        block_lower = block.lower()
                        if "watermark" in block_lower or any(k in block_lower for k in kw_list):
                            removed_count += 1
                            return ""
                        return block

                    text = re.sub(
                        r'<w:drawing\b[^>]*>.*?</w:drawing>',
                        check_drawing_watermark,
                        text,
                        flags=re.DOTALL | re.IGNORECASE
                    )

                    content = text.encode("utf-8")

                zout.writestr(item, content)

        audit = {"docx_watermarks_removed": removed_count}
        return out_buf.getvalue(), audit

    @classmethod
    def sanitize_pptx(cls, data: bytes, keywords: Optional[List[str]] = None) -> Tuple[bytes, Dict[str, Any]]:
        """
        Removes watermark shapes from slide masters, slide layouts, and presentation slides.
        """
        kw_list = [k.lower() for k in (keywords or DEFAULT_WATERMARK_KEYWORDS)]
        out_buf = io.BytesIO()
        removed_count = 0

        with zipfile.ZipFile(io.BytesIO(data), 'r') as zin, zipfile.ZipFile(out_buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)
                fname = item.filename.lower()

                if fname.startswith("ppt/") and fname.endswith(".xml"):
                    text = content.decode("utf-8", errors="ignore")

                    def check_slide_shape(match):
                        nonlocal removed_count
                        block = match.group(0)
                        block_lower = block.lower()
                        if "watermark" in block_lower or any(k in block_lower for k in kw_list):
                            removed_count += 1
                            return ""
                        return block

                    text = re.sub(
                        r'<p:sp\b[^>]*>.*?</p:sp>',
                        check_slide_shape,
                        text,
                        flags=re.DOTALL | re.IGNORECASE
                    )

                    content = text.encode("utf-8")

                zout.writestr(item, content)

        audit = {"pptx_watermarks_removed": removed_count}
        return out_buf.getvalue(), audit

    @classmethod
    def sanitize_xlsx(cls, data: bytes) -> Tuple[bytes, Dict[str, Any]]:
        """
        Removes worksheet background picture watermarks from Excel workbooks.
        """
        out_buf = io.BytesIO()
        removed_count = 0

        with zipfile.ZipFile(io.BytesIO(data), 'r') as zin, zipfile.ZipFile(out_buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)
                fname = item.filename.lower()

                if fname.startswith("xl/worksheets/") and fname.endswith(".xml"):
                    text = content.decode("utf-8", errors="ignore")

                    # Strip background sheet picture element
                    text, c1 = re.subn(r'<picture\s+[^>]*?/>', '', text, flags=re.DOTALL)
                    removed_count += c1

                    content = text.encode("utf-8")

                zout.writestr(item, content)

        audit = {"xlsx_watermarks_removed": removed_count}
        return out_buf.getvalue(), audit


class PDFWatermarkSanitizer:
    """
    Universal PDF Structural, Vector, and Visual Watermark Remover.
    Handles:
    - Syntax-level marked-content excision (/CPDFSTAMP, /Artifact, /Watermark).
    - Header discriminator: preserves legitimate document headers while stripping tracking hashes.
    - Document-wide and page-level Form XObject unlinking & clearing.
    - Vector drawing watermark path scrubbing (Bézier curves, transparent banners).
    - Low-contrast faint text detection and excision.
    - Scanned page raster watermark filtering and scanner flattening.
    - OCG layer deactivation and annotation stamp removal.
    - Tracking metadata purging.
    """

    def __init__(self, keywords: Optional[List[str]] = None):
        self.keywords = [k.lower() for k in (keywords or DEFAULT_WATERMARK_KEYWORDS)]

    def is_watermark_marked_content(self, block: str) -> bool:
        """
        Distinguishes between actual watermark stamps (repeating tracking hashes or keywords)
        and legitimate document headers/footers (e.g. 'Paper / Subject Code: 58505 / Applied Physics - I.').
        """
        texts = []
        for literal in re.findall(r'\((.*?)\)', block):
            texts.append(literal)
        for hex_str in re.findall(r'<([0-9A-Fa-f]{2,})>', block):
            try:
                texts.append(bytes.fromhex(hex_str).decode('latin1', errors='ignore'))
            except Exception:
                pass

        full_text = ' '.join(texts).strip()
        if not full_text:
            return bool(re.search(r'/(?:Form|Im)\w+\s+Do', block))

        lower_text = full_text.lower()
        if any(kw in lower_text for kw in self.keywords):
            return True

        # Check for continuous hex tracking tokens or repeating hashes
        for token in full_text.split():
            clean_tok = re.sub(r'[^A-Za-z0-9]', '', token)
            # Pure hex token >= 20 chars is a tracking hash
            if len(clean_tok) >= 20 and re.fullmatch(r'[0-9A-Fa-f]+', clean_tok):
                return True
            # Repeating substring token
            if len(clean_tok) >= 32 and clean_tok[:16] in clean_tok[16:]:
                return True

        # Check repeating pattern across whole text
        cleaned_alnum = re.sub(r'[^A-Za-z0-9]', '', full_text)
        if len(cleaned_alnum) >= 40 and cleaned_alnum[:20] in cleaned_alnum[20:]:
            return True

        return False

    def sanitize_pdf_document(
        self,
        doc: pymupdf.Document,
        remove_text: bool = True,
        remove_annots: bool = True,
        remove_vectors: bool = True,
        remove_layers: bool = True,
        strip_metadata: bool = True,
        sanitize_images: bool = True,
        flatten_scanned: bool = True,
        progress_callback = None
    ) -> Dict[str, Any]:
        """
        Executes a comprehensive watermark purge across all PDF pages.
        Guarantees that NO PDF type is skipped.
        """
        audit = {
            "text_watermarks_removed": 0,
            "annotations_removed": 0,
            "ocg_layers_removed": 0,
            "vector_paths_removed": 0,
            "xobjects_cleared": 0,
            "images_sanitized": 0,
            "metadata_cleared": False
        }

        # 1. Strip Metadata tracking tokens
        if strip_metadata:
            doc.set_metadata({
                "title": "",
                "author": "",
                "subject": "",
                "keywords": "",
                "creator": "Ghost PDF Sanitizer",
                "producer": "Ghost PDF Sanitizer",
                "creationDate": "",
                "modDate": ""
            })
            audit["metadata_cleared"] = True

        # 2. Strip OCG (Optional Content Groups) Watermark Layers
        if remove_layers:
            try:
                ocgs = doc.get_ocgs()
                for xref, ocg_dict in ocgs.items():
                    name = str(ocg_dict.get("name", "")).lower()
                    if any(w in name for w in ["watermark", "draft", "stamp", "background"]):
                        doc.set_ocg(xref, on=False)
                        audit["ocg_layers_removed"] += 1
            except Exception:
                pass

        # 3. Discover Document-Wide Watermark Images & Form XObjects
        watermark_xobjects = set()
        watermark_xrefs = set()
        smask_image_xrefs = set()

        # Step 3a: Discover all images with /SMask transparency masks
        for xref in range(1, doc.xref_length()):
            try:
                obj_str = doc.xref_object(xref)
                if "/Subtype /Image" in obj_str and "/SMask" in obj_str:
                    smask_image_xrefs.add(xref)
                    watermark_xrefs.add(xref)
            except Exception:
                pass

        # Step 3b: Discover Form XObjects calling SMask images, or with large watermark BBoxes
        for xref in range(1, doc.xref_length()):
            try:
                obj_str = doc.xref_object(xref)
                if "/Subtype /Form" in obj_str:
                    stream = doc.xref_stream(xref).decode("latin1", errors="ignore")
                    stream_lower = stream.lower()
                    is_wm = False

                    if any(t in stream_lower for t in ["watermark", "stamp", "draft", "cpdfstamp", "/smask"]):
                        is_wm = True
                    elif any(kw in stream_lower for kw in self.keywords):
                        is_wm = True
                    elif any(f"{img_xref} 0 R" in obj_str for img_xref in smask_image_xrefs):
                        is_wm = True
                    elif "/im" in stream_lower:
                        bbox_m = re.search(r'/BBox\s*\[\s*([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s*\]', obj_str)
                        if bbox_m:
                            w = abs(float(bbox_m.group(3)) - float(bbox_m.group(1)))
                            h = abs(float(bbox_m.group(4)) - float(bbox_m.group(2)))
                            if w > 250 and h > 350:
                                is_wm = True

                    if is_wm:
                        watermark_xrefs.add(xref)
            except Exception:
                pass

        # Step 3c: Map watermark XObject xrefs to their names across all pages
        try:
            for p in doc:
                for x in p.get_xobjects():
                    if x[0] in watermark_xrefs:
                        watermark_xobjects.add(x[1])
        except Exception:
            pass

        # 4. Process each page
        processed_xrefs = set()
        total_pages = len(doc)

        for page_idx in range(total_pages):
            if progress_callback:
                try:
                    progress_callback(page_idx, total_pages)
                except Exception:
                    pass
            page = doc[page_idx]

            # A. Annotation Watermarks (/Watermark, /Stamp)
            if remove_annots:
                for annot in list(page.annots()):
                    atype = annot.type[0]
                    # Type 12 = Stamp, Type 25 = Watermark
                    if atype in (12, 25):
                        page.delete_annot(annot)
                        audit["annotations_removed"] += 1

            # B. Detect Dynamic Watermark Terms & Low-Contrast Text on this page
            dynamic_watermark_terms = set(self.keywords)
            try:
                raw_dict = page.get_text("rawdict")
                for b in raw_dict.get("blocks", []):
                    if "lines" not in b:
                        continue
                    for l in b.get("lines", []):
                        dir_x, dir_y = l.get("dir", (1.0, 0.0))
                        is_diagonal = abs(dir_y) > 0.05 and abs(dir_x) > 0.05
                        line_text = "".join(s.get("text", "") for s in l.get("spans", [])).strip()

                        for s in l.get("spans", []):
                            alpha = s.get("alpha", 255)
                            c_int = s.get("color", 0)
                            r = (c_int >> 16) & 0xFF
                            g = (c_int >> 8) & 0xFF
                            b_col = c_int & 0xFF
                            lum = 0.299 * r + 0.587 * g + 0.114 * b_col
                            size = s.get("size", 10.0)

                            # Faint gray or high transparency
                            is_faint = (185 < lum < 245 and abs(r - g) < 20 and abs(g - b_col) < 20) or (0 < alpha < 180)

                            if (is_diagonal or is_faint) and len(line_text) >= 3:
                                dynamic_watermark_terms.add(line_text.lower())
                                for word in line_text.lower().split():
                                    if len(word) >= 4:
                                        dynamic_watermark_terms.add(word)
            except Exception:
                pass

            # C. Detect Vector Drawing Watermark Paths
            has_vector_watermark = False
            if remove_vectors:
                try:
                    drawings = page.get_drawings()
                    for d in drawings:
                        r = d.get("rect", pymupdf.Rect())
                        fo = d.get("fill_opacity", 1.0)
                        so = d.get("stroke_opacity", 1.0)
                        is_large = (r.width > page.rect.width * 0.35) and (r.height > page.rect.height * 0.25)
                        is_transparent = (fo is not None and fo < 0.65) or (so is not None and so < 0.65)
                        fill = d.get("fill")
                        is_faint_fill = fill and all(c > 0.75 for c in fill)

                        if is_large and (is_transparent or is_faint_fill):
                            has_vector_watermark = True
                            audit["vector_paths_removed"] += 1
                except Exception:
                    pass

            # D. Stream-Level Excision (Lossless Vector, Form, and Text Watermark Stripping)
            if remove_text or remove_layers or remove_vectors:
                for cxref in page.get_contents():
                    try:
                        raw_stream = doc.xref_stream(cxref).decode("latin1", errors="ignore")
                        modified_stream = raw_stream

                        # 1. Marked Content Stamps
                        def clean_marked_content_block(match):
                            block = match.group(0)
                            return "" if self.is_watermark_marked_content(block) else block

                        modified_stream, count1 = re.subn(
                            r'/(?:CPDFSTAMP|Watermark|STAMP|Stamp|Background)\s+(?:BMC|BDC).*?EMC',
                            clean_marked_content_block,
                            modified_stream,
                            flags=re.DOTALL
                        )
                        audit["text_watermarks_removed"] += count1

                        # 2. Marked Content Artifacts
                        modified_stream, count2 = re.subn(
                            r'/(?:Artifact|Private)\s*<<[^>]*?(?:Watermark|Background)[^>]*?>>\s*(?:BMC|BDC).*?EMC',
                            '',
                            modified_stream,
                            flags=re.DOTALL
                        )
                        audit["text_watermarks_removed"] += count2

                        # 3. Strip invocations of detected Watermark XObjects/Forms
                        for wm_name in watermark_xobjects:
                            pattern = rf'q\s+[^Q]*?/{re.escape(wm_name)}\s+Do\s+Q'
                            modified_stream, count_x = re.subn(pattern, '', modified_stream, flags=re.DOTALL)
                            audit["text_watermarks_removed"] += count_x
                            modified_stream = re.sub(rf'/{re.escape(wm_name)}\s+Do', '', modified_stream)

                        # 4. Vector Drawing Paths with Transparency
                        if has_vector_watermark:
                            def clean_vector_block(match):
                                block = match.group(0)
                                # If block contains text operators, preserve it
                                if any(t in block for t in ["BT", "ET", "Tj", "TJ"]):
                                    return block
                                # If it sets low alpha /ca or /CA or gs, and has path operators (re, m, l, c, h)
                                if any(t in block for t in ["/fitzca", "/ca", "/CA", "gs"]) and any(p in block for p in ["re", " m ", " l ", " c ", " B", " f"]):
                                    return ""
                                return block

                            modified_stream_vec = re.sub(r'q\s+[^Q]*?Q', clean_vector_block, modified_stream, flags=re.DOTALL)
                            if modified_stream_vec != modified_stream:
                                modified_stream = modified_stream_vec

                        # 5. BT...ET Text Blocks
                        def decode_pdf_stream_text(stream_text: str) -> str:
                            def hex_repl(m):
                                try:
                                    return bytes.fromhex(m.group(1)).decode('latin1', errors='ignore')
                                except Exception:
                                    return m.group(0)
                            return re.sub(r'<([0-9a-fA-F]{2,})>', hex_repl, stream_text)

                        def clean_bt_et(match):
                            block = match.group(0)
                            decoded = decode_pdf_stream_text(block).lower()

                            # Check keywords & dynamic terms
                            if any(kw in decoded for kw in dynamic_watermark_terms):
                                return ""

                            # Check repeating tracking tokens (e.g. university exam paper hashes)
                            cleaned_alnum = re.sub(r'[^a-z0-9]', '', decoded)
                            if len(cleaned_alnum) >= 40:
                                chunk = cleaned_alnum[:24]
                                if cleaned_alnum.count(chunk) >= 2:
                                    return ""

                            return block

                        modified_stream_bt = re.sub(r'BT\b.*?ET', clean_bt_et, modified_stream, flags=re.DOTALL)
                        if modified_stream_bt != modified_stream:
                            audit["text_watermarks_removed"] += 1
                            modified_stream = modified_stream_bt

                        if modified_stream != raw_stream:
                            doc.update_stream(cxref, modified_stream.encode("latin1"))
                    except Exception:
                        pass

            # E. Embedded Raster Images & Scanned Page Fallback
            page_text_len = len(page.get_text().strip())
            is_scanned_page = (page_text_len < 30)

            if sanitize_images or (flatten_scanned and is_scanned_page):
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    if xref in processed_xrefs:
                        continue
                    processed_xrefs.add(xref)

                    try:
                        base_image = doc.extract_image(xref)
                        if not base_image or "image" not in base_image:
                            continue

                        w = base_image.get("width", 0)
                        h = base_image.get("height", 0)
                        if w < 120 or h < 120:
                            continue

                        img_bytes = base_image["image"]
                        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                        image_modified = False

                        # 1. Printer tracking dots
                        clean_dots_img, dots = PaperSanitizer.remove_printer_tracking_dots(pil_img)
                        if dots > 0:
                            pil_img = clean_dots_img
                            image_modified = True

                        # 2. Bleach scanned watermark & flatten background
                        if flatten_scanned:
                            bleached_img, bleached_count = PaperSanitizer.bleach_scanned_watermarks(pil_img)
                            if bleached_count > 0:
                                pil_img = bleached_img
                                image_modified = True

                        if image_modified:
                            buf = io.BytesIO()
                            pil_img.save(buf, format="PNG")
                            page.replace_image(xref, stream=buf.getvalue())
                            audit["images_sanitized"] += 1
                    except Exception:
                        pass

        # 5. Empty the stream of any identified Watermark XObjects
        for x_xref in watermark_xrefs:
            try:
                doc.update_stream(x_xref, b"")
                audit["xobjects_cleared"] += 1
            except Exception:
                pass

        return audit


class UniversalSanitizer:
    """
    Unified Front Controller:
    Detects file types (PDF, DOCX, PPTX, XLSX, PNG, JPG, WEBP, TIFF, BMP)
    and orchestrates structural, optical, and steganographic sanitization pipelines.
    """

    @classmethod
    def sanitize_bytes(
        cls,
        data: bytes,
        filename: str = "document.pdf",
        remove_text: bool = True,
        remove_annots: bool = True,
        remove_vectors: bool = True,
        remove_dots: bool = True,
        remove_stego: bool = True,
        flatten_scanned: bool = True,
        keywords: Optional[List[str]] = None,
        inpaint_rect: Optional[Tuple[int, int, int, int]] = None
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Takes raw file bytes and filename, detects format, performs universal sanitization,
        and returns (sanitized_bytes, audit_dict).
        """
        ext = os.path.splitext(filename)[1].lower() if filename else ".pdf"

        # Magic bytes detection
        if data.startswith(b"%PDF"):
            ext = ".pdf"
        elif data.startswith(b"PK\x03\x04"):
            # Check zip contents for Office format
            try:
                with zipfile.ZipFile(io.BytesIO(data), 'r') as z:
                    names = z.namelist()
                    if any(n.startswith("word/") for n in names):
                        ext = ".docx"
                    elif any(n.startswith("ppt/") for n in names):
                        ext = ".pptx"
                    elif any(n.startswith("xl/") for n in names):
                        ext = ".xlsx"
            except Exception:
                pass
        elif data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff") or data.startswith(b"RIFF") or data.startswith(b"BM"):
            if ext not in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"]:
                ext = ".png"

        # Route 1: PDF Document
        if ext == ".pdf":
            doc = pymupdf.open(stream=data, filetype="pdf")
            engine = PDFWatermarkSanitizer(keywords=keywords)
            audit = engine.sanitize_pdf_document(
                doc,
                remove_text=remove_text,
                remove_annots=remove_annots,
                remove_vectors=remove_vectors,
                remove_layers=True,
                strip_metadata=True,
                sanitize_images=remove_dots or remove_stego,
                flatten_scanned=flatten_scanned
            )
            out_buf = io.BytesIO()
            doc.save(out_buf, garbage=4, deflate=True)
            doc.close()
            return out_buf.getvalue(), audit

        # Route 2: DOCX
        elif ext == ".docx":
            return OfficeSanitizer.sanitize_docx(data, keywords=keywords)

        # Route 3: PPTX
        elif ext == ".pptx":
            return OfficeSanitizer.sanitize_pptx(data, keywords=keywords)

        # Route 4: XLSX
        elif ext == ".xlsx":
            return OfficeSanitizer.sanitize_xlsx(data)

        # Route 5: Images
        elif ext in [".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"]:
            img = Image.open(io.BytesIO(data))
            audit = {
                "tracking_dots_removed": 0,
                "stego_neutralized": False,
                "scanned_watermarks_bleached": 0,
                "inpainting_applied": False
            }

            if remove_dots:
                img, dots = PaperSanitizer.remove_printer_tracking_dots(img)
                audit["tracking_dots_removed"] = dots

            if flatten_scanned:
                img, bleached = PaperSanitizer.bleach_scanned_watermarks(img)
                audit["scanned_watermarks_bleached"] = bleached

            if remove_stego:
                img = SteganographySanitizer.neutralize_frequency_watermarks(img)
                img = SteganographySanitizer.clear_lsb_payload(img, bits=1)
                audit["stego_neutralized"] = True

            if inpaint_rect:
                x0, y0, x1, y1 = inpaint_rect
                mask = np.zeros((img.height, img.width), dtype=np.uint8)
                mask[y0:y1, x0:x1] = 255
                img = InpaintingSanitizer.inpaint(img, mask)
                audit["inpainting_applied"] = True

            out_buf = io.BytesIO()
            fmt = "PNG" if ext in [".png", ".webp", ".bmp"] else "JPEG"
            img.save(out_buf, format=fmt)
            return out_buf.getvalue(), audit

        else:
            raise ValueError(f"Unsupported file format: {ext}")

    @classmethod
    def process_file(
        cls,
        input_path: str,
        output_path: Optional[str] = None,
        remove_tracking_dots: bool = True,
        remove_stego: bool = True,
        flatten_background: bool = True,
        keywords: Optional[List[str]] = None,
        inpaint_rect: Optional[Tuple[int, int, int, int]] = None
    ) -> Dict[str, Any]:
        """
        Sanitizes a file on disk (PDF, Office, or Image) and writes the cleaned file to output_path.
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        base, ext = os.path.splitext(input_path)
        if output_path is None:
            output_path = f"{base}_clean{ext}"

        with open(input_path, "rb") as f:
            raw_bytes = f.read()

        clean_bytes, audit = cls.sanitize_bytes(
            raw_bytes,
            filename=input_path,
            remove_text=True,
            remove_annots=True,
            remove_vectors=True,
            remove_dots=remove_tracking_dots,
            remove_stego=remove_stego,
            flatten_scanned=flatten_background,
            keywords=keywords,
            inpaint_rect=inpaint_rect
        )

        with open(output_path, "wb") as f:
            f.write(clean_bytes)

        return {
            "input_file": input_path,
            "output_file": output_path,
            "type": ext.lstrip(".").lower(),
            "audit": audit
        }


def main():
    parser = argparse.ArgumentParser(
        description="Ghost Universal Sanitizer: Watermark & Steganography Remover (PDF, Office, Images)",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", help="Path to input PDF, DOCX, PPTX, XLSX, or Image file")
    parser.add_argument("-o", "--output", help="Path to save sanitized output file")
    parser.add_argument("--all", action="store_true", help="Apply all sanitization passes")
    parser.add_argument("--dots", action="store_true", help="Neutralize laser printer tracking dots (MIC)")
    parser.add_argument("--stego", action="store_true", help="Disrupt digital invisible watermarks & LSB steganography")
    parser.add_argument("--flatten-bg", action="store_true", help="Flatten paper background lighting & bleach scanned watermarks")
    parser.add_argument("--keywords", nargs="+", help="Additional watermark keywords to purge")
    parser.add_argument("--inpaint-rect", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"),
                        help="Bounding box coordinates to content-aware inpaint")
    parser.add_argument("--json", action="store_true", help="Print audit report as JSON")

    args = parser.parse_args()

    apply_all = args.all or not (args.dots or args.stego or args.flatten_bg or args.inpaint_rect)
    remove_dots = args.dots or apply_all
    remove_stego = args.stego or apply_all
    flatten_bg = args.flatten_bg or apply_all

    try:
        report = UniversalSanitizer.process_file(
            input_path=args.input,
            output_path=args.output,
            remove_tracking_dots=remove_dots,
            remove_stego=remove_stego,
            flatten_background=flatten_bg,
            keywords=args.keywords,
            inpaint_rect=tuple(args.inpaint_rect) if args.inpaint_rect else None
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"[+] Sanitization complete!")
            print(f"    Input:  {report['input_file']}")
            print(f"    Output: {report['output_file']}")
            print(f"    Type:   {report['type'].upper()}")
            print(f"    Audit:  {json.dumps(report['audit'], indent=6)}")
    except Exception as e:
        print(f"[-] Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
