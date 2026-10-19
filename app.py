import streamlit as st
import pymupdf
import io
import os
import sys

# Ensure pdf_engine is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from smart_typography import resolve_metric_font_alias, sample_background_color

st.set_page_config(
    page_title="Pixel-Perfect PDF Editor (Ghost Engine)",
    page_icon="📄",
    layout="wide"
)

# Custom CSS for slick modern aesthetic
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
        margin-bottom: 1.5rem;
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

st.markdown('<div class="main-header">Pixel-Perfect PDF Editor</div>', unsafe_allow_html=True)
st.markdown('<div class="badge">🔒 Ghost Mode: 100% In-Memory Local Processing | Zero Watermarks</div>', unsafe_allow_html=True)

# Sidebar: File loading & settings
with st.sidebar:
    st.header("1. Document Input")
    uploaded_file = st.file_uploader("Upload a PDF document", type=["pdf"])
    
    use_sample = st.checkbox("Or use demo invoice (sample_invoice.pdf)", value=(uploaded_file is None))
    
    sample_path = os.path.join(os.path.dirname(__file__), "sample_invoice.pdf")
    
    pdf_bytes = None
    if uploaded_file is not None:
        pdf_bytes = uploaded_file.read()
    elif use_sample and os.path.exists(sample_path):
        with open(sample_path, "rb") as f:
            pdf_bytes = f.read()

if pdf_bytes is None:
    st.info("👈 Please upload a PDF in the sidebar or check 'use demo invoice' to begin testing!")
    st.stop()

# Open document in memory
doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
total_pages = len(doc)

# Page selector in sidebar
with st.sidebar:
    st.header("2. Page Selection")
    page_num = st.number_input("Page Number", min_value=1, max_value=total_pages, value=1) - 1
    page = doc[page_num]

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

# Main layout: 2 columns (Controls & Live Inspection on Left, Visual Diff on Right)
col_controls, col_visual = st.columns([1, 1.2], gap="large")

with col_controls:
    st.subheader("Edit Document Text")
    
    edit_mode = st.radio("Choose Input Method:", ["Select text found on page", "Type custom search string"], horizontal=True)
    
    target_text = ""
    if edit_mode == "Select text found on page":
        if unique_texts:
            default_index = 0
            # Try to default to $500.00 if present
            for i, t in enumerate(unique_texts):
                if "$500" in t:
                    default_index = i
                    break
            target_text = st.selectbox("Select text to modify:", unique_texts, index=default_index)
        else:
            st.warning("No text blocks detected on this page (might be a scanned image).")
    else:
        target_text = st.text_input("Exact text to search & replace:", value="$500.00")

    # Typography Inspector Card
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

        st.markdown(f"""
        <div class="metric-card">
            <strong>Color:</strong> <span style="display:inline-block; width:12px; height:12px; background-color:{hex_color}; border:1px solid #999; vertical-align:middle;"></span> {hex_color} (RGB {r:.2f}, {g:.2f}, {b:.2f})<br>
            <strong>Position (BBox):</strong> x0: {matched_span['bbox'][0]:.1f}, y0: {matched_span['bbox'][1]:.1f}, x1: {matched_span['bbox'][2]:.1f}, y1: {matched_span['bbox'][3]:.1f}
        </div>
        """, unsafe_allow_html=True)

    replacement_text = st.text_input("Replace with new text:", value="$750.00")
    
    action_col1, action_col2 = st.columns(2)
    apply_button = action_col1.button("✨ Apply Seamless Edit", type="primary", use_container_width=True)
    redact_only = action_col2.button("🚫 Whiteout / Erase Only", use_container_width=True)

# Execute edit in memory
edited_pdf_bytes = None
if apply_button or redact_only:
    if not target_text.strip():
        st.error("Please specify text to edit.")
    else:
        # Create a fresh document copy in memory
        edit_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        mod_page = edit_doc[page_num]
        rects = mod_page.search_for(target_text)

        if not rects:
            st.warning(f"Text '{target_text}' not found on page {page_num + 1}!")
        else:
            for rect in rects:
                # 1. Sample background tint
                bg_color = sample_background_color(mod_page, rect)
                
                # 2. Clean redaction
                mod_page.add_redact_annot(rect, fill=bg_color)
                mod_page.apply_redactions()

                if not redact_only:
                    # 3. Determine typography
                    if matched_span:
                        c_int = matched_span["color"]
                        r = ((c_int >> 16) & 0xFF) / 255.0
                        g = ((c_int >> 8) & 0xFF) / 255.0
                        b = (c_int & 0xFF) / 255.0
                        font_size = matched_span["size"]
                        orig_f = matched_span["font"]
                    else:
                        r, g, b = 0.1, 0.1, 0.1
                        font_size = 11.0
                        orig_f = "helv"

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

# Render original page to pixmap
orig_pix = page.get_pixmap(dpi=150)
orig_img_bytes = orig_pix.tobytes("png")

with col_visual:
    tab_view, tab_download = st.tabs(["👁️ Visual Preview", "📥 Export Clean PDF"])
    
    with tab_view:
        if "edited_pdf_bytes" in st.session_state:
            v_col1, v_col2 = st.columns(2)
            v_col1.caption("**Original Page**")
            v_col1.image(orig_img_bytes, use_container_width=True)
            
            # Render edited page
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
        if "edited_pdf_bytes" in st.session_state:
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
