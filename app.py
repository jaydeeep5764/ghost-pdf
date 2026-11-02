import streamlit as st
import pymupdf
import io
import os
import sys
import json
from PIL import Image

# Ensure pdf_engine is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from smart_typography import resolve_metric_font_alias, sample_background_color
from watermark_engine import (
    PDFWatermarkSanitizer,
    PaperSanitizer,
    SteganographySanitizer,
    InpaintingSanitizer,
    DEFAULT_WATERMARK_KEYWORDS
)

st.set_page_config(
    page_title="Ghost Studio: PDF Editor & Universal Watermark Sanitizer",
    page_icon="🛡️",
    layout="wide"
)

# Custom CSS for modern aesthetic
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(90deg, #1E3A8A, #3B82F6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .badge {
        background-color: #ECFDF5;
        color: #065F46;
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 600;
        display: inline-block;
        margin-bottom: 1.2rem;
        border: 1px solid #A7F3D0;
    }
    .metric-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">Ghost Studio: PDF Editor & Universal Sanitizer</div>', unsafe_allow_html=True)
st.markdown('<div class="badge">🔒 100% In-Memory Local Processing | Zero Cloud Dependencies | Zero Tracking</div>', unsafe_allow_html=True)

# Sidebar: File loading & settings
with st.sidebar:
    st.header("1. Document Input")
    uploaded_file = st.file_uploader("Upload a PDF document", type=["pdf"])
    
    sample_path = os.path.join(os.path.dirname(__file__), "sample_invoice.pdf")
    
    pdf_bytes = None
    current_doc_name = None
    if uploaded_file is not None:
        pdf_bytes = uploaded_file.getvalue()
        current_doc_name = uploaded_file.name
    else:
        use_sample = st.checkbox("Or use demo invoice (sample_invoice.pdf)", value=True)
        if use_sample and os.path.exists(sample_path):
            with open(sample_path, "rb") as f:
                pdf_bytes = f.read()
            current_doc_name = "sample_invoice.pdf"

if not pdf_bytes:
    st.info("👈 Please upload a PDF in the sidebar to begin!")
    st.stop()

# Reset session state if a different document is uploaded
if st.session_state.get("active_doc_name") != current_doc_name:
    st.session_state["active_doc_name"] = current_doc_name
    st.session_state["sanitized_pdf_bytes"] = None
    st.session_state["sanitizer_audit"] = None
    st.session_state["edited_pdf_bytes"] = None

# Open document in memory
doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
total_pages = len(doc)

# Page selector in sidebar
with st.sidebar:
    st.header("2. Page Selection")
    page_num = st.number_input("Page Number", min_value=1, max_value=total_pages, value=1) - 1
    page = doc[page_num]

# Render current page for inspection
orig_pix = page.get_pixmap(dpi=150)
orig_img_bytes = orig_pix.tobytes("png")

# Top Level Mode Tabs
tab_sanitizer, tab_editor = st.tabs([
    "🛡️ Universal Watermark & Tracking Remover",
    "✏️ Pixel-Perfect Text Editor"
])

# ==============================================================================
# TAB 1: UNIVERSAL WATERMARK & TRACKING REMOVER
# ==============================================================================
with tab_sanitizer:
    s_col_controls, s_col_visual = st.columns([1, 1.2], gap="large")

    with s_col_controls:
        st.subheader("Sanitization Modules")
        st.caption("Select the watermark and tracking layers to neutralize:")

        opt_text = st.checkbox("Purge Text Watermarks (CONFIDENTIAL, DRAFT, publisher banners)", value=True)
        opt_annots = st.checkbox("Strip Watermark Annotations & OCG Layers", value=True)
        opt_meta = st.checkbox("Scrub Document Tracking Metadata (Author, Title, UUIDs)", value=True)
        opt_images = st.checkbox("Deep Scan Embedded Images (Tracking dots in scans)", value=False)
        
        custom_kw = st.text_input("Additional Watermark Keywords (comma-separated):", "")

        if st.button("⚡ Sanitize Document Now", type="primary", use_container_width=True):
            keywords = [k.strip() for k in custom_kw.split(",") if k.strip()]
            
            prog_bar = st.progress(0, text="Initializing sanitization...")

            try:
                clean_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
                sanitizer = PDFWatermarkSanitizer(keywords=keywords if keywords else None)

                def update_progress(curr, total):
                    pct = int(((curr + 1) / total) * 100)
                    prog_bar.progress(min(pct, 100), text=f"Sanitizing page {curr + 1} of {total}...")

                audit = sanitizer.sanitize_pdf_document(
                    clean_doc,
                    remove_text=opt_text,
                    remove_annots=opt_annots,
                    remove_layers=opt_annots,
                    strip_metadata=opt_meta,
                    sanitize_images=opt_images,
                    progress_callback=update_progress
                )

                prog_bar.progress(100, text="Finalizing clean PDF stream...")
                out_buffer = io.BytesIO()
                clean_doc.save(out_buffer, garbage=4, deflate=True)
                clean_bytes = out_buffer.getvalue()
                clean_doc.close()

                st.session_state["sanitized_pdf_bytes"] = clean_bytes
                st.session_state["sanitizer_audit"] = audit
                prog_bar.empty()
                st.success("Document sanitized successfully!")
            except Exception as e:
                prog_bar.empty()
                st.error(f"Error during sanitization: {e}")

        if st.session_state.get("sanitizer_audit"):
            st.markdown("#### 📊 Sanitization Audit Report")
            audit = st.session_state["sanitizer_audit"]
            c1, c2 = st.columns(2)
            c1.metric("Text Marks Purged", audit.get("text_watermarks_removed", 0))
            c2.metric("Annotations Stripped", audit.get("annotations_removed", 0))
            c3, c4 = st.columns(2)
            c3.metric("Layers Neutralized", audit.get("ocg_layers_removed", 0))
            c4.metric("Images Sanitized", audit.get("images_sanitized", 0))
            if audit.get("metadata_cleared"):
                st.info("🔒 Tracking metadata & author identities completely scrubbed.")

    with s_col_visual:
        st.subheader("Document Inspection & Preview")
        if "sanitized_pdf_bytes" in st.session_state and st.session_state["sanitized_pdf_bytes"]:
            st.success("✅ Document sanitized! Watermarks purged.")
            st.download_button(
                label="⬇️ Download Sanitized PDF",
                data=st.session_state["sanitized_pdf_bytes"],
                file_name=f"sanitized_{current_doc_name or 'clean.pdf'}",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )

            pv_col1, pv_col2 = st.columns(2)
            pv_col1.caption(f"**Original (Page {page_num + 1} of {total_pages})**")
            pv_col1.image(orig_img_bytes, use_container_width=True)

            s_doc = pymupdf.open(stream=st.session_state["sanitized_pdf_bytes"], filetype="pdf")
            s_pix = s_doc[page_num].get_pixmap(dpi=150)
            s_img_bytes = s_pix.tobytes("png")
            s_doc.close()

            pv_col2.caption(f"**Sanitized (Page {page_num + 1} of {total_pages})**")
            pv_col2.image(s_img_bytes, use_container_width=True)
        else:
            st.caption(f"**Original Page Preview (Page {page_num + 1} of {total_pages})**")
            st.image(orig_img_bytes, use_container_width=True)
            st.info("👈 Click '⚡ Sanitize Document Now' on the left to purge watermarks.")

# ==============================================================================
# TAB 2: PIXEL-PERFECT TEXT EDITOR
# ==============================================================================
with tab_editor:
    # Extract all text spans on current page
    text_page = page.get_text("dict")
    spans_list = []
    for block in text_page.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                for span in line["spans"]:
                    txt = span["text"].strip()
                    if txt and len(txt) > 1:
                        spans_list.append({
                            "text": span["text"],
                            "font": span["font"],
                            "size": span["size"],
                            "color": span["color"],
                            "flags": span["flags"],
                            "bbox": span["bbox"]
                        })

    unique_texts = list(dict.fromkeys([s["text"].strip() for s in spans_list]))

    col_controls, col_visual = st.columns([1, 1.2], gap="large")

    with col_controls:
        st.subheader("Edit Document Text")
        
        edit_mode = st.radio("Choose Input Method:", ["Select text found on page", "Type custom search string"], horizontal=True, key="edit_mode_radio")
        
        target_text = ""
        if edit_mode == "Select text found on page":
            if unique_texts:
                default_index = 0
                for i, t in enumerate(unique_texts):
                    if "$500" in t:
                        default_index = i
                        break
                target_text = st.selectbox("Select text to modify:", unique_texts, index=default_index)
            else:
                st.warning("No text blocks detected on this page (might be a scanned image).")
        else:
            target_text = st.text_input("Exact text to search & replace:", value="$500.00")

        matched_span = next((s for s in spans_list if target_text.strip() in s["text"]), None) if target_text else None

        if matched_span:
            st.markdown("#### 🔬 Smart Typography Detection")
            c_int = matched_span["color"]
            r = ((c_int >> 16) & 0xFF) / 255.0
            g = ((c_int >> 8) & 0xFF) / 255.0
            b = (c_int & 0xFF) / 255.0
            hex_color = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
            twin_font = resolve_metric_font_alias(matched_span["font"])

            m1, m2, m3 = st.columns(3)
            m1.metric("Original Font", matched_span["font"])
            m2.metric("Decimal Size", f"{matched_span['size']:.2f} pt")
            m3.metric("Metric Twin", twin_font)

            rect = pymupdf.Rect(matched_span["bbox"])
            bg_color = sample_background_color(page, rect)
            bg_hex = f"#{int(bg_color[0]*255):02x}{int(bg_color[1]*255):02x}{int(bg_color[2]*255):02x}"

            m4, m5 = st.columns(2)
            m4.markdown(f"**Text Color:** `{hex_color}` <span style='display:inline-block;width:14px;height:14px;background-color:{hex_color};border-radius:3px;'></span>", unsafe_allow_html=True)
            m5.markdown(f"**Sampled BG:** `{bg_hex}` <span style='display:inline-block;width:14px;height:14px;background-color:{bg_hex};border-radius:3px;'></span>", unsafe_allow_html=True)

        st.markdown("---")
        replacement_text = st.text_input("New Replacement Text:", value="$1,500.00" if "$500" in target_text else "CONFIDENTIAL")

        if st.button("Apply Pixel-Perfect Edit", type="primary", use_container_width=True):
            if not target_text:
                st.error("Please specify target text to replace.")
            else:
                edit_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
                mod_page = edit_doc[page_num]

                quads = mod_page.search_for(target_text, quads=True)
                if not quads:
                    st.error(f"Could not find exact text '{target_text}' on page {page_num + 1}.")
                else:
                    for quad in quads:
                        rect = quad.rect
                        bg_c = sample_background_color(mod_page, rect)
                        mod_page.add_redact_annot(quad, fill=bg_c)

                    mod_page.apply_redactions()

                    if matched_span:
                        orig_f = matched_span["font"]
                        font_size = matched_span["size"]
                        c_int = matched_span["color"]
                        r = ((c_int >> 16) & 0xFF) / 255.0
                        g = ((c_int >> 8) & 0xFF) / 255.0
                        b = (c_int & 0xFF) / 255.0

                        twin = resolve_metric_font_alias(orig_f)
                        f_obj = pymupdf.Font(twin)
                        font_id = f"custom_{twin}"
                        mod_page.insert_font(fontname=font_id, fontbuffer=f_obj.buffer)

                        baseline_y = rect.y1 - (font_size * 0.15)
                        insert_pt = pymupdf.Point(rect.x0, baseline_y)

                        mod_page.insert_text(
                            insert_pt,
                            replacement_text,
                            fontsize=font_size,
                            color=(r, g, b),
                            fontname=font_id
                        )

                out_buffer = io.BytesIO()
                edit_doc.save(out_buffer, garbage=4, deflate=True)
                edited_pdf_bytes = out_buffer.getvalue()
                edit_doc.close()
                st.session_state["edited_pdf_bytes"] = edited_pdf_bytes
                st.success(f"Successfully replaced '{target_text}' with '{replacement_text}'!")

    with col_visual:
        tab_view, tab_download = st.tabs(["👁️ Visual Preview", "📥 Export Clean PDF"])
        
        with tab_view:
            if st.session_state.get("edited_pdf_bytes"):
                v_col1, v_col2 = st.columns(2)
                v_col1.caption("**Original Page**")
                v_col1.image(orig_img_bytes, use_container_width=True)
                
                e_doc = pymupdf.open(stream=st.session_state["edited_pdf_bytes"], filetype="pdf")
                e_pix = e_doc[page_num].get_pixmap(dpi=150)
                e_img_bytes = e_pix.tobytes("png")
                e_doc.close()

                v_col2.caption("**Edited (Pixel-Perfect / Zero Watermarks)**")
                v_col2.image(e_img_bytes, use_container_width=True)
            else:
                st.caption(f"**Current Document Preview (Page {page_num + 1} of {total_pages})**")
                st.image(orig_img_bytes, use_container_width=True)

        with tab_download:
            if st.session_state.get("edited_pdf_bytes"):
                st.markdown("### Your clean, watermark-free document is ready!")
                st.download_button(
                    label="⬇️ Download Clean Vector PDF",
                    data=st.session_state["edited_pdf_bytes"],
                    file_name="edited_clean.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )
            else:
                st.info("Make an edit on the left column to generate your download file.")
