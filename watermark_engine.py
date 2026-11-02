"""
Universal Watermark & Steganography Sanitizer Engine.
Designed to pair with Ghost PDF (pdf_engine).

Capabilities:
1. PDF Structural & Text Watermark Stripping (Lossless vector & text redaction).
2. Physical Paper Tracking Dot Neutralizer (Erases Machine Identification Codes / Yellow Dots from color scans).
3. Digital Steganography & Invisible Mark Disruptor (2D FFT phase dithering & LSB payload clearing).
4. Visible Logo & Stamp Content-Aware Inpainting (Pure local NumPy/Pillow texture diffusion).
5. Comprehensive Audit Evidence Provenance.
"""

import os
import sys
import re
import math
import argparse
import json
import io
from typing import Union, List, Tuple, Dict, Any, Optional

import numpy as np
from PIL import Image, ImageFilter, ImageOps
import pymupdf

# Common academic & document watermark text tokens
DEFAULT_WATERMARK_KEYWORDS = [
    "confidential", "draft", "sample", "watermark", "preview",
    "do not copy", "proprietary", "copyright", "strictly private",
    "internal use only", "for evaluation only", "downloaded from",
    "arxiv:", "ieeexplore", "researchgate", "springer", "sciencedirect"
]


class PaperSanitizer:
    """
    Physical document and scan sanitization.
    Specializes in neutralizing Machine Identification Codes (MIC / Yellow laser printer tracking dots)
    and flattening uneven scanner shadows / paper tints.
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

        # Dots typically exist on light paper backgrounds (rg_min > 180)
        paper_mask = (rg_min > 160)
        
        # Calculate dynamic threshold from the image background
        mean_contrast = np.mean(yellow_contrast[paper_mask]) if np.any(paper_mask) else 0.0
        std_contrast = np.std(yellow_contrast[paper_mask]) if np.any(paper_mask) else 1.0
        threshold = max(18.0, mean_contrast + sensitivity * std_contrast)

        # Potential dot mask
        dot_candidates = paper_mask & (yellow_contrast > threshold)

        # Micro-filter: Yellow dots are small isolated clusters (1 to 6 pixels in diameter).
        # We perform local neighborhood check to avoid touching large legitimate yellow artwork.
        dot_mask = np.zeros_like(dot_candidates, dtype=bool)
        h, w = dot_candidates.shape

        # Use 5x5 window density: dots have low local density (< 12 pixels in a 5x5 area)
        from PIL import Image as PilImg
        cand_img = PilImg.fromarray(dot_candidates.astype(np.uint8) * 255)
        # 3x3 box blur to check neighborhood density
        density = np.array(cand_img.filter(ImageFilter.BoxBlur(1)), dtype=np.float32) / 255.0
        
        # Valid dots: candidate is True, but local density is small (isolated dots, not large yellow graphics)
        dot_mask = dot_candidates & (density < 0.45) & (density > 0.0)

        modified_pixels = int(np.sum(dot_mask))
        if modified_pixels == 0:
            return image, 0

        # Neutralize: replace the blue channel with min(r, g) so yellow tint becomes white/neutral
        cleaned_arr = arr.copy()
        # Smoothly interpolate blue channel to match surrounding neighborhood
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

        # Grayscale luminance
        lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]

        # Background mask (light areas)
        bg_mask = lum >= paper_threshold

        # Push near-white background smoothly to pure 255
        scale = np.clip((lum - paper_threshold) / (255.0 - paper_threshold + 1e-5), 0.0, 1.0)
        scale = np.expand_dims(scale, axis=2)

        cleaned_arr = arr * (1.0 - scale * bg_mask[:, :, None]) + 255.0 * (scale * bg_mask[:, :, None])
        return Image.fromarray(np.clip(cleaned_arr, 0, 255).astype(np.uint8), mode="RGB")


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
        Invisible watermarks rely on precise phase coherence in mid-to-high frequency bands.
        Adding micro-dither to frequency phases destroys the watermark correlation detector
        while remaining completely imperceptible to human eyes.
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

            # High-pass / mid-band mask: leave DC and low frequencies completely untouched
            y, x = np.ogrid[:h, :w]
            center_y, center_x = h // 2, w // 2
            dist_from_center = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
            max_dist = np.sqrt(center_x ** 2 + center_y ** 2)
            normalized_dist = dist_from_center / (max_dist + 1e-5)

            # Dither target: mid and high frequencies (> 0.08 normalized radius)
            dither_mask = (normalized_dist > 0.08).astype(np.float64)

            # Deterministic pseudo-random micro phase shift
            rng = np.random.default_rng(seed=42 + ch)
            noise = rng.uniform(-strength * math.pi, strength * math.pi, size=(h, w))
            new_phase = phase + noise * dither_mask

            # Reconstruct complex spectrum
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
        erasing hidden steganographic data payloads (StegHide, OpenStego, LSB injection).
        """
        bits = max(1, min(4, bits))
        arr = np.array(image.convert("RGB"), dtype=np.uint8)

        mask = np.uint8((0xFF << bits) & 0xFF)
        # Clears lower bits
        cleaned_arr = arr & mask

        # Add subtle randomized rounding to prevent flat quantization bands
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
        
        Args:
            image: PIL Image
            mask: 2D uint8 numpy array with shape (H, W), where 255 = watermark, 0 = keep
            iterations: Number of diffusion passes
        """
        img_rgb = image.convert("RGB")
        img_arr = np.array(img_rgb, dtype=np.float32)
        h, w, c = img_arr.shape

        mask_bool = (mask > 50)
        if not np.any(mask_bool):
            return image

        # Initialize hole with boundary mean color
        boundary_dilated = np.array(Image.fromarray(mask_bool.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(7))) > 0
        boundary_ring = boundary_dilated & (~mask_bool)
        
        for ch in range(c):
            channel = img_arr[:, :, ch]
            fill_val = np.mean(channel[boundary_ring]) if np.any(boundary_ring) else np.mean(channel)
            channel[mask_bool] = fill_val

        # Iterative Laplace-style diffusion inside the mask
        kernel = np.array([[0.05, 0.2, 0.05],
                           [0.2,  0.0, 0.2],
                           [0.05, 0.2, 0.05]], dtype=np.float32)

        for _ in range(iterations):
            for ch in range(c):
                channel = img_arr[:, :, ch]
                # Convolution with boundary reflection
                padded = np.pad(channel, 1, mode='edge')
                smoothed = (
                    kernel[0, 0] * padded[:-2, :-2] + kernel[0, 1] * padded[:-2, 1:-1] + kernel[0, 2] * padded[:-2, 2:] +
                    kernel[1, 0] * padded[1:-1, :-2] + kernel[1, 1] * channel            + kernel[1, 2] * padded[1:-1, 2:] +
                    kernel[2, 0] * padded[2:, :-2]   + kernel[2, 1] * padded[2:, 1:-1]   + kernel[2, 2] * padded[2:, 2:]
                )
                channel[mask_bool] = smoothed[mask_bool]

        return Image.fromarray(np.clip(img_arr, 0, 255).astype(np.uint8), mode="RGB")


class PDFWatermarkSanitizer:
    """
    Deep PDF structural and visual watermark remover.
    Handles vector objects, diagonal text, annotations, OCG layers, and metadata.
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
            # Long pure hex token >= 20 characters is a tracking hash
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
        remove_layers: bool = True,
        strip_metadata: bool = True,
        sanitize_images: bool = False,
        progress_callback = None
    ) -> Dict[str, Any]:
        """
        Executes a comprehensive watermark purge across all PDF pages.
        Returns detailed audit metrics.
        """
        audit = {
            "text_watermarks_removed": 0,
            "annotations_removed": 0,
            "ocg_layers_removed": 0,
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
                        # Disable/remove the layer
                        doc.set_ocg(xref, on=False)
                        audit["ocg_layers_removed"] += 1
            except Exception:
                pass

        # 3. Discover Document-Wide Watermark XObjects (e.g. semi-transparent Form4/Im1 overlays)
        watermark_xobjects = set()
        watermark_xrefs = set()
        try:
            for p in doc:
                for x in p.get_xobjects():
                    x_xref, x_name = x[0], x[1]
                    obj_str = doc.xref_object(x_xref)
                    if "/SMask" in obj_str:
                        watermark_xobjects.add(x_name)
                        watermark_xrefs.add(x_xref)
                    elif "/Subtype /Form" in obj_str:
                        form_stream = doc.xref_stream(x_xref).decode("latin1", errors="ignore")
                        if any(t in form_stream for t in ["/Im", "/gs", "/CPDFSTAMP", "Watermark", "Stamp", "watermark", "stamp"]):
                            watermark_xobjects.add(x_name)
                            watermark_xrefs.add(x_xref)
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

            # B. Stream-Level Watermark Excision (Lossless Text/Watermark Stripping)
            # Removes watermark operators directly from the PDF content stream so that NO
            # white redaction rectangles are ever drawn over real document text or formulas!
            if remove_text or remove_layers:
                # Detect any diagonal or semi-transparent watermark strings on this page
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
                                if (is_diagonal or (0 < alpha < 180)) and len(line_text) >= 3:
                                    dynamic_watermark_terms.add(line_text.lower())
                except Exception:
                    pass

                for cxref in page.get_contents():
                    try:
                        raw_stream = doc.xref_stream(cxref).decode("latin1", errors="ignore")
                        modified_stream = raw_stream

                        # 1. Selectively strip marked content watermark stamps without removing legitimate headers
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

                        # 2. Strip marked content artifacts with /Watermark or /Background subtype
                        modified_stream, count2 = re.subn(
                            r'/(?:Artifact|Private)\s*<<[^>]*?(?:Watermark|Background)[^>]*?>>\s*(?:BMC|BDC).*?EMC',
                            '',
                            modified_stream,
                            flags=re.DOTALL
                        )
                        audit["text_watermarks_removed"] += count2

                        # 3. Strip invocations of detected watermark XObjects/Forms
                        for wm_name in watermark_xobjects:
                            pattern = rf'q\s+[^Q]*?/{re.escape(wm_name)}\s+Do\s+Q'
                            modified_stream, count_x = re.subn(pattern, '', modified_stream, flags=re.DOTALL)
                            audit["text_watermarks_removed"] += count_x
                            modified_stream = re.sub(rf'/{re.escape(wm_name)}\s+Do', '', modified_stream)

                        # 4. Strip BT...ET text blocks containing watermark keywords or repeating tracking hashes
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
                            # Check watermark keywords & dynamic terms
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

        # 5. Empty the stream of any identified watermark XObjects
        for x_xref in watermark_xrefs:
            try:
                doc.update_stream(x_xref, b"")
            except Exception:
                pass

            # C. Targeted Keyword Quads (Only for any remaining non-stream annotations)
            if remove_text:
                has_quad_redactions = False
                for kw in self.keywords:
                    quad_matches = page.search_for(kw, quads=True)
                    for q in quad_matches:
                        page.add_redact_annot(quad=q, fill=(1, 1, 1))
                        audit["text_watermarks_removed"] += 1
                        has_quad_redactions = True
                if has_quad_redactions:
                    page.apply_redactions()

            # D. Embedded Images Sanitization (Deduplicated across pages, skipped for small icons)
            if sanitize_images:
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
                        # Skip tiny icons, bullets, and glyphs
                        if w < 150 or h < 150:
                            continue
                        
                        img_bytes = base_image["image"]
                        pil_img = Image.open(io.BytesIO(img_bytes))

                        # Clean printer tracking dots if present
                        clean_img, dots_cleaned = PaperSanitizer.remove_printer_tracking_dots(pil_img)
                        if dots_cleaned > 0:
                            buf = io.BytesIO()
                            clean_img.save(buf, format="PNG")
                            page.replace_image(xref, stream=buf.getvalue())
                            audit["images_sanitized"] += 1
                    except Exception:
                        pass

        return audit


class UniversalSanitizer:
    """
    Unified Front Controller:
    Detects file types (PDF vs Image) and orchestrates structural, optical,
    and steganographic sanitization pipelines.
    """

    @classmethod
    def process_file(
        cls,
        input_path: str,
        output_path: Optional[str] = None,
        remove_tracking_dots: bool = True,
        remove_stego: bool = True,
        flatten_background: bool = False,
        keywords: Optional[List[str]] = None,
        inpaint_rect: Optional[Tuple[int, int, int, int]] = None
    ) -> Dict[str, Any]:
        """
        Sanitizes a PDF or image file from disk and writes the cleaned file to output_path.
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        ext = os.path.splitext(input_path)[1].lower()
        if output_path is None:
            base, ext_out = os.path.splitext(input_path)
            output_path = f"{base}_clean{ext_out}"

        report = {
            "input_file": input_path,
            "output_file": output_path,
            "type": "unknown",
            "audit": {}
        }

        # Route 1: PDF Document
        if ext == ".pdf":
            report["type"] = "pdf"
            doc = pymupdf.open(input_path)
            engine = PDFWatermarkSanitizer(keywords=keywords)
            audit = engine.sanitize_pdf_document(
                doc,
                remove_text=True,
                remove_annots=True,
                remove_layers=True,
                strip_metadata=True,
                sanitize_images=remove_tracking_dots or remove_stego
            )
            # Save PDF cleanly with deflated streams
            doc.save(output_path, garbage=4, deflate=True)
            doc.close()
            report["audit"] = audit

        # Route 2: Image / Scan (JPG, PNG, WEBP, TIFF)
        elif ext in [".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"]:
            report["type"] = "image"
            img = Image.open(input_path)
            audit = {"tracking_dots_removed": 0, "stego_neutralized": False, "inpainting_applied": False}

            # 1. Printer tracking dots
            if remove_tracking_dots:
                img, dots = PaperSanitizer.remove_printer_tracking_dots(img)
                audit["tracking_dots_removed"] = dots

            # 2. Scanner background flattening
            if flatten_background:
                img = PaperSanitizer.flatten_scanner_background(img)
                audit["background_flattened"] = True

            # 3. Frequency steganography & LSB
            if remove_stego:
                img = SteganographySanitizer.neutralize_frequency_watermarks(img)
                img = SteganographySanitizer.clear_lsb_payload(img, bits=1)
                audit["stego_neutralized"] = True

            # 4. Inpainting rectangle (if specified)
            if inpaint_rect:
                x0, y0, x1, y1 = inpaint_rect
                mask = np.zeros((img.height, img.width), dtype=np.uint8)
                mask[y0:y1, x0:x1] = 255
                img = InpaintingSanitizer.inpaint(img, mask)
                audit["inpainting_applied"] = True

            img.save(output_path)
            report["audit"] = audit

        else:
            raise ValueError(f"Unsupported file format: {ext}")

        return report


def main():
    parser = argparse.ArgumentParser(
        description="Ghost Sanitizer: Universal Watermark & Document Steganography Remover",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", help="Path to input PDF or Image file")
    parser.add_argument("-o", "--output", help="Path to save sanitized output file")
    parser.add_argument("--all", action="store_true", help="Apply all sanitization passes")
    parser.add_argument("--dots", action="store_true", help="Neutralize laser printer tracking dots (MIC)")
    parser.add_argument("--stego", action="store_true", help="Disrupt digital invisible watermarks & LSB steganography")
    parser.add_argument("--flatten-bg", action="store_true", help="Flatten paper background lighting & aging")
    parser.add_argument("--keywords", nargs="+", help="Additional watermark keywords to purge from PDF")
    parser.add_argument("--inpaint-rect", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"),
                        help="Bounding box coordinates to content-aware inpaint")
    parser.add_argument("--json", action="store_true", help="Print audit report as JSON")

    args = parser.parse_args()

    # Default to all if no specific flags provided
    apply_all = args.all or not (args.dots or args.stego or args.flatten_bg or args.inpaint_rect)
    remove_dots = args.dots or apply_all
    remove_stego = args.stego or apply_all

    try:
        report = UniversalSanitizer.process_file(
            input_path=args.input,
            output_path=args.output,
            remove_tracking_dots=remove_dots,
            remove_stego=remove_stego,
            flatten_background=args.flatten_bg,
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
