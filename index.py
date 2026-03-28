# index.py — Advanced PDF Watermark Tool (optimized version)
# Features:
#  - Tiled Text Mode
#  - Multi-Stamp Manager
#  - Optional PDF Security
#  - Template Save/Load (lazy decode)
#  - Auto image compression for smaller templates

import io
import json
import base64
import os
import tempfile
import subprocess
import datetime
import copy
import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

import database as db
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from PyPDF2 import PdfReader, PdfWriter, PdfMerger
from PyPDF2.errors import FileNotDecryptedError
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader, simpleSplit
import pypdfium2 as pdfium
import streamlit.components.v1 as components

# --- INITIALIZE STREAMLIT ---
st.set_page_config(page_title="Advanced PDF Watermark Tool", layout="wide")

hide_st_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    div[data-testid="stAppDeployButton"] {visibility: hidden !important;}
    div[data-testid="stDecoration"] {visibility: hidden !important;}
    div[data-testid="stStatusWidget"] {visibility: hidden !important;}
    div[data-testid="stDeployButton"] {display: none !important;}
    </style>
"""
st.markdown(hide_st_style, unsafe_allow_html=True)



tabs = st.tabs([
    "🪶 Watermark / Stamp",
    "🔗 Merge PDFs",
    "✂️ Split PDF",
    "📄 Extract Pages",
    "🔍 Extract Text",
    "🖼 Convert to Images",
    "📦 Compress PDF"
])




# ─────────────────────────────────────────────────────────────────────────────
# 🔐 Access Control — Only authorized users can open the app

def login_screen():
    st.title("🔐 Access Restricted")
    st.write("This application is private. Please log in to continue.")
    user = st.text_input("Username")
    pw = st.text_input("Password", type="password")
    col1, col2 = st.columns([0.7, 0.3])
    with col2:
        if st.button("Login", use_container_width=True):
            if db.authenticate_user(user, pw):
                st.session_state.authenticated = True
                st.session_state.current_user = user
                # Create and save token in query params so it survives refreshes
                token = db.create_session(user)
                st.query_params["session"] = token
                
                st.success(f"Welcome, {user}! ✅")
                st.rerun()
            else:
                st.error("❌ Incorrect username or password. Access denied.")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.current_user = None
    
    # Auto-login if valid session token is in URL
    if "session" in st.query_params:
        session_token = st.query_params["session"]
        username = db.get_user_by_session(session_token)
        if username:
            st.session_state.authenticated = True
            st.session_state.current_user = username

# Show login form if not authenticated
if not st.session_state.authenticated:
    login_screen()
    st.stop()

def get_page_size_pt(page) -> Tuple[float, float]:
    """Get actual width and height of a PDF page, taking rotation into account."""
    mb = page.mediabox
    w, h = float(mb.width), float(mb.height)
    # If the page is rotated by 90 or 270 degrees, swap width and height
    if page.get('/Rotate', 0) in [90, 270]:
        return h, w
    return w, h

def run_watermark_tool():
    # ─────────────────────────────────────────────────────────────────────────────
    # Once authenticated, show top bar + logout
    col1, col2 = st.columns([0.85, 0.15])
    with col1:
        st.caption(f"👋 Logged in as: **{st.session_state.current_user}**")
    with col2:
        if st.button("🚪 Logout", use_container_width=True):
            user = st.session_state.current_user
            if user:
                db.set_setting(user, "session_token", "") # Clear token from DB
            
            st.session_state.authenticated = False
            st.session_state.current_user = None
            if "session" in st.query_params:
                del st.query_params["session"]
                
            st.rerun()


    # ─────────────────────────────────────────────────────────────────────────────
    # App config / constants
    # (Page config already set at top level)
    PT_PER_MM = mm
    PREVIEW_LIMIT = 10  # limit preview pages for performance

    # ─────────────────────────────────────────────────────────────────────────────
    # Utility functions
    def mm_to_pt(v_mm: float) -> float:
        return float(v_mm) * PT_PER_MM

    def pick_font_name(bold: bool, italic: bool) -> str:
        if bold and italic: return "Helvetica-BoldOblique"
        if bold: return "Helvetica-Bold"
        if italic: return "Helvetica-Oblique"
        return "Helvetica"

    def ensure_alpha(can, fill_alpha: Optional[float] = None, stroke_alpha: Optional[float] = None):
        if fill_alpha is not None:
            try: can.setFillAlpha(fill_alpha)
            except Exception: pass
        if stroke_alpha is not None:
            try: can.setStrokeAlpha(stroke_alpha)
            except Exception: pass

    def get_subdirectories(path: str) -> List[str]:
        try:
            p = os.path.normpath(os.path.expanduser(path))
            if not os.path.isdir(p):
                return []
            return [d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))]
        except Exception:
            return []

    def folder_picker_ui(key_prefix: str, current_path: str):
        """Simple server-side folder picker within Streamlit."""
        st.write(f"📂 **Browsing:** `{current_path}`")
        
        # Navigation
        parent = os.path.dirname(current_path)
        col_nav1, col_nav2 = st.columns([0.3, 0.7])
        with col_nav1:
            if st.button("⬅ Parent", key=f"{key_prefix}_up"):
                return parent
        
        subdirs = get_subdirectories(current_path)
        if not subdirs:
            st.caption("No subdirectories found.")
        else:
            selected_sub = st.selectbox("Subdirectories", ["-- Select to enter --"] + sorted(subdirs), key=f"{key_prefix}_select")
            if selected_sub != "-- Select to enter --":
                return os.path.join(current_path, selected_sub)
        
        return current_path

    # ─────────────────────────────────────────────────────────────────────────────
    # Optimization helpers
    def compress_image(img_bytes: bytes, max_size=(400, 400), quality=70) -> bytes:
        """Compress image to reduce template size."""
        try:
            im = Image.open(io.BytesIO(img_bytes))
            im.thumbnail(max_size)
            out = io.BytesIO()
            if im.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            im.save(out, format="JPEG", quality=quality)
            return out.getvalue()
        except Exception:
            return img_bytes

    @st.cache_data(show_spinner=False)
    def decode_b64_lazy(b64_str: str) -> bytes:
        """Cached Base64 decode."""
        return base64.b64decode(b64_str)

    # ─────────────────────────────────────────────────────────────────────────────
    # Data model
    @dataclass
    class Stamp:
        stamp_type: str  # "image" | "text"
        x_mm: float
        y_mm: float
        w_mm: float
        h_mm: float
        rotation_deg: float = 0.0
        page_from: int = 1
        page_to: int = 1
        image_bytes: Optional[bytes] = None
        text: str = ""
        font_size_pt: int = 28
        bold: bool = True
        italic: bool = False
        rect_fill_hex: str = "#FFFFFF"
        rect_border_hex: str = "#000000"
        text_color_hex: str = "#000000"
        rect_opacity: float = 0.0
        border_width_pt: float = 1.0
        padding_mm: float = 3.0
        tiled: bool = False
        tile_dx_mm: float = 60.0
        tile_dy_mm: float = 60.0
        tile_angle_deg: float = 45.0

    # ─────────────────────────────────────────────────────────────────────────────
    # Session defaults
    ss = st.session_state
    if "stamps" not in ss: ss.stamps: List[Stamp] = []
    if "selected_stamp_index" not in ss: ss.selected_stamp_index = None
    if "preview_page_index" not in ss: ss.preview_page_index = 0
    if "pdf_bytes" not in ss: ss.pdf_bytes = None
    if "preview_update_requested" not in ss: ss.preview_update_requested = False
    if "sec_enabled" not in ss: ss.sec_enabled = False
    if "sec_user_pw" not in ss: ss.sec_user_pw = ""
    if "sec_owner_pw" not in ss: ss.sec_owner_pw = ""
    if "sec_show_user" not in ss: ss.sec_show_user = False
    if "sec_show_owner" not in ss: ss.sec_show_owner = False

    # ─────────────────────────────────────────────────────────────────────────────
    # Template helpers
    def stamps_to_template_dict(stamps: List[Stamp]) -> dict:
        data = {"version": 2, "stamps": []}
        for s in stamps:
            d = s.__dict__.copy()
            if s.image_bytes:
                try:
                    d["image_bytes"] = base64.b64encode(s.image_bytes).decode("utf-8")
                except Exception:
                    d["image_bytes"] = None
            data["stamps"].append(d)
        return data

    def template_dict_to_stamps(data: dict) -> List[Stamp]:
        stamps = []
        for s in data.get("stamps", []):
            img_data = s.get("image_bytes")
            if isinstance(img_data, str) and len(img_data) > 20:
                s["image_bytes"] = decode_b64_lazy(img_data)
            stamps.append(Stamp(**s))
        return stamps

    # ─────────────────────────────────────────────────────────────────────────────
    # Sidebar — Upload PDF + Add New Stamp
    num_pages = 0
    page_w_pt, page_h_pt = (595.276, 841.89)

    with st.sidebar:
        st.header("📄 PDF Input")
        pdf_file = st.file_uploader("Upload PDF File", type=["pdf"], key="watermark_upload")
        
        # New: Auto-generate filename logic
        if pdf_file is not None:
            if "last_uploaded_name" not in ss or ss.last_uploaded_name != pdf_file.name:
                base_name = os.path.splitext(pdf_file.name)[0]
                ss.custom_filename = f"{base_name}_stamped.pdf"
                ss.last_uploaded_name = pdf_file.name
        else:
            if "last_uploaded_name" in ss:
                ss.custom_filename = "stamped_output.pdf"
                del ss.last_uploaded_name

        render_scale = st.slider("Preview quality / scale", 1.0, 3.0, 1.8, 0.1)

        if pdf_file:
            ss.pdf_bytes = pdf_file.read()
            try:
                probe = PdfReader(io.BytesIO(ss.pdf_bytes))
                num_pages = len(probe.pages)
                page_w_pt, page_h_pt = get_page_size_pt(probe.pages[0])
            except Exception:
                st.error("Failed to read PDF (maybe encrypted).")

        st.markdown("---")
        st.header("Add New Stamp")

        if "auto_sign" not in ss: ss.auto_sign = False
        ss.auto_sign = st.checkbox("Add automatic digital signature", value=ss.auto_sign)

        if ss.auto_sign:
            w_mm_page = page_w_pt / mm
            h_mm_page = page_h_pt / mm
            
            default_w = 60.0
            default_h = 20.0
            default_padding = 5.0
            
            default_x = w_mm_page - default_w - default_padding
            default_y = default_padding

            use_default_pos = st.checkbox("Use default position (bottom-right corner)", True)

            if not use_default_pos:
                default_x = st.number_input("X (mm)", 0.0, 5000.0, default_x)
                default_y = st.number_input("Y (mm)", 0.0, 5000.0, default_y)
                default_w = st.number_input("Width (mm)", 5.0, 5000.0, default_w)
                default_h = st.number_input("Height (mm)", 5.0, 5000.0, default_h)

            username = st.session_state.current_user or "Unknown User"
            db_sig_action = db.get_setting(username, "default_sig_action", "Digitally signed")
            
            sig_action_val = st.text_area("Signature Action (User and Date are added automatically)", value=db_sig_action, height=60)
            if sig_action_val != db_sig_action:
                db.set_setting(username, "default_sig_action", sig_action_val)
            sig_action = sig_action_val
            
            sig_opacity = st.slider("Signature Background Transparency", 0.0, 1.0, 0.0, 0.05, 
                                    help="0.0 = Solid background, 1.0 = Transparent background")

            if st.button("🖋 Add Digital Signature Stamp"):
                if not sig_action.strip():
                    st.error("Signature action cannot be empty!")
                else:
                    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    final_sig_text = f"{sig_action.strip()} by {username}\nDate: {now_str}"
                    
                    ss.stamps.append(
                        Stamp(
                            stamp_type="text",
                            x_mm=default_x,
                            y_mm=default_y,
                            w_mm=default_w,
                            h_mm=default_h,
                            rotation_deg=0,
                            page_from=1,
                            page_to=max(1, num_pages) if num_pages else 1,
                            text=final_sig_text,
                            font_size_pt=10,
                            bold=False,
                            italic=False,
                            rect_fill_hex="#FFFFFF",
                            rect_border_hex="#000000",
                            text_color_hex="#000000",
                            rect_opacity=sig_opacity,
                            border_width_pt=0.5,
                            padding_mm=2.0,
                            tiled=False
                        )
                    )
                    
                    st.success("✅ Digital signature added successfully!")
                    st.session_state.selected_stamp_index = len(ss.stamps) - 1
                    st.rerun()


        new_type = st.radio("Type", ["image", "text"], horizontal=True)
        nx = st.number_input("X (mm)", 0.0, 5000.0, 50.0)
        ny = st.number_input("Y (mm)", 0.0, 5000.0, 50.0)
        nw = st.number_input("Width (mm)", 5.0, 5000.0, 50.0)
        nh = st.number_input("Height (mm)", 5.0, 5000.0, 30.0)
        nrot = st.slider("Rotation (°)", -180.0, 180.0, 0.0)

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
        n_tiled = False
        n_tile_dx_mm = 60.0
        n_tile_dy_mm = 60.0
        n_tile_angle = 45.0

        if new_type == "image":
            up = st.file_uploader("Image (PNG/JPG)", type=["png", "jpg", "jpeg"], key="new_img")
            if up:
                n_img = compress_image(up.read())
        else:
            n_text = st.text_input("Text", "CONTROL COPY")
            c1, c2, c3 = st.columns(3)
            with c1: n_bold = st.checkbox("Bold", True)
            with c2: n_italic = st.checkbox("Italic", False)
            with c3: n_font = st.number_input("Font size (pt)", 8, 200, 28)
            c4, c5 = st.columns(2)
            with c4:
                n_fill = st.color_picker("Rect fill", "#FFFFFF")
                n_opacity = st.slider("Rect transparency (0→1)", 0.0, 1.0, 0.7, 0.05)
            with c5:
                n_border = st.color_picker("Rect border", "#000000")
                n_bw = st.number_input("Border width (pt)", 0.0, 12.0, 0.0, 0.5)
            n_text_col = st.color_picker("Text color", "#000000")
            n_pad = st.number_input("Padding (mm)", 0.0, 50.0, 3.0, 0.5)
            with st.expander("Tiled watermark (text only)"):
                n_tiled = st.checkbox("Enable tiled mode", False)
                n_tile_dx_mm = st.number_input("Tile spacing X (mm)", 10.0, 500.0, 120.0, 1.0)
                n_tile_dy_mm = st.number_input("Tile spacing Y (mm)", 10.0, 500.0, 120.0, 1.0)
                n_tile_angle = st.slider("Tile angle (°)", -180.0, 180.0, 45.0)

        if st.button("➕ Add stamp"):
            if new_type == "image" and not n_img:
                st.warning("Please upload an image.")
            else:
                pf, pt = 1, max(1, num_pages) if num_pages else 1
                ss.stamps.append(
                    Stamp(
                        stamp_type=new_type,
                        x_mm=nx, y_mm=ny, w_mm=nw, h_mm=nh, rotation_deg=nrot,
                        page_from=pf, page_to=pt,
                        image_bytes=n_img,
                        text=n_text, font_size_pt=n_font, bold=n_bold, italic=n_italic,
                        rect_fill_hex=n_fill, rect_border_hex=n_border, text_color_hex=n_text_col,
                        rect_opacity=n_opacity, border_width_pt=n_bw, padding_mm=n_pad,
                        tiled=(n_tiled if new_type == "text" else False),
                        tile_dx_mm=n_tile_dx_mm, tile_dy_mm=n_tile_dy_mm, tile_angle_deg=n_tile_angle
                    )
                )
                ss.selected_stamp_index = len(ss.stamps) - 1
                st.success("Stamp added — edit it in the right panel.")

    # ─────────────────────────────────────────────────────────────────────────────
    # Template Save/Load
    st.sidebar.markdown("---")
    st.sidebar.header("📁 Template Save/Load")
    c_tpl1, c_tpl2 = st.sidebar.columns(2)
    with c_tpl1:
        if st.button("💾 Save Template"):
            tpl = stamps_to_template_dict(ss.stamps)
            tpl_bytes = json.dumps(tpl, indent=2).encode("utf-8")
            st.download_button("⬇ Download JSON", tpl_bytes, "watermark_template.json", "application/json")
    with c_tpl2:
        tpl_file = st.file_uploader("Upload template.json", type=["json"], key="tpl_upload")

        # Stage 1: read file but don't rerun immediately
        if tpl_file and "template_pending" not in st.session_state:
            tpl_data = json.loads(tpl_file.read().decode("utf-8"))
            st.session_state.template_pending = tpl_data
            st.info("✅ Template ready — click 'Apply Template' to load it.")

        # Stage 2: apply after user confirms
        if "template_pending" in st.session_state:
            if st.button("📄 Apply Template"):
                try:
                    ss.stamps = template_dict_to_stamps(st.session_state.template_pending)
                    del st.session_state["template_pending"]
                    ss.preview_update_requested = True
                    st.success("Template applied instantly ✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error applying template: {e}")


    # (The rest of your main preview + manager + apply logic remains unchanged)

                

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

        # For preview size, we use the first page (consistent with the rest of the UI)
        first = pdf.get_page(0)
        # pdfium handles rotation automatically in rendered dimensions
        page_w_pt, page_h_pt = first.get_size()
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
                # TEXT STAMP — Boxed or Tiled
                text_rgb = HexColor(sp.text_color_hex).rgb()
                text_rgba = (int(text_rgb[0]*255), int(text_rgb[1]*255), int(text_rgb[2]*255), 255)
                try:
                    try:
                        font = ImageFont.truetype("arial.ttf", max(8, int(sp.font_size_pt * px_per_pt_y)))
                    except Exception:
                        font = ImageFont.load_default()
                except Exception:
                    font = ImageFont.load_default()

                if getattr(sp, "tiled", False):
                    # TILED MODE: repeat text across the entire page at tile_angle_deg
                    angle = getattr(sp, "tile_angle_deg", sp.rotation_deg)
                    dx_pt = mm_to_pt(getattr(sp, "tile_dx_mm", 60.0))
                    dy_pt = mm_to_pt(getattr(sp, "tile_dy_mm", 60.0))
                    dx_px = max(6, int(dx_pt * px_per_pt_x))
                    dy_px = max(6, int(dy_pt * px_per_pt_y))

                    txt_layer = Image.new("RGBA", (page.width, page.height), (0,0,0,0))

                    # Prepare a text sprite
                    temp = Image.new("RGBA", (1,1), (0,0,0,0))
                    tempd = ImageDraw.Draw(temp)
                    tw, th = tempd.textbbox((0,0), sp.text, font=font)[2:]

                    sprite_w = tw + 4
                    sprite_h = th + 4
                    base_sprite = Image.new("RGBA", (sprite_w, sprite_h), (0,0,0,0))
                    spr_d = ImageDraw.Draw(base_sprite)
                    spr_d.text((2,2), sp.text, fill=text_rgba, font=font)

                    rot_sprite = base_sprite.rotate(-angle, resample=Image.BICUBIC, expand=True)

                    # offset grid by (x_mm, y_mm)
                    off_x_px = int(mm_to_pt(sp.x_mm) * px_per_pt_x)
                    off_y_px = int(mm_to_pt(sp.y_mm) * px_per_pt_y)

                    for y in range(-page.height, page.height*2, dy_px):
                        for x in range(-page.width, page.width*2, dx_px):
                            px_ = x + off_x_px
                            py_ = y + off_y_px
                            txt_layer.alpha_composite(rot_sprite, (px_, py_))

                    overlay = Image.alpha_composite(overlay, txt_layer)

                else:
                    # BOX MODE: rectangle + border + centered text + rotation
                    fill_rgb = HexColor(sp.rect_fill_hex).rgb()
                    border_rgb = HexColor(sp.rect_border_hex).rgb()
                    alpha = int(round(255 * (1.0 - sp.rect_opacity)))

                    # Draw fill
                    draw.rectangle(
                        [l, t, r, b],
                        fill=(int(fill_rgb[0]*255), int(fill_rgb[1]*255), int(fill_rgb[2]*255), alpha)
                    )
                    # Border (scaled to pixels)
                    border_px = max(1, int(round(sp.border_width_pt * px_per_pt_x)))
                    draw.rectangle(
                        [l, t, r, b],
                        outline=(int(border_rgb[0]*255), int(border_rgb[1]*255), int(border_rgb[2]*255), alpha),
                        width=border_px
                    )

                    # Draw text in its own layer, then rotate so border stays crisp
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

            if sp.stamp_type == "image" and sp.image_bytes:
                # Rotate around center of the box for consistency
                cx = x_pt + w_pt/2
                cy = y_pt + h_pt/2
                can.translate(cx, cy)
                can.rotate(sp.rotation_deg)
                can.translate(-w_pt/2, -h_pt/2)
                can.drawImage(ImageReader(io.BytesIO(sp.image_bytes)), 0, 0, width=w_pt, height=h_pt, mask='auto')

            elif sp.stamp_type == "text":
                text_c = HexColor(sp.text_color_hex)
                can.setFillColor(text_c)
                font_name = pick_font_name(sp.bold, sp.italic)
                can.setFont(font_name, float(sp.font_size_pt))

                if sp.tiled:
                    # Full-page repeat tiles at tile_angle_deg; ignore box rect
                    dx_pt = mm_to_pt(sp.tile_dx_mm)
                    dy_pt = mm_to_pt(sp.tile_dy_mm)
                    angle = sp.tile_angle_deg
                    # offset grid origin using (x_mm, y_mm)
                    off_x = mm_to_pt(sp.x_mm)
                    off_y = mm_to_pt(sp.y_mm)

                    alpha = max(0.0, min(1.0, 1.0 - float(sp.rect_opacity)))
                    for y in range(-int(page_h_pt), int(page_h_pt*2), int(max(6, dy_pt))):
                        for x in range(-int(page_w_pt), int(page_w_pt*2), int(max(6, dx_pt))):
                            can.saveState()
                            can.translate(x + off_x, y + off_y)
                            can.rotate(angle)
                            ensure_alpha(can, fill_alpha=alpha, stroke_alpha=alpha)
                            can.drawString(0, 0, sp.text or "")
                            can.restoreState()

                else:
                    # BOX MODE: draw rect + border + centered text inside the box (with rotation)
                    cx = x_pt + w_pt/2
                    cy = y_pt + h_pt/2
                    can.translate(cx, cy)
                    can.rotate(sp.rotation_deg)
                    can.translate(-w_pt/2, -h_pt/2)

                    fill = HexColor(sp.rect_fill_hex)
                    border = HexColor(sp.rect_border_hex)
                    can.setLineWidth(sp.border_width_pt)
                    can.setStrokeColor(border)
                    can.setFillColor(fill)
                    alpha = max(0.0, min(1.0, 1.0 - float(sp.rect_opacity)))
                    ensure_alpha(can, fill_alpha=alpha, stroke_alpha=alpha)
                    can.rect(0, 0, w_pt, h_pt, stroke=1, fill=1)

                    # Center text within padded box
                    can.setFillColor(text_c)
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
    # MAIN LAYOUT — Preview (left) and Right Control Panel (with Stamp Manager)
    main_col, right_col = st.columns([0.62, 0.38], gap="large")

    # RIGHT CONTROL PANEL — Multi-Stamp Manager + form to edit SELECTED stamp + Security expander + Apply
    with right_col:
        st.header("Stamp Manager")
        if not st.session_state.stamps:
            st.info("Add a stamp from the left sidebar to edit it here.")
            apply_now = False
        else:
            # selection model
            labels = []
            for i, s in enumerate(st.session_state.stamps):
                kind = "IMG" if s.stamp_type == "image" else "TXT"
                desc = (s.text[:18] + "…") if (s.stamp_type == "text" and s.text and len(s.text) > 18) else (s.text or "")
                labels.append(f"#{i+1} [{kind}] p{s.page_from}-{s.page_to} {desc}")
            default_index = st.session_state.selected_stamp_index
            if default_index is None or default_index >= len(st.session_state.stamps):
                default_index = len(st.session_state.stamps) - 1
            selected = st.selectbox("Select a stamp to edit", options=list(range(len(labels))), format_func=lambda i: labels[i], index=default_index)
            st.session_state.selected_stamp_index = selected

            # Reorder / duplicate / delete
            cact1, cact2, cact3, cact4 = st.columns(4)
            with cact1:
                move_up = st.button("⬆ Up", use_container_width=True, disabled=(selected == 0), key="up_btn")
            with cact2:
                move_down = st.button("⬇ Down", use_container_width=True, disabled=(selected >= len(st.session_state.stamps)-1), key="down_btn")
            with cact3:
                dup = st.button("🧬 Duplicate", use_container_width=True, key="dup_btn")
            with cact4:
                del_req = st.button("🗑 Delete", use_container_width=True, key="del_btn")

            # Delete confirmation using session_state
            if "delete_pending" not in st.session_state:
                st.session_state.delete_pending = None
            if del_req:
                st.session_state.delete_pending = selected

            if st.session_state.delete_pending is not None:
                st.warning(f"⚠ Are you sure you want to delete stamp #{st.session_state.delete_pending+1}? This action cannot be undone.")
                cdel1, cdel2 = st.columns(2)
                with cdel1:
                    confirm_delete = st.button("✅ Yes, Delete", key="confirm_delete")
                with cdel2:
                    cancel_delete = st.button("❌ Cancel", key="cancel_delete")

                if confirm_delete:
                    idx_to_del = st.session_state.delete_pending
                    stamps = st.session_state.stamps
                    stamps.pop(idx_to_del)
                    st.session_state.stamps = stamps
                    st.session_state.delete_pending = None
                    if len(stamps) == 0:
                        st.session_state.selected_stamp_index = None
                    else:
                        st.session_state.selected_stamp_index = max(0, idx_to_del - 1)
                    st.session_state.preview_update_requested = True
                    st.success("Stamp deleted successfully.")
                    st.rerun()

                if cancel_delete:
                    st.session_state.delete_pending = None
                    st.info("Deletion canceled.")

            # Handle reorder / duplicate
            if move_up:
                stamps = st.session_state.stamps
                stamps[selected-1], stamps[selected] = stamps[selected], stamps[selected-1]
                st.session_state.stamps = stamps
                st.session_state.selected_stamp_index = selected-1
                st.session_state.preview_update_requested = True
                st.rerun()

            if move_down:
                stamps = st.session_state.stamps
                stamps[selected+1], stamps[selected] = stamps[selected], stamps[selected+1]
                st.session_state.stamps = stamps
                st.session_state.selected_stamp_index = selected+1
                st.session_state.preview_update_requested = True
                st.rerun()

            if dup:
                stamps = st.session_state.stamps
                clone = copy.deepcopy(stamps[selected])
                stamps.insert(selected+1, clone)
                st.session_state.stamps = stamps
                st.session_state.selected_stamp_index = selected+1
                st.session_state.preview_update_requested = True
                st.success("Stamp duplicated.")
                st.rerun()

            st.markdown("---")

            # Editor for the SELECTED stamp
            sidx = st.session_state.selected_stamp_index
            editing = st.session_state.stamps[sidx]
            st.subheader("Edit Selected Stamp (apply on Enter)")

            with st.form(key=f"selected_stamp_editor_{sidx}", clear_on_submit=False):
                st.caption("Changes apply when you press **Enter** or click **Update Preview**.")

                st.write(f"Editing: **#{sidx+1}** — **{editing.stamp_type.upper()}**")

                st.subheader("Page Range (this stamp only)")
                npages = num_pages if num_pages > 0 else 1
                cpg1, cpg2 = st.columns(2)
                with cpg1:
                    page_from = st.number_input("From page", 1, npages, min(editing.page_from, npages), key=f"pg_from_{sidx}")
                with cpg2:
                    page_to = st.number_input("To page", 1, npages, max(min(editing.page_to, npages), page_from), key=f"pg_to_{sidx}")

                st.subheader("Geometry")
                cg1, cg2 = st.columns(2)
                with cg1:
                    x_mm = st.number_input("X (mm)", 0.0, 5000.0, editing.x_mm, 1.0, key=f"x_{sidx}")
                    w_mm = st.number_input("Width (mm)", 5.0, 5000.0, editing.w_mm, 1.0, key=f"w_{sidx}")
                with cg2:
                    y_mm = st.number_input("Y (mm)", 0.0, 5000.0, editing.y_mm, 1.0, key=f"y_{sidx}")
                    h_mm = st.number_input("Height (mm)", 5.0, 5000.0, editing.h_mm, 1.0, key=f"h_{sidx}")
                rotation = st.slider(
                    "Rotation (°)",
                    -180.0, 180.0,
                    float(editing.rotation_deg),
                    1.0,
                    key=f"rot_{sidx}"
                )

                if editing.stamp_type == "image":
                    up2 = st.file_uploader("Replace image (optional)", type=["png", "jpg", "jpeg"], key=f"replace_img_{sidx}")
                else:
                    st.subheader("Text & Style")
                    cx1, cx2, cx3 = st.columns(3)
                    with cx1: bold = st.checkbox("Bold", value=editing.bold, key=f"bold_{sidx}")
                    with cx2: italic = st.checkbox("Italic", value=editing.italic, key=f"italic_{sidx}")
                    with cx3: font_size_pt = st.number_input("Font size (pt)", 8, 200, editing.font_size_pt, key=f"fs_{sidx}")

                    text_val = st.text_input("Text", value=editing.text, key=f"text_{sidx}")

                    cx4, cx5 = st.columns(2)
                    with cx4:
                        rect_fill_hex = st.color_picker("Rect fill", value=editing.rect_fill_hex, key=f"fill_{sidx}")
                        rect_opacity = st.slider("Rect transparency (0→1)", 0.0, 1.0, editing.rect_opacity, 0.05, key=f"opc_{sidx}")
                    with cx5:
                        rect_border_hex = st.color_picker("Rect border", value=editing.rect_border_hex, key=f"bor_{sidx}")
                        border_width_pt = st.number_input("Border width (pt)", 0.0, 12.0, editing.border_width_pt, 0.5, key=f"bw_{sidx}")

                    text_color_hex = st.color_picker("Text color", value=editing.text_color_hex, key=f"txtc_{sidx}")
                    padding_mm = st.number_input("Padding (mm)", 0.0, 50.0, editing.padding_mm, 0.5, key=f"pad_{sidx}")

                    st.subheader("Tiled Watermark (Full Page)")
                    last_tiled = st.checkbox("Enable tiled mode", value=editing.tiled, key=f"tiled_{sidx}")
                    tc1, tc2 = st.columns(2)
                    with tc1:
                        tile_dx_mm = st.number_input("Tile spacing X (mm)", 10.0, 500.0, editing.tile_dx_mm, 1.0, key=f"dx_{sidx}")
                    with tc2:
                        tile_dy_mm = st.number_input("Tile spacing Y (mm)", 10.0, 500.0, editing.tile_dy_mm, 1.0, key=f"dy_{sidx}")
                    tile_angle_deg = st.slider("Tile angle (°)", -180.0, 180.0, editing.tile_angle_deg, 1.0, key=f"ang_{sidx}")

                submit = st.form_submit_button("🔄 Update Preview", use_container_width=True)

            if submit:
                st.session_state.preview_update_requested = True
                editing.page_from = page_from
                editing.page_to = page_to
                editing.x_mm = x_mm; editing.y_mm = y_mm; editing.w_mm = w_mm; editing.h_mm = h_mm
                editing.rotation_deg = rotation
                if editing.stamp_type == "image":
                    if 'up2' in locals() and up2 is not None:
                        editing.image_bytes = up2.read()
                else:
                    editing.bold = bold; editing.italic = italic; editing.font_size_pt = font_size_pt
                    editing.text = text_val
                    editing.rect_fill_hex = rect_fill_hex
                    editing.rect_opacity = rect_opacity
                    editing.rect_border_hex = rect_border_hex
                    editing.border_width_pt = border_width_pt
                    editing.text_color_hex = text_color_hex
                    editing.padding_mm = padding_mm
                    editing.tiled = last_tiled
                    editing.tile_dx_mm = tile_dx_mm
                    editing.tile_dy_mm = tile_dy_mm
                    editing.tile_angle_deg = tile_angle_deg

                st.session_state.stamps[sidx] = editing
                st.rerun()

        st.markdown("---")

        # 🔐 SECURITY OPTIONS (expander, optional)
        # 🔐 SECURITY OPTIONS (expander, optional)
        with st.expander("🔐 PDF Security Options (optional)", expanded=False):
            st.session_state.sec_enabled = st.checkbox(
                "Enable password protection",
                value=st.session_state.sec_enabled,
                key="sec_enable"
            )

            if st.session_state.sec_enabled:
                csec1, csec2 = st.columns(2)
                with csec1:
                    st.session_state.sec_show_user = st.checkbox("👁 Show user password", value=st.session_state.sec_show_user, key="sec_show_user_ck")
                    st.session_state.sec_user_pw = st.text_input(
                        "User Password (to open PDF)",
                        value=st.session_state.sec_user_pw,
                        type=("default" if st.session_state.sec_show_user else "password"),
                        key="sec_user_input"
                    )
                with csec2:
                    st.session_state.sec_show_owner = st.checkbox("👁 Show owner password", value=st.session_state.sec_show_owner, key="sec_show_owner_ck")
                    st.session_state.sec_owner_pw = st.text_input(
                        "Owner Password (to change/remove protection)",
                        value=st.session_state.sec_owner_pw,
                        type=("default" if st.session_state.sec_show_owner else "password"),
                        key="sec_owner_input"
                    )

                st.markdown("### Restrict PDF Actions")
                st.session_state.sec_disable_print = st.checkbox("🖨️ Disable printing", True)
                st.session_state.sec_disable_copy = st.checkbox("📋 Disable text copying", True)
                st.session_state.sec_disable_modify = st.checkbox("✏️ Disable modifications", True)
                st.session_state.sec_disable_annotate = st.checkbox("💬 Disable annotations/comments", True)
                st.session_state.sec_disable_formfill = st.checkbox("📝 Disable form filling", True)
                st.session_state.sec_disable_accessibility = st.checkbox("♿ Disable accessibility extract", True)

                if not st.session_state.sec_user_pw or not st.session_state.sec_owner_pw:
                    st.info("Enter both passwords to enable protection.")
                elif st.session_state.sec_user_pw == st.session_state.sec_owner_pw:
                    st.warning("User and Owner passwords must be different.")

        st.markdown("---")
        st.header("📤 Export Options")
        username = st.session_state.get("current_user", "Unknown")
        default_dl = os.path.join(os.path.expanduser("~"), "Downloads", "stamped_output.pdf")
        
        db_export_path = db.get_setting(username, "export_path", default_dl)
        
        if "export_path_input" not in st.session_state:
            st.session_state.export_path_input = db_export_path

        if "show_browse_primary" not in ss: ss.show_browse_primary = False
        if ss.show_browse_primary:
            with st.container(border=True):
                new_path = folder_picker_ui("prim", st.session_state.export_path_input)
                if new_path != st.session_state.export_path_input:
                    st.session_state.export_path_input = new_path
                    st.rerun()
                if st.button("Close Browser", key="close_prim"):
                    ss.show_browse_primary = False
                    st.rerun()
            
        c_path, c_browse, c_save = st.columns([0.6, 0.2, 0.2])
        with c_path:
            export_path_val = st.text_input("Local Save Directory", key="export_path_input")
        
        with c_browse:
            # New: Custom Filename field (defaulting to the auto-generated one)
            c_default = ss.get("custom_filename", "stamped_output.pdf")
            custom_filename = st.text_input("Export Filename", value=c_default, key="custom_filename_input")
            
            # Clean up the filename
            if custom_filename and not custom_filename.lower().endswith(".pdf"):
                custom_filename += ".pdf"
            ss.custom_filename = custom_filename
            
            st.write("") # Padding for alignment
            if st.button("📂 Browse", use_container_width=True, key="browse_prim_btn"):
                ss.show_browse_primary = not ss.show_browse_primary
                st.rerun()
                    
        with c_save:
            st.write("")
            st.write("")
            if st.button("💾 Save to DB", use_container_width=True, key="save_primary"):
                db.set_setting(username, "export_path", export_path_val)
                st.success("Default path saved!")
                
        st.session_state.export_path = export_path_val

        db_ro_export_path = db.get_setting(username, "ro_export_path", "")
        
        if "ro_export_path_input" not in st.session_state:
            st.session_state.ro_export_path_input = db_ro_export_path

        if "show_browse_ro" not in ss: ss.show_browse_ro = False
        if ss.show_browse_ro:
            # Default to primary if empty
            base_ro = st.session_state.ro_export_path_input or st.session_state.export_path_input or os.path.expanduser("~")
            with st.container(border=True):
                new_path_ro = folder_picker_ui("ro_p", base_ro)
                if new_path_ro != base_ro:
                    st.session_state.ro_export_path_input = new_path_ro
                    st.rerun()
                if st.button("Close Browser", key="close_ro"):
                    ss.show_browse_ro = False
                    st.rerun()
            
        c_ro_path, c_ro_browse, c_ro_save = st.columns([0.6, 0.2, 0.2])
        with c_ro_path:
            ro_export_path_val = st.text_input("Read-Only Save Directory (Optional)", key="ro_export_path_input")
        
        with c_ro_browse:
            st.write("") # Padding for alignment
            st.write("")
            if st.button("📂 Browse", key="ro_browse_btn", use_container_width=True):
                ss.show_browse_ro = not ss.show_browse_ro
                st.rerun()
            
        with c_ro_save:
            st.write("")
            st.write("")
            if st.button("💾 Save to DB", key="ro_save", use_container_width=True):
                db.set_setting(username, "ro_export_path", ro_export_path_val)
                st.success("Read-Only path saved!")
                
        st.session_state.ro_export_path = ro_export_path_val

        # Apply button (explicit action, visible regardless of stamps)
        btn_label = "🚀 Apply Changes (Stamps / Security / Export)" if ss.stamps else "🚀 Apply Settings (Security / Export)"
        apply_now = st.button(btn_label, use_container_width=True, key="apply_btn")

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
                    if total_preview_pages > 1:
                        # normal slider if multiple pages
                        st.session_state.preview_page_index = (
                            st.slider(
                                "Preview page",
                                1,
                                total_preview_pages,
                                st.session_state.preview_page_index + 1,
                                1
                            ) - 1
                        )
                    else:
                        # single-page PDF: avoid error and still preview it
                        st.session_state.preview_page_index = 0
                        st.caption("📄 This PDF has only one page. Showing the single preview.")            
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
    # APPLY — merge overlays by page, honoring each stamp's page range + optional encryption
    if apply_now:
        if not st.session_state.pdf_bytes:
            st.error("Please upload a PDF.")
        else:
            # Validate security inputs if enabled
            if st.session_state.sec_enabled:
                if not st.session_state.sec_user_pw or not st.session_state.sec_owner_pw:
                    st.error("Password protection is enabled. Please enter both User and Owner passwords.")
                    st.stop()
                if st.session_state.sec_user_pw == st.session_state.sec_owner_pw:
                    st.error("User and Owner passwords must be different.")
                    st.stop()
            
            # Optional Digital Signature (if enabled in sidebar)
            if ss.get("auto_sign", False):
                username = st.session_state.current_user or "Unknown User"
                # use dynamic num_pages if possible
                np = num_pages if 'num_pages' in locals() and num_pages > 0 else 1
                
                # Check if we already have a signature stamp to avoid duplicates
                has_sig = any("Digitally signed" in s.text for s in ss.stamps if s.stamp_type == "text")
                if not has_sig:
                    default_w = 60.0
                    default_h = 20.0
                    default_padding = 5.0
                    
                    # Estimate based on current page_w_pt (sidebar value)
                    w_mm_page = (page_w_pt / mm) if 'page_w_pt' in locals() else 210.0
                    default_x = w_mm_page - default_w - default_padding
                    default_y = default_padding
                    
                    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    sig_text = f"Digitally signed by {username}\nDate: {now}"

                    ss.stamps.append(
                        Stamp(
                            stamp_type="text",
                            x_mm=default_x,
                            y_mm=default_y,
                            w_mm=default_w,
                            h_mm=default_h,
                            rotation_deg=0,
                            page_from=1,
                            page_to=np,
                            text=sig_text,
                            font_size_pt=10,
                            bold=False,
                            italic=False,
                            rect_fill_hex="#FFFFFF",
                            rect_border_hex="#000000",
                            text_color_hex="#000000",
                            rect_opacity=0.0,
                            border_width_pt=0.5,
                            padding_mm=2.0,
                            tiled=False
                        )
                    )
                    st.success("✅ Digital signature added successfully!")
                    st.session_state.selected_stamp_index = len(ss.stamps) - 1
            
            # Start Processing

            with st.spinner("Applying stamps to PDF..."):
                reader = PdfReader(io.BytesIO(st.session_state.pdf_bytes))
                writer = PdfWriter()

                # Add pages + overlays
                n = len(reader.pages)
                for i, page in enumerate(reader.pages):
                    curr_w, curr_h = get_page_size_pt(page)
                    overlay_reader = build_overlay_pdf_for_page(st.session_state.stamps, i, curr_w, curr_h)
                    if overlay_reader:
                        page.merge_page(overlay_reader.pages[0])
                    writer.add_page(page)

                # Optional encryption (maximum lockdown)
                if st.session_state.sec_enabled:
                    # Default: everything allowed (base = -4)
                    permissions = 0xFFFFFFFC  # (-4) in 32-bit signed form

                    # Deny actions based on toggles
                    if st.session_state.sec_disable_print:
                        permissions &= ~0b100            # disable printing
                    if st.session_state.sec_disable_modify:
                        permissions &= ~0b10000          # disable document modification
                    if st.session_state.sec_disable_copy:
                        permissions &= ~0b100000         # disable text copying/extract
                    if st.session_state.sec_disable_annotate:
                        permissions &= ~0b1000000        # disable annotations/comments
                    if st.session_state.sec_disable_formfill:
                        permissions &= ~0b1000000000     # disable form filling
                    if st.session_state.sec_disable_accessibility:
                        permissions &= ~0b10000000000    # disable accessibility extract (e.g. screen readers)

                    # Ensure value fits signed 32-bit range
                    if permissions > 0x7FFFFFFF:
                        permissions -= 0x100000000

                    writer.encrypt(
                        user_password=st.session_state.sec_user_pw,
                        owner_password=st.session_state.sec_owner_pw,
                        permissions_flag=permissions,
                        use_128bit=True
                    )

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    writer.write(tmp)
                    out_path = tmp.name

            with open(out_path, "rb") as f:
                pdf_data = f.read()
                
            # Save directly to local path
            try:
                def safe_save(p_raw, data, default_name):
                    if not p_raw or not p_raw.strip():
                        return None
                    
                    p = os.path.normpath(os.path.expanduser(p_raw.strip()))
                    
                    # If it's a directory, append default filename
                    if os.path.isdir(p):
                        p = os.path.join(p, default_name)
                    
                    p_dir = os.path.dirname(p)
                    if p_dir and not os.path.exists(p_dir):
                        try:
                            os.makedirs(p_dir, exist_ok=True)
                        except Exception as e:
                            if "[WinError 1326]" in str(e):
                                raise Exception(f"Network Share Access Error: The share '{p_dir}' requires a username and password. Try mapping it to a drive letter (e.g. Z:\\) in Windows first.")
                            raise Exception(f"Could not create directory '{p_dir}': {e}")
                    
                    # Final check for write access to the directory
                    target_dir = p_dir if p_dir else "."
                    if not os.access(target_dir, os.W_OK):
                        raise Exception(f"Access Denied: The directory '{os.path.abspath(target_dir)}' is not writable. Please choose another location.")
                    
                    with open(p, "wb") as f_out:
                        f_out.write(data)
                    return p

                # 1. Primary Export
                save_path_raw = st.session_state.get("export_path", "")
                c_name = st.session_state.get("custom_filename", "stamped_output.pdf")
                final_path = safe_save(save_path_raw, pdf_data, c_name)
                if final_path:
                    st.success(f"✅ PDF successfully saved directly to: {final_path}")
                    
                # 2. Read-Only Export (Dynamic encryption)
                ro_save_path_raw = st.session_state.get("ro_export_path", "")
                if ro_save_path_raw and ro_save_path_raw.strip():
                    ro_reader = PdfReader(out_path)
                    ro_writer = PdfWriter()
                    for page in ro_reader.pages:
                        ro_writer.add_page(page)
                        
                    ro_permissions = 0xFFFFFFFC # base (-4)
                    ro_permissions &= ~0b100            # disable printing
                    ro_permissions &= ~0b10000          # disable modify
                    ro_permissions &= ~0b100000         # disable copy
                    ro_permissions &= ~0b1000000        # disable annotations
                    ro_permissions &= ~0b1000000000     # disable form filling
                    ro_permissions &= ~0b10000000000    # disable accessibility
                    if ro_permissions > 0x7FFFFFFF:
                        ro_permissions -= 0x100000000
                        
                    ro_writer.encrypt(user_password="", owner_password=str(uuid.uuid4()), permissions_flag=ro_permissions, use_128bit=True)
                    
                    # Write to buffer then save
                    ro_buf = io.BytesIO()
                    ro_writer.write(ro_buf)
                    
                    ro_name = c_name.replace(".pdf", "_readonly.pdf")
                    final_ro_path = safe_save(ro_save_path_raw, ro_buf.getvalue(), ro_name)
                    if final_ro_path:
                        st.success(f"✅ Read-Only PDF securely locked and saved to: {final_ro_path}")
            except Exception as e:
                st.error(f"❌ Failed to save to local path: {e}")
                
            fname = st.session_state.get("custom_filename", "stamped_output.pdf")
            st.download_button("📥 Download stamped PDF via Browser", pdf_data, file_name=fname, mime="application/pdf")
            if st.session_state.sec_enabled:
                st.success("✅ Done! Stamps applied and PDF encrypted.")
            else:
                st.success("✅ Done! Stamps applied.")




def merge_pdf_tool():
    st.header("🔗 Merge PDFs")
    files = st.file_uploader("Upload PDFs to merge", type="pdf", accept_multiple_files=True, key="merge_upload")

    if files and st.button("Merge PDFs"):
        merger = PdfMerger()
        merged_files = []
        skipped_files = []

        for f in files:
            try:
                file_bytes = io.BytesIO(f.read())
                merger.append(file_bytes)
                merged_files.append(f.name)
            except FileNotDecryptedError:
                skipped_files.append(f.name)
                st.error(f"🔒 The file **{f.name}** is encrypted — could not merge it.")
            except Exception as e:
                skipped_files.append(f.name)
                st.error(f"❌ Error merging {f.name}: {e}")

        # ✅ Only show success if at least one file was merged
        if merged_files:
            out = io.BytesIO()
            merger.write(out)
            merger.close()
            c_name = st.session_state.get("custom_filename", "merged.pdf") # Use a default for merge tool if not set
            st.download_button(
                label=f"💾 Download {c_name}",
                data=out.getvalue(),
                file_name=c_name,
                mime="application/pdf",
                use_container_width=True
            )
            st.success(f"✅ Successfully merged {len(merged_files)} PDF(s).")

            if skipped_files:
                st.warning("⚠️ These files were skipped:")
                for name in skipped_files:
                    st.write(f"- {name}")
        else:
            st.warning("⚠️ No PDFs were merged (all were encrypted or invalid).")

def split_pdf_tool():
    st.header("✂️ Split PDF by Pages")
    file = st.file_uploader("Upload PDF to split", type="pdf", key="split_upload")

    if file:
        try:
            reader = PdfReader(io.BytesIO(file.read()))
            if reader.is_encrypted:
                st.error(f"🔒 The file **{file.name}** is encrypted — cannot split it.")
                return

            num_pages = len(reader.pages)
            st.info(f"PDF has {num_pages} pages.")
            start = st.number_input("From page", 1, num_pages, 1)
            end = st.number_input("To page", 1, num_pages, num_pages)

            if st.button("Split PDF"):
                writer = PdfWriter()
                for i in range(start - 1, end):
                    writer.add_page(reader.pages[i])

                out = io.BytesIO()
                writer.write(out)
                st.download_button("📥 Download split PDF", out.getvalue(),
                                   f"split_{start}-{end}.pdf", "application/pdf")
                st.success(f"✅ Extracted pages {start}–{end}.")
        except FileNotDecryptedError:
            st.error("🔒 This file is encrypted — cannot split.")
        except Exception as e:
            st.error(f"❌ Failed to process file: {e}")

def extract_pages_tool():
    st.header("📄 Extract Specific Pages")
    file = st.file_uploader("Upload PDF", type="pdf", key="extract_pages_upload")
    pages = st.text_input("Enter pages (e.g., 1,3,5-7)")

    if file and st.button("Extract"):
        try:
            reader = PdfReader(io.BytesIO(file.read()))
            if reader.is_encrypted:
                st.error(f"🔒 The file **{file.name}** is encrypted — cannot extract pages.")
                return

            writer = PdfWriter()
            indices = []
            for part in pages.split(','):
                if '-' in part:
                    a, b = map(int, part.split('-'))
                    indices.extend(range(a - 1, b))
                else:
                    indices.append(int(part) - 1)

            for i in indices:
                if 0 <= i < len(reader.pages):
                    writer.add_page(reader.pages[i])

            out = io.BytesIO()
            writer.write(out)
            st.download_button("📥 Download extracted PDF", out.getvalue(),
                               "extracted.pdf", "application/pdf")
            st.success("✅ Pages extracted successfully!")
        except FileNotDecryptedError:
            st.error("🔒 File is encrypted — cannot extract pages.")
        except Exception as e:
            st.error(f"❌ Failed to extract pages: {e}")

def extract_text_tool():
    st.header("🔍 Extract Text from PDF")
    file = st.file_uploader("Upload PDF", type="pdf", key="extract_text_upload")

    if file and st.button("Extract Text"):
        try:
            reader = PdfReader(io.BytesIO(file.read()))
            if reader.is_encrypted:
                st.error(f"🔒 The file **{file.name}** is encrypted — cannot extract text.")
                return

            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            st.download_button("📄 Download extracted text", text, "extracted.txt", "text/plain")
            st.text_area("Preview extracted text", text[:3000])
        except FileNotDecryptedError:
            st.error("🔒 File is encrypted — cannot extract text.")
        except Exception as e:
            st.error(f"❌ Failed to extract text: {e}")


def convert_to_images_tool():
    st.header("🖼 Convert PDF Pages to Images")
    file = st.file_uploader("Upload PDF", type="pdf", key="convert_image_upload")

    if file and st.button("Convert"):
        try:
            pdf = pdfium.PdfDocument(io.BytesIO(file.read()))
            for i, page in enumerate(pdf):
                img = page.render(scale=2).to_pil()
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                st.image(img, caption=f"Page {i+1}")
                st.download_button(f"⬇ Download Page {i+1}",
                                   buf.getvalue(), f"page_{i+1}.png", "image/png")
            pdf.close()
            st.success("✅ Conversion complete!")
        except Exception as e:
            if "encrypted" in str(e).lower():
                st.error(f"🔒 The file **{file.name}** is encrypted — cannot convert to images.")
            else:
                st.error(f"❌ Failed to convert: {e}")


def compress_pdf_tool():
    st.header("📦 Compress PDF")
    file = st.file_uploader("Upload PDF to compress", type="pdf", key="compress_upload")

    quality = st.slider("Compression quality (higher = larger file)", 20, 95, 70)
    scale = st.slider("Render scale (affects resolution)", 0.5, 2.0, 1.0, 0.1)

    if file and st.button("Compress"):
        try:
            # --- Check for encryption early ---
            reader = PdfReader(io.BytesIO(file.read()))
            if reader.is_encrypted:
                st.error(f"🔒 The file **{file.name}** is encrypted — cannot compress it.")
                return

            # Reset pointer and write to temporary file
            file.seek(0)
            input_bytes = file.read()
            tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            tmp_in.write(input_bytes)
            tmp_in.close()

            # --- Try Ghostscript first ---
            gs_found = None
            for cmd in ["gs", "gswin64c", "gswin32c"]:
                try:
                    subprocess.run([cmd, "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    gs_found = cmd
                    break
                except Exception:
                    continue

            if gs_found:
                st.info(f"✅ Ghostscript found: **{gs_found}**")
                tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                cmd = [
                    gs_found,
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.4",
                    "-dPDFSETTINGS=/ebook",
                    "-dNOPAUSE", "-dQUIET", "-dBATCH",
                    f"-sOutputFile={tmp_out.name}",
                    tmp_in.name,
                ]
                try:
                    subprocess.run(cmd, check=True)
                    with open(tmp_out.name, "rb") as f:
                        st.download_button(
                            "📥 Download Compressed PDF",
                            f.read(),
                            "compressed.pdf",
                            "application/pdf",
                        )
                    st.success("✅ Compression successful via Ghostscript.")
                    return
                except Exception as e:
                    st.warning(f"⚠️ Processing...")

            # --- Fallback: Python-only rebuild ---
            try:
                st.warning("⚙️ Processing...")
                pdf = pdfium.PdfDocument(tmp_in.name)
                output_buffer = io.BytesIO()
                c = canvas.Canvas(output_buffer)

                for i, page in enumerate(pdf):
                    pil_image = page.render(scale=scale).to_pil()

                    # Compress image
                    img_bytes = io.BytesIO()
                    pil_image.save(img_bytes, format="JPEG", quality=quality)
                    img_bytes.seek(0)

                    # Draw compressed image full-page
                    w, h = page.get_width(), page.get_height()
                    c.setPageSize((w, h))
                    c.drawImage(ImageReader(img_bytes), 0, 0, width=w, height=h)
                    c.showPage()

                c.save()
                pdf.close()

                output_buffer.seek(0)
                st.download_button(
                    "📥 Download Compressed PDF",
                    output_buffer.getvalue(),
                    "compressed_fallback.pdf",
                    "application/pdf",
                )
                st.success("✅ Fallback compression done successfully.")
            except Exception as e:
                if "encrypted" in str(e).lower():
                    st.error(f"🔒 The file **{file.name}** is encrypted — cannot compress it.")
                else:
                    st.error(f"❌ Compression failed: {e}")

        except FileNotDecryptedError:
            st.error(f"🔒 The file **{file.name}** is encrypted — cannot compress it.")
        except Exception as e:
            st.error(f"❌ Error reading file: {e}")



with tabs[0]:
    # 🔹 Your existing Watermark / Stamp logic
    run_watermark_tool()

with tabs[1]:
    merge_pdf_tool()

with tabs[2]:
    split_pdf_tool()

with tabs[3]:
    extract_pages_tool()

with tabs[4]:
    extract_text_tool()

with tabs[5]:
    convert_to_images_tool()

with tabs[6]:
    compress_pdf_tool()