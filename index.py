# index.py
import io
import tempfile
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple

import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader, simpleSplit
import pypdfium2 as pdfium

# ─────────────────────────────────────────────────────────────────────────────
# App config / constants
st.set_page_config(page_title="Advanced PDF Watermark Tool", layout="wide")
PT_PER_MM = mm
PREVIEW_LIMIT = 10  # live preview up to 10 pages (performance guard)

def mm_to_pt(v_mm: float) -> float:
    return float(v_mm) * PT_PER_MM

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def pick_font_name(bold: bool, italic: bool) -> str:
    if bold and italic: return "Helvetica-BoldOblique"
    if bold: return "Helvetica-Bold"
    if italic: return "Helvetica-Oblique"
    return "Helvetica"

def ensure_alpha(can, fill_alpha: Optional[float] = None, stroke_alpha: Optional[float] = None):
    # Best-effort transparency for ReportLab (older versions may ignore)
    if fill_alpha is not None:
        try: can.setFillAlpha(fill_alpha)
        except Exception: pass
    if stroke_alpha is not None:
        try: can.setStrokeAlpha(stroke_alpha)
        except Exception: pass

# ─────────────────────────────────────────────────────────────────────────────
# Data model & session
@dataclass
class Stamp:
    stamp_type: str  # "image" | "text"
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float
    rotation_deg: float = 0.0
    # per-stamp page range
    page_from: int = 1
    page_to: int = 1
    # image
    image_bytes: Optional[bytes] = None
    # text
    text: str = ""
    font_size_pt: int = 28
    bold: bool = True
    italic: bool = False
    rect_fill_hex: str = "#FFFFFF"
    rect_border_hex: str = "#000000"
    text_color_hex: str = "#000000"
    rect_opacity: float = 0.0   # 0 solid, 1 fully transparent
    border_width_pt: float = 1.0
    padding_mm: float = 3.0

if "stamps" not in st.session_state:
    st.session_state.stamps: List[Stamp] = []
if "preview_page_index" not in st.session_state:
    st.session_state.preview_page_index = 0  # 0-based
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "preview_update_requested" not in st.session_state:
    st.session_state.preview_update_requested = False

# ─────────────────────────────────────────────────────────────────────────────
# LEFT SIDEBAR — PDF & Add New Stamp
with st.sidebar:
    st.header("PDF & Preview")
    pdf_file = st.file_uploader("Upload PDF", type=["pdf"], help="Required")
    render_scale = st.slider("Preview quality / scale", 1.0, 3.0, 1.8, 0.1)

    num_pages = 0
    page_w_pt, page_h_pt = (595.276, 841.89)  # A4 defaults (points)

    if pdf_file:
        pdf_file.seek(0)
        st.session_state.pdf_bytes = pdf_file.read()
        try:
            probe = PdfReader(io.BytesIO(st.session_state.pdf_bytes))
            num_pages = len(probe.pages)
            mb = probe.pages[0].mediabox
            page_w_pt, page_h_pt = float(mb.width), float(mb.height)
        except Exception:
            st.error("Failed to read PDF. It may be encrypted or corrupted.")

    st.markdown("---")
    st.header("Add New Stamp")
    new_type = st.radio("Type", ["image", "text"], horizontal=True)

    # geometry defaults
    nx = st.number_input("X (mm)", 0.0, 5000.0, 50.0, 1.0)
    ny = st.number_input("Y (mm)", 0.0, 5000.0, 50.0, 1.0)
    nw = st.number_input("Width (mm)", 5.0, 5000.0, 50.0, 1.0)
    nh = st.number_input("Height (mm)", 5.0, 5000.0, 30.0, 1.0)
    nrot = st.slider("Rotation (°)", -180.0, 180.0, 0.0, 1.0)

    n_img = None
    n_text = ""
    n_font = 28
    n_bold = True
    n_italic = False
    n_fill = "#FFFFFF"
    n_border = "#000000"
    n_text_col = "#000000"
    n_opacity = 0.0
    n_bw = 1.0
    n_pad = 3.0

    if new_type == "image":
        up = st.file_uploader("Image (PNG/JPG)", type=["png", "jpg", "jpeg"], key="new_img")
        if up: n_img = up.read()
    else:
        n_text = st.text_input("Text", value="APPROVED")
        c1, c2, c3 = st.columns(3)
        with c1: n_bold = st.checkbox("Bold", True)
        with c2: n_italic = st.checkbox("Italic", False)
        with c3: n_font = st.number_input("Font size (pt)", 8, 200, 28)

        c4, c5 = st.columns(2)
        with c4:
            n_fill = st.color_picker("Rect fill", value="#FFFFFF")
            n_opacity = st.slider("Rect transparency (0→1)", 0.0, 1.0, 0.0, 0.05)
        with c5:
            n_border = st.color_picker("Rect border", value="#000000")
            n_bw = st.number_input("Border width (pt)", 0.0, 12.0, 1.0, 0.5)

        n_text_col = st.color_picker("Text color", value="#000000")
        n_pad = st.number_input("Padding (mm)", 0.0, 50.0, 3.0, 0.5)

    if st.button("➕ Add stamp"):
        if new_type == "image" and not n_img:
            st.warning("Please upload an image.")
        else:
            pf, pt = 1, max(1, num_pages) if num_pages else 1
            st.session_state.stamps.append(
                Stamp(
                    stamp_type=new_type, x_mm=nx, y_mm=ny, w_mm=nw, h_mm=nh, rotation_deg=nrot,
                    page_from=pf, page_to=pt,
                    image_bytes=n_img,
                    text=n_text, font_size_pt=n_font, bold=n_bold, italic=n_italic,
                    rect_fill_hex=n_fill, rect_border_hex=n_border, text_color_hex=n_text_col,
                    rect_opacity=n_opacity, border_width_pt=n_bw, padding_mm=n_pad
                )
            )
            st.success("Stamp added — edit it in the right control panel.")

# ─────────────────────────────────────────────────────────────────────────────
# Cached rendering & helpers
@st.cache_data(show_spinner=False)
def render_pdf_pages_to_images(pdf_bytes: bytes, scale: float, limit: int) -> Tuple[List[Image.Image], Tuple[float,float]]:
    try:
        pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    except Exception:
        return [], (595.276, 841.89)

    n = len(pdf)
    pages = min(n, limit)
    if pages <= 0:
        pdf.close()
        return [], (595.276, 841.89)

    first = pdf.get_page(0)
    page_w_pt = first.get_width()
    page_h_pt = first.get_height()
    first.close()

    images = []
    for i in range(pages):
        pg = pdf.get_page(i)
        img = pg.render(scale=scale).to_pil()
        pg.close()
        images.append(img)

    pdf.close()
    return images, (page_w_pt, page_h_pt)

def draw_preview_overlay_for_page(
    base_img: Image.Image,
    page_idx0: int,
    stamps: List[Stamp],
    page_w_pt: float,
    page_h_pt: float
) -> Image.Image:
    """Draw overlay for stamps whose page range includes page_idx0."""
    page = base_img.copy().convert("RGBA")
    overlay = Image.new("RGBA", page.size, (0,0,0,0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    px_per_pt_x = page.width / page_w_pt
    px_per_pt_y = page.height / page_h_pt

    def rect_pixels(x_pt, y_pt, w_pt, h_pt):
        left_px = int(round(x_pt * px_per_pt_x))
        right_px = int(round((x_pt + w_pt) * px_per_pt_x))
        top_px = int(round((y_pt + h_pt) * px_per_pt_y))
        bottom_px = int(round(y_pt * px_per_pt_y))
        # Convert to PIL (top-left origin)
        top_y = page.height - top_px
        bottom_y = page.height - bottom_px
        return left_px, top_y, right_px, bottom_y

    for sp in stamps:
        if not (sp.page_from - 1 <= page_idx0 <= sp.page_to - 1):
            continue

        x_pt, y_pt = mm_to_pt(sp.x_mm), mm_to_pt(sp.y_mm)
        w_pt, h_pt = mm_to_pt(sp.w_mm), mm_to_pt(sp.h_mm)
        l, t, r, b = rect_pixels(x_pt, y_pt, w_pt, h_pt)

        if sp.stamp_type == "image" and sp.image_bytes:
            try:
                img = Image.open(io.BytesIO(sp.image_bytes)).convert("RGBA")
                img = img.resize((max(1, r - l), max(1, b - t)))
                img = img.rotate(-sp.rotation_deg, expand=True, resample=Image.BICUBIC)
                # center inside target rect
                cx = (l + r) // 2
                cy = (t + b) // 2
                ox = cx - img.width // 2
                oy = cy - img.height // 2
                overlay.alpha_composite(img, (ox, oy))
            except Exception:
                pass
        else:
            # Rectangle fill
            fill_rgb = HexColor(sp.rect_fill_hex).rgb()
            border_rgb = HexColor(sp.rect_border_hex).rgb()
            alpha = int(round(255 * (1.0 - sp.rect_opacity)))

            # Fill first
            draw.rectangle(
                [l, t, r, b],
                fill=(int(fill_rgb[0]*255), int(fill_rgb[1]*255), int(fill_rgb[2]*255), alpha)
            )
            # Then border (fixed)
            border_px = max(1, int(round(sp.border_width_pt * px_per_pt_x)))
            draw.rectangle(
                [l, t, r, b],
                outline=(int(border_rgb[0]*255), int(border_rgb[1]*255), int(border_rgb[2]*255), alpha),
                width=border_px
            )
            # Text layer (rotate separately so border stays crisp)
            try:
                text_rgb = HexColor(sp.text_color_hex).rgb()
                text_rgba = (int(text_rgb[0]*255), int(text_rgb[1]*255), int(text_rgb[2]*255), 255)
                try:
                    font = ImageFont.truetype("arial.ttf", max(8, int(sp.font_size_pt * px_per_pt_y)))
                except Exception:
                    font = ImageFont.load_default()
                text_layer = Image.new("RGBA", page.size, (0,0,0,0))
                td = ImageDraw.Draw(text_layer)
                tw, th = td.textbbox((0,0), sp.text, font=font)[2:]
                cx = (l + r) // 2
                cy = (t + b) // 2
                tx = cx - tw // 2
                ty = cy - th // 2
                td.text((tx, ty), sp.text, fill=text_rgba, font=font)
                text_layer = text_layer.rotate(-sp.rotation_deg, resample=Image.BICUBIC)
                overlay = Image.alpha_composite(overlay, text_layer)
            except Exception:
                pass

    return Image.alpha_composite(page, overlay)

def build_overlay_pdf_for_page(stamps: List[Stamp], page_idx0: int, page_w_pt: float, page_h_pt: float) -> Optional[PdfReader]:
    """Create a 1-page overlay PDF containing stamps that apply to page_idx0."""
    relevant = [s for s in stamps if (s.page_from - 1 <= page_idx0 <= s.page_to - 1)]
    if not relevant:
        return None

    packet = io.BytesIO()
    can = rl_canvas.Canvas(packet, pagesize=(page_w_pt, page_h_pt))

    for sp in relevant:
        x_pt, y_pt = mm_to_pt(sp.x_mm), mm_to_pt(sp.y_mm)
        w_pt, h_pt = mm_to_pt(sp.w_mm), mm_to_pt(sp.h_mm)

        can.saveState()
        # rotate around center
        cx = x_pt + w_pt/2
        cy = y_pt + h_pt/2
        can.translate(cx, cy)
        can.rotate(sp.rotation_deg)
        can.translate(-w_pt/2, -h_pt/2)

        if sp.stamp_type == "image" and sp.image_bytes:
            can.drawImage(ImageReader(io.BytesIO(sp.image_bytes)), 0, 0, width=w_pt, height=h_pt, mask='auto')
        else:
            fill = HexColor(sp.rect_fill_hex)
            border = HexColor(sp.rect_border_hex)
            text_c = HexColor(sp.text_color_hex)
            can.setLineWidth(sp.border_width_pt)
            can.setStrokeColor(border)
            can.setFillColor(fill)
            alpha = max(0.0, min(1.0, 1.0 - float(sp.rect_opacity)))
            ensure_alpha(can, fill_alpha=alpha, stroke_alpha=alpha)
            can.rect(0, 0, w_pt, h_pt, stroke=1, fill=1)

            font_name = pick_font_name(sp.bold, sp.italic)
            can.setFillColor(text_c)
            can.setFont(font_name, float(sp.font_size_pt))
            pad = mm_to_pt(sp.padding_mm)
            box_w, box_h = max(0.0, w_pt - 2*pad), max(0.0, h_pt - 2*pad)
            lines = simpleSplit(sp.text or "", font_name, float(sp.font_size_pt), box_w)
            leading = float(sp.font_size_pt) * 1.2
            total_h = leading * len(lines)
            start_y = max((h_pt - total_h) / 2.0, pad)
            for i, line in enumerate(lines):
                lw = can.stringWidth(line, font_name, float(sp.font_size_pt))
                tx = max((w_pt - lw) / 2.0, pad)
                ty = start_y + leading * (len(lines) - 1 - i)
                if ty < pad: break
                can.drawString(tx, ty, line)
        can.restoreState()

    can.save()
    packet.seek(0)
    return PdfReader(packet)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN LAYOUT — Preview (left) and Right Control Panel (form-based)
main_col, right_col = st.columns([0.62, 0.38], gap="large")

# RIGHT CONTROL PANEL — edit LAST stamp ONLY, apply on submit
with right_col:
    st.header("Control Panel (Apply on Enter)")
    apply_now = False

    if not st.session_state.stamps:
        st.info("Add a stamp from the left sidebar to edit it here.")
    else:
        last_idx = len(st.session_state.stamps) - 1
        last = st.session_state.stamps[last_idx]

        with st.form(key="last_stamp_editor", clear_on_submit=False):
            st.caption("Editing the most recently added stamp. Changes apply when you press **Enter** or click **Update Preview**.")

            st.subheader("Page Range (this stamp only)")
            npages = num_pages if num_pages > 0 else 1
            cpg1, cpg2 = st.columns(2)
            with cpg1:
                page_from = st.number_input("From page", 1, npages, min(last.page_from, npages))
            with cpg2:
                page_to = st.number_input("To page", 1, npages, max(min(last.page_to, npages), page_from))

            st.subheader("Geometry")
            cg1, cg2 = st.columns(2)
            with cg1:
                x_mm = st.number_input("X (mm)", 0.0, 5000.0, last.x_mm, 1.0)
                w_mm = st.number_input("Width (mm)", 5.0, 5000.0, last.w_mm, 1.0)
            with cg2:
                y_mm = st.number_input("Y (mm)", 0.0, 5000.0, last.y_mm, 1.0)
                h_mm = st.number_input("Height (mm)", 5.0, 5000.0, last.h_mm, 1.0)
            rotation = st.slider("Rotation (°)", -180.0, 180.0, last.rotation_deg, 1.0)

            if last.stamp_type == "image":
                up2 = st.file_uploader("Replace image (optional)", type=["png", "jpg", "jpeg"], key="replace_img_form")
            else:
                st.subheader("Text & Style")
                cx1, cx2, cx3 = st.columns(3)
                with cx1: bold = st.checkbox("Bold", value=last.bold)
                with cx2: italic = st.checkbox("Italic", value=last.italic)
                with cx3: font_size_pt = st.number_input("Font size (pt)", 8, 200, last.font_size_pt)

                text_val = st.text_input("Text", value=last.text)

                cx4, cx5 = st.columns(2)
                with cx4:
                    rect_fill_hex = st.color_picker("Rect fill", value=last.rect_fill_hex)
                    rect_opacity = st.slider("Rect transparency (0→1)", 0.0, 1.0, last.rect_opacity, 0.05)
                with cx5:
                    rect_border_hex = st.color_picker("Rect border", value=last.rect_border_hex)
                    border_width_pt = st.number_input("Border width (pt)", 0.0, 12.0, last.border_width_pt, 0.5)

                text_color_hex = st.color_picker("Text color", value=last.text_color_hex)
                padding_mm = st.number_input("Padding (mm)", 0.0, 50.0, last.padding_mm, 0.5)

            submit = st.form_submit_button("🔄 Update Preview", use_container_width=True)

        if submit:
            st.session_state.preview_update_requested = True
            last.page_from = page_from
            last.page_to = page_to
            last.x_mm = x_mm; last.y_mm = y_mm; last.w_mm = w_mm; last.h_mm = h_mm
            last.rotation_deg = rotation
            if last.stamp_type == "image":
                if 'up2' in locals() and up2 is not None:
                    last.image_bytes = up2.read()
            else:
                last.bold = bold; last.italic = italic; last.font_size_pt = font_size_pt
                last.text = text_val
                last.rect_fill_hex = rect_fill_hex
                last.rect_opacity = rect_opacity
                last.rect_border_hex = rect_border_hex
                last.border_width_pt = border_width_pt
                last.text_color_hex = text_color_hex
                last.padding_mm = padding_mm
            st.session_state.stamps[last_idx] = last
            st.rerun()

    st.markdown("---")
    # Apply button (explicit action)
    apply_now = st.button("✅ Apply Stamp(s) to PDF", use_container_width=True)

# LEFT (CENTER) — Preview with spinner on update
with main_col:
    st.header("Preview (navigate)")
    if not st.session_state.pdf_bytes:
        st.info("Upload a PDF in the left sidebar.")
    else:
        # Show spinner if an update was requested
        if st.session_state.preview_update_requested:
            with st.spinner("Updating preview..."):
                pass
            st.session_state.preview_update_requested = False

        base_imgs, (page_w_pt, page_h_pt) = render_pdf_pages_to_images(
            st.session_state.pdf_bytes, render_scale, PREVIEW_LIMIT
        )
        total_preview_pages = len(base_imgs)
        if total_preview_pages == 0:
            st.error("Unable to load PDF for preview. It may be encrypted or corrupted.")
        else:
            nav1, nav2, nav3 = st.columns([0.2, 0.6, 0.2])
            with nav1:
                if st.button("◀ Prev", use_container_width=True) and st.session_state.preview_page_index > 0:
                    st.session_state.preview_page_index -= 1
                    st.rerun()
            with nav2:
                st.session_state.preview_page_index = st.slider(
                    "Preview page", 1, total_preview_pages, st.session_state.preview_page_index + 1, 1
                ) - 1
            with nav3:
                if st.button("Next ▶", use_container_width=True) and st.session_state.preview_page_index < total_preview_pages - 1:
                    st.session_state.preview_page_index += 1
                    st.rerun()

            idx = st.session_state.preview_page_index
            preview = draw_preview_overlay_for_page(
                base_imgs[idx], idx, st.session_state.stamps, page_w_pt, page_h_pt
            )
            st.image(preview, caption=f"Preview page {idx+1}/{total_preview_pages} (updates when you press 'Update Preview')")

# ─────────────────────────────────────────────────────────────────────────────
# APPLY — merge overlays by page, honoring each stamp's page range
if apply_now:
    if not st.session_state.pdf_bytes:
        st.error("Please upload a PDF.")
    elif not st.session_state.stamps:
        st.error("Please add at least one stamp.")
    else:
        with st.spinner("Applying stamps to PDF..."):
            reader = PdfReader(io.BytesIO(st.session_state.pdf_bytes))
            writer = PdfWriter()
            n = len(reader.pages)

            for i, page in enumerate(reader.pages):
                overlay_reader = build_overlay_pdf_for_page(st.session_state.stamps, i, page_w_pt, page_h_pt)
                if overlay_reader:
                    page.merge_page(overlay_reader.pages[0])
                writer.add_page(page)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                writer.write(tmp)
                out_path = tmp.name

        with open(out_path, "rb") as f:
            st.download_button("📥 Download stamped PDF", f, file_name="stamped_output.pdf", mime="application/pdf")
        st.success("✅ Done! Stamps applied.")
