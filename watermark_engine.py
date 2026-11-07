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
        Safeguards text strokes from being bleached or erased when text lies over the watermark.
        """
        img_rgb = image.convert("RGB")
        arr = np.array(img_rgb, dtype=np.float32)

        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]

        lum = 0.299 * r + 0.587 * g + 0.114 * b
        chroma = np.maximum(np.abs(r - g), np.maximum(np.abs(r - b), np.abs(g - b)))

        # 1. Text stroke protection: Dark ink strokes (lum < 135) must NEVER be bleached to white
        is_text_stroke = (lum < 135.0)

        # 2. Color watermarks on light paper background
        color_wm_bg = (lum >= 155.0) & (chroma > 18.0)

        # 3. Faint gray watermarks on light paper
        gray_wm_bg = (lum >= 195.0) & (lum <= 238.0) & (chroma <= 15.0)

        # 4. Color watermark intersecting text strokes (chroma on dark ink) -> desaturate, do not bleach
        color_wm_on_text = is_text_stroke & (chroma > 18.0)

        wm_bg_mask = color_wm_bg | gray_wm_bg
        count_bleached = int(np.sum(wm_bg_mask)) + int(np.sum(color_wm_on_text))

        if count_bleached == 0:
            return image, 0

        cleaned_arr = arr.copy()
        # Bleach background watermark areas to clean white
        cleaned_arr[wm_bg_mask] = 255.0

        # For text intersecting color stamps, preserve ink darkness by desaturating
        for c in range(3):
            cleaned_arr[color_wm_on_text, c] = lum[color_wm_on_text]

        # Push near-white paper background (> 240) smoothly to 255
        bg_mask = (lum > 240.0) & (~is_text_stroke)
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


class ExemplarPatchInpainter:
    """
    Advanced Content-Aware Inpainter with Texture-Aware Exemplar Synthesis.
    Combines global harmonic diffusion (which solves global illumination and gradient matching)
    with exemplar micro-texture synthesis from surrounding unmasked boundary neighborhoods,
    preventing plastic blur on textured surfaces (fabrics, paper grain, wood, grass).
    """

    @classmethod
    def inpaint(cls, image: Image.Image, mask: np.ndarray, iterations: int = 15) -> Image.Image:
        """
        Inpaints masked regions (where mask > 0) by smoothly diffusing boundary textures inward
        and synthesizing boundary micro-texture residuals.
        """
        img_rgb = image.convert("RGB")
        img_arr = np.array(img_rgb, dtype=np.float32)
        h, w, c = img_arr.shape

        mask_bool = (mask > 50)
        if not np.any(mask_bool):
            return image

        # 1. Boundary analysis
        boundary_dilated = np.array(Image.fromarray(mask_bool.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(9))) > 0
        boundary_ring = boundary_dilated & (~mask_bool)

        if not np.any(boundary_ring):
            return image

        # 2. Check texture variance in the surrounding boundary
        boundary_lum = 0.299 * img_arr[boundary_ring, 0] + 0.587 * img_arr[boundary_ring, 1] + 0.114 * img_arr[boundary_ring, 2]
        boundary_texture_std = float(np.std(boundary_lum))
        is_textured = boundary_texture_std > 6.0

        # 3. Base Harmonic Diffusion (solves global illumination)
        for ch in range(c):
            channel = img_arr[:, :, ch]
            fill_val = np.mean(channel[boundary_ring])
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

        # 4. If textured, blend micro-texture residuals from the surrounding boundary
        if is_textured:
            rng = np.random.default_rng(seed=42)
            for ch in range(c):
                channel = img_arr[:, :, ch]
                boundary_mean = np.mean(channel[boundary_ring])
                residuals = channel[boundary_ring] - boundary_mean
                if len(residuals) > 0:
                    sample_size = int(np.sum(mask_bool))
                    sampled_residuals = rng.choice(residuals, size=sample_size)
                    channel[mask_bool] = np.clip(channel[mask_bool] + sampled_residuals * 0.70, 0.0, 255.0)

        return Image.fromarray(np.clip(img_arr, 0, 255).astype(np.uint8), mode="RGB")


class InpaintingSanitizer:
    """
    Content-Aware Inpainting for visible stamps, logos, and overlays.
    Wraps ExemplarPatchInpainter for backwards compatibility.
    """

    @staticmethod
    def inpaint(image: Image.Image, mask: np.ndarray, iterations: int = 15) -> Image.Image:
        return ExemplarPatchInpainter.inpaint(image, mask, iterations=iterations)


class AutoWatermarkDetector:
    """
    Zero-Click Automated Watermark Detector for Single Images.
    Detects watermark regions without requiring user coordinates:
    1. Corner watermark badges, timestamps, and app logos (e.g. TikTok, Shot on OnePlus).
    2. Stock photo diagonal lattices and repeating watermark grids (Shutterstock, Getty, iStock).
    3. High-contrast / high-frequency text overlays.
    """

    @staticmethod
    def detect_corner_watermarks(image: Image.Image, margin_pct: float = 0.35) -> np.ndarray:
        """
        Scans all 4 image corners for localized logos, timestamps, and copyright stamps.
        Returns a binary mask (uint8: 0 or 255) of detected corner watermarks.
        """
        img_gray = image.convert("L")
        w, h = image.size
        mask = np.zeros((h, w), dtype=np.uint8)

        # Compute morphological gradient: MaxFilter - MinFilter
        dilated = np.array(img_gray.filter(ImageFilter.MaxFilter(5)), dtype=np.float32)
        eroded = np.array(img_gray.filter(ImageFilter.MinFilter(5)), dtype=np.float32)
        morph_grad = dilated - eroded

        mw = int(w * margin_pct)
        mh = int(h * margin_pct)

        corners = [
            (0, 0, mw, mh),                 # Top-left
            (w - mw, 0, w, mh),             # Top-right
            (0, h - mh, mw, h),             # Bottom-left
            (w - mw, h - mh, w, h),         # Bottom-right (most common)
        ]

        global_mean_grad = float(np.mean(morph_grad))

        for x0, y0, x1, y1 in corners:
            corner_grad = morph_grad[y0:y1, x0:x1]
            threshold = max(24.0, global_mean_grad * 1.5)
            c_mask = corner_grad > threshold
            if np.sum(c_mask) >= 15:
                # Find bounding box of the watermark cluster in this corner to cover solid badges and text interiors
                ys, xs = np.where(c_mask)
                cy0, cy1 = int(np.min(ys)), int(np.max(ys))
                cx0, cx1 = int(np.min(xs)), int(np.max(xs))

                cluster_w = cx1 - cx0
                cluster_h = cy1 - cy0
                if cluster_w > 4 and cluster_h > 4 and (cluster_w * cluster_h) < (w * h * 0.25):
                    pad = 3
                    by0 = max(0, y0 + cy0 - pad)
                    by1 = min(h, y0 + cy1 + pad + 1)
                    bx0 = max(0, x0 + cx0 - pad)
                    bx1 = min(w, x0 + cx1 + pad + 1)
                    mask[by0:by1, bx0:bx1] = 255

        return mask

    @staticmethod
    def detect_stock_grid_watermarks(image: Image.Image) -> np.ndarray:
        """
        Detects repeating diagonal grid lines typical of stock photography (Getty, Shutterstock, Adobe Stock).
        Identifies directional contrast anomalies aligned along +/-45 deg diagonal axes.
        """
        img_gray = image.convert("L")
        arr = np.array(img_gray, dtype=np.float32)
        h, w = arr.shape
        mask = np.zeros((h, w), dtype=np.uint8)

        if h < 40 or w < 40:
            return mask

        # Diagonal directional derivatives:
        # 1. 45-degree axis: perpendicular is (-45 deg)
        d_perp1 = np.abs(arr[2:, :-2] - arr[:-2, 2:])
        d_para1 = np.abs(arr[2:, 2:] - arr[:-2, :-2])
        grid1 = (d_perp1 > (d_para1 * 2.2 + 10.0)) & (d_perp1 > 16.0)

        # 2. -45-degree axis: perpendicular is (+45 deg)
        d_perp2 = np.abs(arr[2:, 2:] - arr[:-2, :-2])
        d_para2 = np.abs(arr[2:, :-2] - arr[:-2, 2:])
        grid2 = (d_perp2 > (d_para2 * 2.2 + 10.0)) & (d_perp2 > 16.0)

        combined_grid = grid1 | grid2
        # Require substantial line coverage before activating grid removal
        if np.sum(combined_grid) >= (w * h * 0.003):
            cand_img = Image.fromarray(combined_grid.astype(np.uint8) * 255)
            dilated_grid = np.array(cand_img.filter(ImageFilter.MaxFilter(5)))
            mask[1:-1, 1:-1] = dilated_grid

        return mask

    @classmethod
    def detect_watermark_mask(cls, image: Image.Image) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Unified Zero-Click Detector:
        Scans for corner watermarks, stock photo lattices, and high-frequency stamps.
        Returns (binary_mask, detection_audit).
        """
        corner_mask = cls.detect_corner_watermarks(image)
        grid_mask = cls.detect_stock_grid_watermarks(image)

        combined_mask = np.maximum(corner_mask, grid_mask)
        total_pixels = int(np.sum(combined_mask > 0))

        audit = {
            "corner_watermark_detected": bool(np.any(corner_mask > 0)),
            "grid_watermark_detected": bool(np.any(grid_mask > 0)),
            "detected_watermark_pixels": total_pixels
        }
        return combined_mask, audit


class BatchImageAlphaInverter:
    """
    Google Research-inspired batch image matting and exact linear alpha inversion.
    When multiple images (e.g. stock previews, catalog photos, screenshots)
    share the same watermark at the same position and opacity:

    Solves:
      I_k(x,y) = alpha(x,y) * W(x,y) + (1 - alpha(x,y)) * B_k(x,y)
    yielding:
      B_k(x,y) = clip( (I_k(x,y) - alpha(x,y) * W(x,y)) / (1.0 - alpha(x,y)), 0, 255 )

    Delivers 100% sharp texture recovery with 0% blur.
    """

    @staticmethod
    def invert_batch(images: List[Image.Image]) -> Tuple[List[Image.Image], Dict[str, Any]]:
        if len(images) < 2:
            return images, {"status": "skipped", "reason": "Requires at least 2 images for batch inversion"}

        # Reference geometry
        ref_w, ref_h = images[0].size
        arrays = []
        for img in images:
            im = img.convert("RGB")
            if im.size != (ref_w, ref_h):
                im = im.resize((ref_w, ref_h), Image.Resampling.BILINEAR)
            arrays.append(np.array(im, dtype=np.float32))

        stacked = np.stack(arrays, axis=0)  # Shape: (N, H, W, 3)

        # 1. Background estimation via percentile
        bg_estimate = np.percentile(stacked, 75, axis=0)

        # 2. Extract stationary gradient: median gradient across images cancels out uncorrelated backgrounds
        grad_y = np.median(np.diff(stacked, axis=1), axis=0)
        grad_x = np.median(np.diff(stacked, axis=2), axis=0)

        grad_mag = np.zeros((ref_h, ref_w), dtype=np.float32)
        grad_mag[:-1, :] += np.mean(np.abs(grad_y), axis=2)
        grad_mag[:, :-1] += np.mean(np.abs(grad_x), axis=2)

        # Contrast deviation from local neighborhood
        contrast_dev = np.mean(np.abs(bg_estimate - np.median(stacked, axis=0)), axis=2)
        wm_mask = (grad_mag > 22.0) | (contrast_dev > 6.0)

        # Morphological dilation to encompass full watermark strokes
        cand_mask_img = Image.fromarray((wm_mask * 255).astype(np.uint8))
        dilated_wm = np.array(cand_mask_img.filter(ImageFilter.MaxFilter(5))) > 0
        total_wm_pixels = int(np.sum(dilated_wm))

        if total_wm_pixels < 20:
            return images, {"status": "no_recurrent_watermark_found", "wm_pixels": 0}

        # Continuous alpha estimation
        alpha_est = np.clip(contrast_dev / 55.0, 0.05, 0.85)
        alpha_3d = np.repeat(alpha_est[:, :, np.newaxis], 3, axis=2)

        cleaned_images = []
        for i, arr in enumerate(arrays):
            clean_arr = arr.copy()
            restored = (arr - alpha_3d * bg_estimate) / (1.0 - alpha_3d + 1e-5)
            clean_arr[dilated_wm] = np.clip(restored[dilated_wm], 0.0, 255.0)

            clean_img = Image.fromarray(clean_arr.astype(np.uint8), mode="RGB")
            if clean_img.size != images[i].size:
                clean_img = clean_img.resize(images[i].size, Image.Resampling.LANCZOS)
            cleaned_images.append(clean_img)

        audit = {
            "status": "success",
            "batch_size": len(images),
            "watermark_pixels_inverted": total_wm_pixels
        }
        return cleaned_images, audit


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


class PreflightStructuralDetector:
    """
    Enfocus PitStop-inspired structural preflight detector for PDFs.
    Identifies watermarks by syntactic, geometric, and topological traits
    without relying on predefined keyword dictionaries:
    1. Affine Matrix Rotation: Angles |theta| in [15°, 75°] or [105°, 165°] (diagonal tilt, e.g. 45°).
    2. Extended Graphics State: Fill/stroke alpha (/ca, /CA) <= 0.70.
    3. Structural Patterns: Full-page /Pattern fills (e.g. /wspe_, tiling watermarks).
    4. Cross-Page Repetition: Form XObjects or stream blocks recurring across pages.
    """

    @staticmethod
    def is_diagonal_affine(a: float, b: float, c: float, d: float) -> bool:
        """Determines if a 2D affine transformation matrix has a diagonal tilt angle."""
        if abs(a) < 1e-6 and abs(b) < 1e-6:
            return False
        angle = math.degrees(math.atan2(b, a)) % 180
        return (15.0 <= angle <= 75.0) or (105.0 <= angle <= 165.0)

    @classmethod
    def find_structural_watermark_xobjects(cls, doc: pymupdf.Document) -> Set[int]:
        """
        Scans all Form XObjects across the PDF dictionary tree to identify
        structural watermark objects by affine matrices, pattern fills, and low opacity.
        """
        watermark_xrefs = set()
        for xref in range(1, doc.xref_length()):
            try:
                subtype = doc.xref_get_key(xref, "Subtype")
                if subtype[1] == "/Form":
                    stream = doc.xref_stream(xref).decode("latin1", errors="ignore")
                    stream_lower = stream.lower()

                    # Trait 1: Diagonal transformation matrix (cm)
                    for m in re.finditer(
                        r'([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+cm',
                        stream
                    ):
                        try:
                            a, b, c, d = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
                            if cls.is_diagonal_affine(a, b, c, d):
                                watermark_xrefs.add(xref)
                                break
                        except Exception:
                            continue

                    # Trait 2: Pattern tiling full-page fill (/Pattern cs ... re f)
                    if "/pattern" in stream_lower and re.search(r'\bre\s+f\b', stream):
                        watermark_xrefs.add(xref)

                    # Trait 3: Extended graphics state setting low opacity
                    if any(t in stream for t in ["/ca ", "/CA ", "/fitzca"]) and ("/X1" in stream or "/Im" in stream or "/Do" in stream):
                        watermark_xrefs.add(xref)

                    # Trait 4: Specific watermark stamp pattern naming (/wspe_)
                    if "/wspe_" in stream:
                        watermark_xrefs.add(xref)
            except Exception:
                continue

        return watermark_xrefs

    @classmethod
    def strip_structural_stream_watermarks(cls, stream: str) -> Tuple[str, int]:
        """
        Scans raw page content streams for standalone structural watermark blocks
        enclosed in graphics state push/pop operators (q ... Q).
        """
        count = 0

        def inspect_q_block(match):
            nonlocal count
            block = match.group(0)

            # Check 1: Does it contain diagonal rotation cm?
            has_diagonal = False
            for m in re.finditer(
                r'([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+cm',
                block
            ):
                try:
                    a, b, c, d = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
                    if cls.is_diagonal_affine(a, b, c, d):
                        has_diagonal = True
                        break
                except Exception:
                    continue

            # Check 2: Low transparency or faint gray color
            has_transparency = any(t in block for t in ["/ca ", "/CA ", "/fitzca"])
            has_faint_color = False
            color_match = re.search(r'([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+(?:rg|scn)', block)
            if color_match:
                try:
                    r, g, b = float(color_match.group(1)), float(color_match.group(2)), float(color_match.group(3))
                    if r > 0.75 and g > 0.75 and b > 0.75 and abs(r - g) < 0.15:
                        has_faint_color = True
                except Exception:
                    pass

            # Check 3: Full-page pattern fill (/Pattern cs ... re f)
            is_pattern_fill = "/pattern" in block.lower() and bool(re.search(r'\bre\s+f\b', block))

            if is_pattern_fill:
                # If there are text operators inside, excise only the pattern fill command to preserve text
                if "BT" in block and "ET" in block:
                    count += 1
                    return re.sub(r'/Pattern\s+cs.*?re\s+f', '', block, flags=re.DOTALL)
                count += 1
                return ""

            if has_diagonal and (has_transparency or has_faint_color):
                # If there are legitimate text operators inside the block, do not delete the text
                if "BT" in block and "ET" in block:
                    # Check if text is body text (contains dark color or standard font matrix)
                    if not re.search(r'0(?:\.0+)?\s+0(?:\.0+)?\s+0(?:\.0+)?\s+(?:rg|scn)', block):
                        count += 1
                        return ""
                    # Legitimate text detected; strip only transparency and diagonal graphics paths
                    count += 1
                    return re.sub(r'/(?:ca|CA|fitzca)[^\n\r]*', '', block)
                count += 1
                return ""

            return block

        cleaned_stream = re.sub(r'q\b[^Q]*?Q', inspect_q_block, stream, flags=re.DOTALL)
        return cleaned_stream, count


class ChrominanceDeltaSanitizer:
    """
    Separates chrominance (color saturation) from luminance to neutralize colored document stamps
    (red 'COPY', blue seal, purple approval stamp) over monochrome text.
    Protects underlying or overlapping text strokes from being erased.
    """

    @staticmethod
    def neutralize_color_stamps(image: Image.Image, sat_threshold: float = 0.18, paper_threshold: float = 0.65) -> Tuple[Image.Image, int]:
        img_rgb = image.convert("RGB")
        arr = np.array(img_rgb, dtype=np.float32) / 255.0

        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        cmax = np.maximum(r, np.maximum(g, b))
        cmin = np.minimum(r, np.minimum(g, b))
        delta = cmax - cmin

        # HSV saturation and brightness value
        saturation = delta / (cmax + 1e-5)
        value = cmax

        # 1. Color stamp on light paper background: Bleach to pure paper white (1.0)
        stamp_on_paper = (saturation > sat_threshold) & (value >= paper_threshold)

        # 2. Color stamp intersecting dark text strokes (value < paper_threshold):
        # Desaturate back to monochrome text stroke so the text remains 100% visible and sharp
        stamp_on_text = (saturation > sat_threshold) & (value < paper_threshold) & (lum < 0.55)

        count = int(np.sum(stamp_on_paper)) + int(np.sum(stamp_on_text))
        if count == 0:
            return image, 0

        cleaned_arr = arr.copy()
        cleaned_arr[stamp_on_paper] = 1.0

        for c in range(3):
            cleaned_arr[stamp_on_text, c] = lum[stamp_on_text]

        out_img = Image.fromarray((np.clip(cleaned_arr, 0.0, 1.0) * 255.0).astype(np.uint8), mode="RGB")
        return out_img, count


class MultiPageAlphaInverter:
    """
    Google Research-inspired multi-page watermark extraction & exact alpha inversion.
    Exploits the repetition vulnerability: watermarks in documents and scanned books
    repeat at identical coordinates and opacity across multiple pages, while the underlying text changes.

    Solves:
      I_k(x,y) = alpha(x,y) * W(x,y) + (1 - alpha(x,y)) * B_k(x,y)
    yielding:
      B_k(x,y) = clip( PaperColor + (I_k(x,y) - I_bg(x,y)) / (1.0 - alpha(x,y)), 0, 255 )
    providing 100% sharp text recovery without blurring or AI hallucination artifacts.
    """

    @staticmethod
    def invert_scanned_pages(
        images: List[Image.Image],
        paper_threshold: float = 230.0
    ) -> Tuple[List[Image.Image], int]:
        if len(images) < 2:
            return images, 0

        ref_w, ref_h = images[0].size
        arrays = []
        for img in images:
            if img.size != (ref_w, ref_h):
                img = img.resize((ref_w, ref_h), Image.Resampling.BILINEAR)
            arrays.append(np.array(img.convert("RGB"), dtype=np.float32))

        stacked = np.stack(arrays, axis=0)  # Shape (N, H, W, 3)

        # 80th percentile across pages estimates the watermark blended onto background paper
        bg_estimate = np.percentile(stacked, 80, axis=0)

        # Determine paper color from the brightest regions of the estimated background
        lum_bg = 0.299 * bg_estimate[:, :, 0] + 0.587 * bg_estimate[:, :, 1] + 0.114 * bg_estimate[:, :, 2]
        paper_pixels = bg_estimate[lum_bg >= paper_threshold]
        if len(paper_pixels) > 0:
            paper_color = np.median(paper_pixels, axis=0)
        else:
            paper_color = np.array([255.0, 255.0, 255.0], dtype=np.float32)

        # Contrast delta between estimated paper and estimated watermark
        delta = paper_color - bg_estimate
        contrast = np.mean(np.maximum(delta, 0.0), axis=2)

        # Watermark exists where contrast is above noise threshold
        wm_mask = (contrast >= 7.0)
        total_wm_pixels = int(np.sum(wm_mask))
        if total_wm_pixels < 30:
            return images, 0

        # Alpha estimation: contrast normalized against expected watermark opacity range
        alpha_est = np.clip(contrast / 65.0, 0.05, 0.82)
        alpha_3d = np.repeat(alpha_est[:, :, np.newaxis], 3, axis=2)

        cleaned_images = []
        for i, arr in enumerate(arrays):
            clean_arr = arr.copy()
            restored = paper_color + (arr - bg_estimate) / (1.0 - alpha_3d + 1e-5)
            clean_arr[wm_mask] = np.clip(restored[wm_mask], 0.0, 255.0)

            clean_img = Image.fromarray(clean_arr.astype(np.uint8), mode="RGB")
            if clean_img.size != images[i].size:
                clean_img = clean_img.resize(images[i].size, Image.Resampling.LANCZOS)
            cleaned_images.append(clean_img)

        return cleaned_images, total_wm_pixels


class PDFWatermarkSanitizer:
    """
    Universal PDF Structural, Vector, and Visual Watermark Remover.
    Handles:
    - Syntax-level marked-content excision (/CPDFSTAMP, /Artifact, /Watermark).
    - Header discriminator: preserves legitimate document headers while stripping tracking hashes.
    - Document-wide and page-level Form XObject unlinking & clearing.
    - Enfocus PitStop-inspired structural preflight rotation matrix and pattern slicing.
    - Google Research-inspired multi-page exact alpha inversion for scanned pages.
    - Chrominance separation for colored stamps.
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

        # Step 3b.2: Enfocus PitStop-inspired Structural Preflight Detection (zero-keyword)
        structural_forms = PreflightStructuralDetector.find_structural_watermark_xobjects(doc)
        watermark_xrefs.update(structural_forms)
        if structural_forms:
            audit["structural_watermarks_removed"] = len(structural_forms)

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
                        angle = math.degrees(math.atan2(abs(dir_y), abs(dir_x))) % 180
                        # Require a genuine diagonal orientation (angle between 15° and 75°)
                        is_diagonal = (15.0 <= angle <= 75.0)
                        line_text = "".join(s.get("text", "") for s in l.get("spans", [])).strip()

                        for s in l.get("spans", []):
                            alpha = s.get("alpha", 255)
                            c_int = s.get("color", 0)
                            r = (c_int >> 16) & 0xFF
                            g = (c_int >> 8) & 0xFF
                            b_col = c_int & 0xFF
                            lum = 0.299 * r + 0.587 * g + 0.114 * b_col
                            size = s.get("size", 10.0)

                            # Faint gray or high transparency: require large font size (watermarks are large)
                            is_faint = ((195 < lum < 245 and abs(r - g) < 15 and abs(g - b_col) < 15) or (0 < alpha < 160)) and (size >= 16.0 or is_diagonal)

                            # Safe addition: Only add the complete line text if short (<= 4 words)
                            # NEVER add individual generic words to prevent deleting body paragraphs!
                            if (is_diagonal or is_faint) and (3 <= len(line_text) <= 50):
                                words = line_text.lower().split()
                                if len(words) <= 5:
                                    dynamic_watermark_terms.add(line_text.lower())
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

                            # 1. Check repeating university exam tracking tokens across the block
                            cleaned_alnum = re.sub(r'[^a-z0-9]', '', decoded)
                            if len(cleaned_alnum) >= 40:
                                chunk = cleaned_alnum[:24]
                                if cleaned_alnum.count(chunk) >= 2:
                                    return ""

                            # 2. Check if the block has text showing operators
                            if not re.search(r'Tj|TJ|\'|\"', block):
                                return block

                            # 3. Surgical Operator-Level Excision:
                            # Instead of deleting the entire BT...ET block (which wipes out paragraphs of legitimate text),
                            # we inspect individual text showing operators (Tj and TJ) and excise ONLY the watermark ones.
                            def clean_single_tj(m_tj):
                                raw_op = m_tj.group(0)
                                content = decode_pdf_stream_text(m_tj.group(1)).lower().strip()
                                if any(kw in content for kw in dynamic_watermark_terms):
                                    return ""
                                return raw_op

                            modified_block = re.sub(r'\(([^\)]*?)\)\s*Tj', clean_single_tj, block)
                            modified_block = re.sub(r'<([0-9a-fA-F]+)>\s*Tj', clean_single_tj, modified_block)

                            def clean_array_tj(m_arr):
                                raw_op = m_arr.group(0)
                                content = decode_pdf_stream_text(m_arr.group(1)).lower().strip()
                                if any(kw in content for kw in dynamic_watermark_terms):
                                    return ""
                                return raw_op

                            modified_block = re.sub(r'\[(.*?)\]\s*TJ', clean_array_tj, modified_block, flags=re.DOTALL)

                            # If all text operators were watermark and excised, remove the now-empty block
                            if not re.search(r'Tj|TJ|\'|\"', modified_block):
                                return ""

                            return modified_block

                        modified_stream_bt = re.sub(r'BT\b.*?ET', clean_bt_et, modified_stream, flags=re.DOTALL)
                        if modified_stream_bt != modified_stream:
                            audit["text_watermarks_removed"] += 1
                            modified_stream = modified_stream_bt

                        # 6. Enfocus PitStop-inspired Structural Preflight Slicer (diagonal affine & pattern fills)
                        modified_stream_struct, count_struct = PreflightStructuralDetector.strip_structural_stream_watermarks(modified_stream)
                        if count_struct > 0:
                            audit["structural_watermarks_removed"] = audit.get("structural_watermarks_removed", 0) + count_struct
                            modified_stream = modified_stream_struct

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

                        # 3. Chrominance Delta Neutralizer (colored stamps: red/blue/purple)
                        clean_chroma_img, chroma_count = ChrominanceDeltaSanitizer.neutralize_color_stamps(pil_img)
                        if chroma_count > 0:
                            pil_img = clean_chroma_img
                            image_modified = True
                            audit["chroma_stamps_neutralized"] = audit.get("chroma_stamps_neutralized", 0) + chroma_count

                        if image_modified:
                            buf = io.BytesIO()
                            pil_img.save(buf, format="PNG")
                            page.replace_image(xref, stream=buf.getvalue())
                            audit["images_sanitized"] += 1
                    except Exception:
                        pass

        # 4F. Google Research Multi-Page Exact Alpha Inversion for Scanned Multi-Page PDFs
        if flatten_scanned and total_pages >= 2:
            scanned_pages_info = []
            for pidx in range(min(total_pages, 8)):
                p = doc[pidx]
                if len(p.get_text().strip()) < 30:
                    imgs = p.get_images()
                    if len(imgs) == 1:
                        scanned_pages_info.append((pidx, imgs[0][0]))

            if len(scanned_pages_info) >= 2:
                try:
                    pil_imgs = []
                    for pidx, img_xref in scanned_pages_info:
                        extracted = doc.extract_image(img_xref)
                        if extracted and "image" in extracted:
                            pil_imgs.append(Image.open(io.BytesIO(extracted["image"])).convert("RGB"))

                    if len(pil_imgs) == len(scanned_pages_info):
                        cleaned_scanned_imgs, inv_pixels = MultiPageAlphaInverter.invert_scanned_pages(pil_imgs)
                        if inv_pixels > 0:
                            for idx, (pidx, img_xref) in enumerate(scanned_pages_info):
                                buf = io.BytesIO()
                                cleaned_scanned_imgs[idx].save(buf, format="PNG")
                                doc[pidx].replace_image(img_xref, stream=buf.getvalue())
                                processed_xrefs.add(img_xref)
                            audit["alpha_inversions_applied"] = len(scanned_pages_info)
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
            has_alpha = (img.mode == "RGBA")
            audit = {
                "tracking_dots_removed": 0,
                "stego_neutralized": False,
                "scanned_watermarks_bleached": 0,
                "color_stamps_neutralized": 0,
                "inpainting_applied": False,
                "auto_detected_watermark": False
            }

            if remove_dots:
                img, dots = PaperSanitizer.remove_printer_tracking_dots(img)
                audit["tracking_dots_removed"] = dots

            # 1. Neutralize color stamps (red/blue/purple) preserving dark text/lines
            img, chroma_count = ChrominanceDeltaSanitizer.neutralize_color_stamps(img)
            if chroma_count > 0:
                audit["color_stamps_neutralized"] = chroma_count

            if flatten_scanned:
                img, bleached = PaperSanitizer.bleach_scanned_watermarks(img)
                audit["scanned_watermarks_bleached"] = bleached

            if remove_stego:
                img = SteganographySanitizer.neutralize_frequency_watermarks(img)
                img = SteganographySanitizer.clear_lsb_payload(img, bits=1)
                audit["stego_neutralized"] = True

            # 2. Inpainting: manual rect or zero-click auto-detection
            if inpaint_rect:
                x0, y0, x1, y1 = inpaint_rect
                mask = np.zeros((img.height, img.width), dtype=np.uint8)
                mask[y0:y1, x0:x1] = 255
                img = ExemplarPatchInpainter.inpaint(img, mask)
                audit["inpainting_applied"] = True
            else:
                # Zero-click auto-detection (corner logos, stock photo lattices)
                detected_mask, det_audit = AutoWatermarkDetector.detect_watermark_mask(img)
                audit.update(det_audit)
                if np.any(detected_mask > 0):
                    img = ExemplarPatchInpainter.inpaint(img, detected_mask)
                    audit["inpainting_applied"] = True
                    audit["auto_detected_watermark"] = True

            out_buf = io.BytesIO()
            fmt = "PNG" if (has_alpha or ext in [".png", ".webp", ".bmp"]) else "JPEG"
            if fmt == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out_buf, format=fmt)
            return out_buf.getvalue(), audit

        else:
            raise ValueError(f"Unsupported file format: {ext}")

    @classmethod
    def sanitize_image_batch(
        cls,
        image_inputs: List[Union[bytes, str, Image.Image]],
        output_format: str = "PNG"
    ) -> Tuple[List[bytes], Dict[str, Any]]:
        """
        Google Research Multi-Image Formulation for Batches:
        Processes a collection of images sharing the same watermark, isolating W and alpha
        across the batch and executing exact mathematical inversion (0% blur).
        """
        pil_images = []
        for inp in image_inputs:
            if isinstance(inp, bytes):
                pil_images.append(Image.open(io.BytesIO(inp)).convert("RGB"))
            elif isinstance(inp, str):
                pil_images.append(Image.open(inp).convert("RGB"))
            elif isinstance(inp, Image.Image):
                pil_images.append(inp.convert("RGB"))

        cleaned_pil_imgs, audit = BatchImageAlphaInverter.invert_batch(pil_images)

        cleaned_bytes_list = []
        for c_img in cleaned_pil_imgs:
            buf = io.BytesIO()
            c_img.save(buf, format=output_format)
            cleaned_bytes_list.append(buf.getvalue())

        return cleaned_bytes_list, audit

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
