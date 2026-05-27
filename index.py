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
import hashlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

import database as db
import streamlit as st
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageEnhance
import difflib
try:
    import pytesseract
    _TESS_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.name == "nt" and os.path.isfile(_TESS_WIN):
        pytesseract.pytesseract.tesseract_cmd = _TESS_WIN
    _HAS_OCR = True
    _OCR_ERR = ""
except Exception as _e:
    _HAS_OCR = False
    _OCR_ERR = str(_e)
from PyPDF2 import PdfReader, PdfWriter, PdfMerger
from PyPDF2.errors import FileNotDecryptedError
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader, simpleSplit
import pypdfium2 as pdfium
import streamlit.components.v1 as components
import pikepdf

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



# ─────────────────────────────────────────────────────────────────────────────
# 🔐 Access Control — Only authorized users can open the app

def _get_owner_pass() -> str:
    """Derive the owner password from the application secret (same logic as protected_pdf_tool)."""
    raw_secret = os.environ.get("APP_PDF_SECRET") or db.get_setting("__system__", "app_pdf_secret", "")
    if not raw_secret:
        raw_secret = uuid.uuid4().hex + uuid.uuid4().hex
        db.set_setting("__system__", "app_pdf_secret", raw_secret)
    return hashlib.sha256((raw_secret + ":owner").encode()).hexdigest()[:32]


def _render_protected_pdf(pdf_bytes: bytes):
    """Decrypt (if protected) and render a multi-page view-only PDF viewer."""
    owner_pass = _get_owner_pass()
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes), password=owner_pass) as pdf:
            plain_buf = io.BytesIO()
            pdf.save(plain_buf)
    except pikepdf.PasswordError:
        try:
            with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
                plain_buf = io.BytesIO()
                pdf.save(plain_buf)
        except pikepdf.PasswordError:
            st.error("❌ This PDF is password-protected and was not locked by this application.")
            return
    except Exception as e:
        st.error(f"❌ Could not open file: {e}")
        return

    plain_buf.seek(0)
    try:
        pdf_doc = pdfium.PdfDocument(plain_buf)
    except Exception as e:
        st.error(f"❌ Could not render PDF: {e}")
        return

    num_pages = len(pdf_doc)
    st.success(f"✅ Document opened — {num_pages} page(s)")

    # Render all pages to base64 PNGs (scale=1.5 balances quality vs. size)
    with st.spinner("Loading pages…"):
        pages_b64 = []
        img_w, img_h = 1, 1
        for i in range(num_pages):
            pg = pdf_doc[i]
            pil_img = pg.render(scale=1.5).to_pil()
            if i == 0:
                img_w, img_h = pil_img.size
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            pages_b64.append(base64.b64encode(buf.getvalue()).decode())
        pdf_doc.close()

    # Build a JS-safe array literal of all page data URIs
    pages_js = "[\n" + ",\n".join(f'  "{b}"' for b in pages_b64) + "\n]"

    protected_html = f"""
<style>
  html, body {{ margin: 0; padding: 0; background: #0e1117; font-family: sans-serif; }}

  .wrap {{
    position: relative;
    user-select: none; -webkit-user-select: none; -moz-user-select: none;
    line-height: 0;
  }}
  .wrap img {{
    width: 100%; display: block;
    -webkit-user-drag: none; user-drag: none; pointer-events: none;
  }}
  .overlay {{ position: absolute; inset: 0; background: transparent; z-index: 10; }}

  /* Navigation bar */
  .nav {{
    display: flex; align-items: center; justify-content: center;
    gap: 10px; padding: 8px 0; background: #0e1117;
  }}
  .nav button {{
    background: rgba(40,40,55,0.9); color: #ddd;
    border: 1px solid #555; border-radius: 6px;
    padding: 5px 16px; cursor: pointer; font-size: 14px;
    transition: background 0.15s;
  }}
  .nav button:hover {{ background: rgba(70,70,100,0.95); }}
  .nav button:disabled {{ opacity: 0.3; cursor: default; }}
  .nav .pageinfo {{ color: #aaa; font-size: 13px; min-width: 90px; text-align: center; }}

  /* Fullscreen toggle */
  #fs-btn {{
    position: fixed; bottom: 14px; right: 14px; z-index: 999;
    background: rgba(30,30,40,0.85); color: #ddd;
    border: 1px solid #555; border-radius: 6px;
    padding: 6px 14px; cursor: pointer; font-size: 14px;
    backdrop-filter: blur(4px); transition: background 0.15s;
  }}
  #fs-btn:hover {{ background: rgba(60,60,80,0.95); }}

  /* Fullscreen overrides */
  :fullscreen body, :-webkit-full-screen body {{
    display: flex; flex-direction: column; height: 100vh; overflow: hidden;
  }}
  :fullscreen .wrap, :-webkit-full-screen .wrap {{
    flex: 1; display: flex; align-items: center; justify-content: center;
    overflow: hidden;
  }}
  :fullscreen .wrap img, :-webkit-full-screen .wrap img {{
    width: auto; max-width: 100vw;
    max-height: calc(100vh - 56px);
    object-fit: contain;
  }}
  :fullscreen .overlay, :-webkit-full-screen .overlay {{ position: fixed; inset: 0; }}
  :fullscreen .nav, :-webkit-full-screen .nav {{
    flex-shrink: 0; background: rgba(14,17,23,0.92); width: 100%;
  }}
</style>

<div class="wrap">
  <img id="pdfimg" src="" draggable="false" />
  <div class="overlay" oncontextmenu="return false;"></div>
</div>
<div class="nav">
  <button id="prev-btn">&#9664; Prev</button>
  <span class="pageinfo" id="pageinfo"></span>
  <button id="next-btn">Next &#9654;</button>
</div>
<button id="fs-btn">&#x26F6; Full Screen</button>

<script>
  var PAGES = {pages_js};
  var IMG_W = {img_w}, IMG_H = {img_h};
  var cur = 0;

  var img     = document.getElementById('pdfimg');
  var prevBtn = document.getElementById('prev-btn');
  var nextBtn = document.getElementById('next-btn');
  var info    = document.getElementById('pageinfo');
  var fsBtn   = document.getElementById('fs-btn');

  function showPage(n) {{
    cur = Math.max(0, Math.min(n, PAGES.length - 1));
    img.src = 'data:image/png;base64,' + PAGES[cur];
    info.textContent = 'Page ' + (cur + 1) + ' / ' + PAGES.length;
    prevBtn.disabled = (cur === 0);
    nextBtn.disabled = (cur === PAGES.length - 1);
    sendHeight();
  }}

  function sendHeight() {{
    if (document.fullscreenElement || document.webkitFullscreenElement) return;
    var w = document.documentElement.offsetWidth || 800;
    var h = Math.round(IMG_H * w / IMG_W) + 56;
    window.parent.postMessage({{type: 'streamlit:setFrameHeight', height: h}}, '*');
  }}

  prevBtn.addEventListener('click', function() {{ showPage(cur - 1); }});
  nextBtn.addEventListener('click', function() {{ showPage(cur + 1); }});

  /* Keyboard: arrows navigate pages; other keys stay protected */
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown')  {{ showPage(cur + 1); return; }}
    if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')    {{ showPage(cur - 1); return; }}
    var ctrl = e.ctrlKey || e.metaKey, shift = e.shiftKey;
    if (ctrl && !shift && ['s','S','u','U','a','A','p','P'].includes(e.key)) {{ e.preventDefault(); }}
    if (ctrl && shift  && ['i','I','j','J','c','C'].includes(e.key))         {{ e.preventDefault(); }}
    if (e.key === 'F12') {{ e.preventDefault(); }}
  }});

  /* Fullscreen */
  function toggleFS() {{
    if (!document.fullscreenElement && !document.webkitFullscreenElement) {{
      (document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen)
        .call(document.documentElement);
    }} else {{
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    }}
  }}
  fsBtn.addEventListener('click', toggleFS);
  function onFSChange() {{
    var inFS = !!(document.fullscreenElement || document.webkitFullscreenElement);
    fsBtn.innerHTML = inFS ? '&#x2715; Exit Full Screen' : '&#x26F6; Full Screen';
    if (!inFS) setTimeout(sendHeight, 100);
  }}
  document.addEventListener('fullscreenchange', onFSChange);
  document.addEventListener('webkitfullscreenchange', onFSChange);

  /* Copy protection */
  document.addEventListener('contextmenu', function(e) {{ e.preventDefault(); }}, true);
  document.addEventListener('dragstart',   function(e) {{ e.preventDefault(); }});

  showPage(0);
  window.addEventListener('resize', function() {{ setTimeout(sendHeight, 50); }});
</script>
"""
    init_h = int(img_h / img_w * 1200) + 70
    components.html(protected_html, height=init_h)


def public_protected_viewer():
    """View-only protected PDF viewer — no login required."""
    st.subheader("🔑 View Protected Document")

    # Load admin-configured shared folder and whitelist
    shared_folder = db.get_setting("__system__", "shared_viewer_folder", "") or ""
    try:
        visible_files: list[str] = json.loads(
            db.get_setting("__system__", "shared_viewer_files", "[]") or "[]"
        )
    except (json.JSONDecodeError, TypeError):
        visible_files = []

    shared_pdfs: list[str] = []
    if shared_folder and os.path.isdir(shared_folder) and visible_files:
        # Only show files that exist on disk AND are in the admin whitelist
        shared_pdfs = sorted(
            f for f in visible_files
            if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(shared_folder, f))
        )

    # Mode toggle: show "Select" option only when folder is configured and has files
    if shared_pdfs:
        mode = st.radio(
            "Source",
            ["📂 Select from shared folder", "📤 Upload file"],
            horizontal=True,
            key="pub_view_mode",
        )
    else:
        mode = "📤 Upload file"

    pdf_bytes: bytes | None = None

    if mode == "📂 Select from shared folder":
        selected = st.selectbox("Select a document", shared_pdfs, key="pub_view_select")
        if selected:
            try:
                with open(os.path.join(shared_folder, selected), "rb") as fh:
                    pdf_bytes = fh.read()
            except Exception as e:
                st.error(f"❌ Could not read file: {e}")
    else:
        st.caption("Upload a PDF that was locked by this application to view it securely.")
        locked_file = st.file_uploader(
            "Upload protected PDF", type="pdf", key="pub_view_upload"
        )
        if locked_file:
            pdf_bytes = locked_file.read()

    if pdf_bytes:
        _render_protected_pdf(pdf_bytes)


if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.current_user = None
    st.session_state.is_admin = False
    
    # Auto-login if valid session token is in URL
    if "session" in st.query_params:
        session_token = st.query_params["session"]
        username = db.get_user_by_session(session_token)
        if username:
            st.session_state.authenticated = True
            st.session_state.current_user = username
            user_rec = db.get_user_by_username(username)
            if user_rec:
                st.session_state.is_admin = bool(user_rec['is_admin'])

def login_screen():
    st.title("🔐 Access Restricted")
    st.write("This application is private. Please log in to continue.")
    user = st.text_input("Username")
    pw = st.text_input("Password", type="password")
    col1, col2 = st.columns([0.7, 0.3])
    with col2:
        if st.button("Login", use_container_width=True):
            user_record = db.authenticate_user(user, pw)
            if user_record:
                st.session_state.authenticated = True
                st.session_state.current_user = user
                st.session_state.is_admin = bool(user_record['is_admin'])
                # Create and save token in query params so it survives refreshes
                token = db.create_session(user)
                st.query_params["session"] = token
                
                st.success(f"Welcome, {user}! ✅")
                st.rerun()
            else:
                st.error("❌ Incorrect username or password. Access denied.")

# ── Unauthenticated routing ───────────────────────────────────────────────────
if not st.session_state.authenticated:
    if st.session_state.get("_show_login", False):
        # ── Login page ────────────────────────────────────────────────────────
        if st.button("← Back to documents", key="back_to_viewer_btn"):
            st.session_state["_show_login"] = False
            st.rerun()
        login_screen()
    else:
        # ── Home / viewer page ────────────────────────────────────────────────
        col_title, col_login = st.columns([0.8, 0.2])
        with col_login:
            if st.button("🔐 Login", use_container_width=True, key="go_to_login_btn"):
                st.session_state["_show_login"] = True
                st.rerun()
        public_protected_viewer()
    st.stop()

# --- Refresh last seen ---
db.update_last_seen(st.session_state.current_user)

# --- Permission definitions ---
TAB_PERMISSIONS = [
    ("tab_watermark",        "🪶 Watermark / Stamp"),
    ("tab_merge",            "🔗 Merge PDFs"),
    ("tab_split",            "✂️ Split PDF"),
    ("tab_extract_pages",    "📄 Extract Pages"),
    ("tab_extract_text",     "🔍 Extract Text"),
    ("tab_convert_images",   "🖼 Convert to Images"),
    ("tab_compress",         "📦 Compress PDF"),
    ("tab_readonly",         "🔒 Read Only"),
    ("tab_protected_viewer", "🔑 Protected Viewer"),
    ("tab_compare",          "🔎 Compare PDFs"),
]
BTN_PERMISSIONS = [
    ("btn_apply_stamp",    "Apply Stamp / Watermark"),
    ("btn_save_local",     "Save PDF to local path"),
    ("btn_merge",          "Merge PDFs"),
    ("btn_split",          "Split PDF"),
    ("btn_extract_pages",  "Extract Pages"),
    ("btn_extract_text",   "Extract Text"),
    ("btn_convert_images", "Convert to Images"),
    ("btn_compress",       "Compress PDF"),
    ("btn_readonly_apply", "Apply Read-Only Lockdown"),
    ("btn_lock_pdf",       "Lock PDF (Protected Viewer)"),
    ("btn_view_protected", "View Protected PDF"),
]
ALL_PERMISSIONS = TAB_PERMISSIONS + BTN_PERMISSIONS

def can_do(perm_key, default=True):
    """Returns True if the current user has the given permission. Admins always pass."""
    if st.session_state.get("is_admin"):
        return True
    username = st.session_state.get("current_user", "")
    return db.get_permission(username, perm_key, default)

def perm_button(label, perm_key, **kwargs):
    """Renders a button that is auto-disabled when the user lacks permission."""
    if not can_do(perm_key):
        kwargs["disabled"] = True
        kwargs.setdefault("help", "🔒 You don't have permission for this action.")
    return st.button(label, **kwargs) and can_do(perm_key)

# --- Build tabs dynamically based on permissions ---
_ALL_TAB_DEFS = [
    ("🪶 Watermark / Stamp", "tab_watermark"),
    ("🔗 Merge PDFs",        "tab_merge"),
    ("✂️ Split PDF",         "tab_split"),
    ("📄 Extract Pages",     "tab_extract_pages"),
    ("🔍 Extract Text",      "tab_extract_text"),
    ("🖼 Convert to Images", "tab_convert_images"),
    ("📦 Compress PDF",      "tab_compress"),
    ("🔒 Read Only",         "tab_readonly"),
    ("🔑 Protected Viewer",  "tab_protected_viewer"),
    ("🔎 Compare PDFs",      "tab_compare"),
]
tab_titles     = []
_tab_perm_keys = []
for _title, _pkey in _ALL_TAB_DEFS:
    if can_do(_pkey):
        tab_titles.append(_title)
        _tab_perm_keys.append(_pkey)
if st.session_state.is_admin:
    tab_titles.append("🛡️ Admin")
    _tab_perm_keys.append("tab_admin")

tabs = st.tabs(tab_titles)

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

def get_page_size_pt(page) -> Tuple[float, float]:
    """Get actual width and height of a PDF page, taking rotation into account."""
    mb = page.mediabox
    w, h = float(mb.width), float(mb.height)
    # If the page is rotated by 90 or 270 degrees, swap width and height
    if page.get('/Rotate', 0) in [90, 270]:
        return h, w
    return w, h

def _render_topbar():
    """Render the shared top bar (user info, change password, logout) for all tabs."""
    col1, col2, col3 = st.columns([0.70, 0.17, 0.13])
    with col1:
        st.caption(f"👋 Logged in as: **{st.session_state.current_user}**")
    with col2:
        if st.button("🔑 Change Password", use_container_width=True, key="topbar_chpw_btn"):
            st.session_state["_show_chpw"] = not st.session_state.get("_show_chpw", False)
            st.rerun()
    with col3:
        if st.button("🚪 Logout", use_container_width=True, key="topbar_logout_btn"):
            user = st.session_state.current_user
            if user:
                db.set_setting(user, "session_token", "")
            st.session_state.authenticated = False
            st.session_state.current_user = None
            if "session" in st.query_params:
                del st.query_params["session"]
            st.rerun()

    if st.session_state.get("_show_chpw"):
        with st.container(border=True):
            st.markdown("##### Change Your Password")
            with st.form("chpw_form"):
                cur_pw   = st.text_input("Current password", type="password", key="chpw_cur")
                new_pw   = st.text_input("New password", type="password", key="chpw_new")
                new_pw2  = st.text_input("Confirm new password", type="password", key="chpw_new2")
                save_btn = st.form_submit_button("Save", use_container_width=True)

            if save_btn:
                uname = st.session_state.current_user
                if not db.authenticate_user(uname, cur_pw):
                    st.error("Current password is incorrect.")
                elif len(new_pw) < 4:
                    st.error("New password must be at least 4 characters.")
                elif new_pw != new_pw2:
                    st.error("New passwords do not match.")
                else:
                    db.change_password(uname, new_pw)
                    st.session_state["_show_chpw"] = False
                    st.success("✅ Password changed successfully.")
                    st.rerun()


def run_watermark_tool():


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
        rect_border_opacity: float = 0.0
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

        prev_auto_sign = ss.get("auto_sign", False)
        ss.auto_sign = st.checkbox("Add digital signature", value=prev_auto_sign)

        if ss.auto_sign:
            w_mm_page = page_w_pt / mm

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

            sig_action_val = st.text_area("Signature action label", value=db_sig_action, height=60)
            if sig_action_val != db_sig_action:
                db.set_setting(username, "default_sig_action", sig_action_val)

            sig_opacity = st.slider("Box Fill Transparency", 0.0, 1.0, 0.0, 0.05, key="sig_fill_opacity")
            sig_border_opacity = st.slider("Box Border Transparency", 0.0, 1.0, 0.0, 0.05, key="sig_border_opacity")

            # When checkbox is first checked, record the timestamp and add the stamp
            if not prev_auto_sign:
                ss["sig_sign_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if sig_action_val.strip():
                    final_sig_text = f"{sig_action_val.strip()} by {username}\nDate: {ss['sig_sign_time']}"
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
                            rect_border_opacity=sig_border_opacity,
                            border_width_pt=0.5,
                            padding_mm=2.0,
                            tiled=False
                        )
                    )
                    ss["sig_stamp_idx"] = len(ss.stamps) - 1
                    st.session_state.selected_stamp_index = ss["sig_stamp_idx"]
                    st.rerun()

        elif prev_auto_sign:
            # Checkbox was just unchecked — remove the signature stamp
            sig_idx = ss.get("sig_stamp_idx")
            if sig_idx is not None and sig_idx < len(ss.stamps):
                ss.stamps.pop(sig_idx)
            ss.pop("sig_stamp_idx", None)
            ss.pop("sig_sign_time", None)
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
        n_border_opacity = 0.0
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
        
        st.subheader("Box & Style")
        c4, c5 = st.columns(2)
        with c4:
            n_fill = st.color_picker("Rect fill", "#FFFFFF")
            n_opacity = st.slider("Rect fill transparency (0→1)", 0.0, 1.0, 0.0, 0.05)
        with c5:
            n_border = st.color_picker("Rect border", "#000000")
            n_border_opacity = st.slider("Rect border transparency (0→1)", 0.0, 1.0, 0.0, 0.05)
            n_bw = st.number_input("Border width (pt)", 0.0, 12.0, 1.0, 0.5)
        n_text_col = st.color_picker("Text color", "#000000")
        n_pad = st.number_input("Padding (mm)", 0.0, 50.0, 3.0, 0.5)

        if new_type == "text":
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
                        rect_opacity=n_opacity,
                        rect_border_opacity=n_border_opacity,
                        border_width_pt=n_bw,
                        padding_mm=n_pad,
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
            pdf = pdfium.PdfDocument(io.BytesIO(_strip_cropbox(pdf_bytes)))
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
                    # Draw fill (transparency)
                    fill_opacity = float(getattr(sp, "rect_opacity", 0.0))
                    fill_alpha = int(round(255 * (1.0 - fill_opacity)))
                    if fill_alpha > 0:
                        draw.rectangle(
                            [l, t, r, b],
                            fill=(int(fill_rgb[0]*255), int(fill_rgb[1]*255), int(fill_rgb[2]*255), fill_alpha)
                        )
                    
                    # Border (opacity)
                    border_opacity = float(getattr(sp, "rect_border_opacity", 0.0))
                    border_alpha = int(round(255 * (1.0 - border_opacity)))
                    border_px = int(round(sp.border_width_pt * px_per_pt_x))
                    
                    if border_alpha > 0 and border_px > 0:
                        draw.rectangle(
                            [l, t, r, b],
                            outline=(int(border_rgb[0]*255), int(border_rgb[1]*255), int(border_rgb[2]*255), border_alpha),
                            width=max(1, border_px)
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

            # 1. Coordinate Transform (Rotate around box center)
            cx, cy = x_pt + w_pt/2, y_pt + h_pt/2
            can.translate(cx, cy)
            can.rotate(sp.rotation_deg)
            can.translate(-w_pt/2, -h_pt/2)

            # 2. Draw Box (Rect + Border) - for both image and text in Box Mode
            # Tiled mode handles its own background/opacity differently
            is_tiled = (sp.stamp_type == "text" and sp.tiled)
            
            if not is_tiled:
                fill_alpha = max(0.0, min(1.0, 1.0 - float(sp.rect_opacity or 0.0)))
                border_alpha = max(0.0, min(1.0, 1.0 - float(sp.rect_border_opacity or 0.0)))
                
                if fill_alpha > 0 or border_alpha > 0:
                    can.saveState()
                    ensure_alpha(can, fill_alpha=fill_alpha, stroke_alpha=border_alpha)
                    can.setLineWidth(sp.border_width_pt)
                    can.setStrokeColor(HexColor(sp.rect_border_hex))
                    can.setFillColor(HexColor(sp.rect_fill_hex))
                    can.rect(0, 0, w_pt, h_pt, stroke=(1 if border_alpha > 0 else 0), fill=(1 if fill_alpha > 0 else 0))
                    can.restoreState()

            # 3. Draw Content
            if sp.stamp_type == "image" and sp.image_bytes:
                can.drawImage(ImageReader(io.BytesIO(sp.image_bytes)), 0, 0, width=w_pt, height=h_pt, mask='auto')

            elif sp.stamp_type == "text":
                text_c = HexColor(sp.text_color_hex)
                font_name = pick_font_name(sp.bold, sp.italic)
                can.setFont(font_name, float(sp.font_size_pt))

                if sp.tiled:
                    # Tiled mode uses rect_opacity for the text itself
                    alpha = max(0.0, min(1.0, 1.0 - float(sp.rect_opacity)))
                    dx_pt, dy_pt = mm_to_pt(sp.tile_dx_mm), mm_to_pt(sp.tile_dy_mm)
                    # For tiled mode, we need to undo the box translation/rotation for full page
                    can.restoreState() # Pop the box transform
                    can.saveState()    # Fresh state for tiling
                    
                    off_x, off_y = mm_to_pt(sp.x_mm), mm_to_pt(sp.y_mm)
                    for y in range(-int(page_h_pt), int(page_h_pt*2), int(max(6, dy_pt))):
                        for x in range(-int(page_w_pt), int(page_w_pt*2), int(max(6, dx_pt))):
                            can.saveState()
                            can.translate(x + off_x, y + off_y)
                            can.rotate(sp.tile_angle_deg)
                            ensure_alpha(can, fill_alpha=alpha, stroke_alpha=alpha)
                            can.setFillColor(text_c)
                            can.drawString(0, 0, sp.text or "")
                            can.restoreState()
                else:
                    # Center text within padded box
                    can.setFillColor(text_c)
                    ensure_alpha(can, fill_alpha=1.0)
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

            can.restoreState() # Restore the state saved at the beginning of the loop for this stamp

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

                    is_sig = (sidx == ss.get("sig_stamp_idx"))
                    text_val = st.text_input(
                        "Text",
                        value=editing.text,
                        key=f"text_{sidx}",
                        disabled=is_sig,
                        help="Digital signature text is read-only." if is_sig else None,
                    )

                    cx4, cx5 = st.columns(2)
                    with cx4:
                        rect_fill_hex = st.color_picker("Rect fill", value=editing.rect_fill_hex, key=f"fill_{sidx}")
                        rect_opacity = st.slider("Rect fill transparency (0→1)", 0.0, 1.0, float(editing.rect_opacity), 0.05, key=f"opc_{sidx}")
                    with cx5:
                        rect_border_hex = st.color_picker("Rect border", value=editing.rect_border_hex, key=f"bor_{sidx}")
                        rect_border_opacity = st.slider("Rect border transparency (0→1)", 0.0, 1.0, float(editing.rect_border_opacity), 0.05, key=f"bopc_{sidx}")
                        border_width_pt = st.number_input("Border width (pt)", 0.0, 12.0, float(editing.border_width_pt), 0.5, key=f"bw_{sidx}")

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

        ss.save_to_local = st.checkbox(
            "Save to local directory on apply",
            value=ss.get("save_to_local", True),
            disabled=not export_path_val.strip(),
            help="Uncheck to apply stamps and download via browser only, without writing to the local path."
        )

        # Apply button (explicit action, visible regardless of stamps)
        btn_label = "🚀 Apply Changes (Stamps / Security / Export)" if ss.stamps else "🚀 Apply Settings (Security / Export)"
        apply_now = perm_button(btn_label, "btn_apply_stamp", use_container_width=True, key="apply_btn")

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
            
            # Start Processing

            with st.spinner("Applying stamps to PDF..."):
                # Strip CropBox so stamp coordinates match the preview (which also strips it)
                _apply_bytes = _strip_cropbox(st.session_state.pdf_bytes)
                reader = PdfReader(io.BytesIO(_apply_bytes))
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
                        permissions &= ~((1 << 2) | (1 << 11)) # disable printing and high-res printing
                    if st.session_state.sec_disable_modify:
                        permissions &= ~(1 << 3)         # disable document modification
                    if st.session_state.sec_disable_copy:
                        permissions &= ~(1 << 4)         # disable text copying/extract
                    if st.session_state.sec_disable_annotate:
                        permissions &= ~(1 << 5)         # disable annotations/comments
                    if st.session_state.sec_disable_formfill:
                        permissions &= ~(1 << 8)         # disable form filling
                    if st.session_state.sec_disable_accessibility:
                        permissions &= ~(1 << 9)         # disable accessibility extract (e.g. screen readers)

                    # Ensure value fits signed 32-bit range
                    if permissions > 0x7FFFFFFF:
                        permissions -= 0x100000000

                    # We'll apply AES-256 encryption using pikepdf after writing
                    pass

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    writer.write(tmp)
                    out_path = tmp.name

            with open(out_path, "rb") as f:
                pdf_data = f.read()

            # --- Upgrade to AES-256 if security is enabled ---
            if st.session_state.sec_enabled:
                try:
                    with pikepdf.open(io.BytesIO(pdf_data)) as pdf:
                        p = st.session_state
                        perms = pikepdf.Permissions(
                            modify_other=not p.sec_disable_modify,
                            extract=not p.sec_disable_copy,
                            modify_annotation=not p.sec_disable_annotate,
                            modify_form=not p.sec_disable_formfill,
                            accessibility=not p.sec_disable_accessibility,
                            print_lowres=not p.sec_disable_print,
                            print_highres=not p.sec_disable_print
                        )
                        enc = pikepdf.Encryption(
                            owner=p.sec_owner_pw,
                            user=p.sec_user_pw,
                            allow=perms,
                            R=6
                        )
                        out_buf = io.BytesIO()
                        pdf.save(out_buf, encryption=enc)
                        pdf_data = out_buf.getvalue()
                except Exception as e:
                    st.warning(f"🔐 AES-256 Upgrade Note: {e}")
                
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

                # 1. Primary Export (requires save-local permission)
                save_path_raw = st.session_state.get("export_path", "")
                c_name = st.session_state.get("custom_filename", "stamped_output.pdf")
                if save_path_raw and ss.get("save_to_local", True):
                    if not can_do("btn_save_local"):
                        st.warning("🔒 You don't have permission to save files to a local path.")
                    else:
                        final_path = safe_save(save_path_raw, pdf_data, c_name)
                        if final_path:
                            st.success(f"✅ PDF successfully saved directly to: {final_path}")
            except Exception as e:
                st.error(f"❌ Failed to save to local path: {e}")
                
            fname = st.session_state.get("custom_filename", "stamped_output.pdf")
            st.download_button("📥 Download stamped PDF via Browser", pdf_data, file_name=fname, mime="application/pdf")
            if st.session_state.sec_enabled:
                st.success("✅ Done! Stamps applied and PDF encrypted.")
            else:
                st.success("✅ Done! Stamps applied.")

    # ─────────────────────────────────────────────────────────────────────────────
    # BATCH WATERMARK — apply current stamps to multiple PDFs at once
    st.markdown("---")
    with st.expander("📦 Batch Watermark — apply stamps to multiple PDFs", expanded=False):
        if not ss.stamps:
            st.info("Add at least one stamp in the sidebar before using batch mode.")
        else:
            st.write(f"Current stamp config: **{len(ss.stamps)} stamp(s)** will be applied to every uploaded PDF.")

            batch_files = st.file_uploader(
                "Upload PDFs", type="pdf",
                accept_multiple_files=True, key="batch_upload"
            )

            batch_suffix = st.text_input(
                "Output filename suffix", value="_stamped",
                help="Appended before .pdf extension, e.g. report_stamped.pdf",
                key="batch_suffix"
            )

            if batch_files and perm_button(
                f"🚀 Apply stamps to {len(batch_files)} PDF(s)", "btn_apply_stamp",
                use_container_width=True, key="batch_apply_btn"
            ):
                progress = st.progress(0)
                status   = st.empty()
                results  = []

                for i, bf in enumerate(batch_files):
                    status.text(f"Processing {bf.name} ({i+1}/{len(batch_files)})…")
                    try:
                        raw = bf.read()
                        reader = PdfReader(io.BytesIO(raw))
                        if reader.is_encrypted:
                            st.warning(f"⚠️ {bf.name} is encrypted — skipped.")
                            progress.progress((i + 1) / len(batch_files))
                            continue

                        writer = PdfWriter()
                        for pg_idx, page in enumerate(reader.pages):
                            w_pt, h_pt = get_page_size_pt(page)
                            # Adjust stamp page ranges to match this PDF's page count
                            num_pg = len(reader.pages)
                            adj_stamps = []
                            for s in ss.stamps:
                                sc = copy.copy(s)
                                sc.page_to = min(sc.page_to, num_pg)
                                adj_stamps.append(sc)
                            overlay_reader = build_overlay_pdf_for_page(adj_stamps, pg_idx, w_pt, h_pt)
                            if overlay_reader:
                                page.merge_page(overlay_reader.pages[0])
                            writer.add_page(page)

                        out_buf = io.BytesIO()
                        writer.write(out_buf)
                        out_bytes = out_buf.getvalue()

                        base, ext = os.path.splitext(bf.name)
                        out_name = f"{base}{batch_suffix}{ext}"
                        results.append((out_name, out_bytes))

                    except Exception as e:
                        st.error(f"❌ {bf.name}: {e}")

                    progress.progress((i + 1) / len(batch_files))

                status.empty()
                progress.empty()

                if results:
                    st.success(f"✅ {len(results)} PDF(s) processed. Download below:")
                    for out_name, out_bytes in results:
                        st.download_button(
                            f"📥 {out_name}",
                            out_bytes,
                            file_name=out_name,
                            mime="application/pdf",
                            key=f"batch_dl_{out_name}"
                        )


def merge_pdf_tool():
    st.header("🔗 Merge PDFs")
    files = st.file_uploader("Upload PDFs to merge", type="pdf", accept_multiple_files=True, key="merge_upload")

    if files and perm_button("Merge PDFs", "btn_merge"):
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

            if perm_button("Split PDF", "btn_split"):
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

    if file and perm_button("Extract", "btn_extract_pages"):
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

_RTL_LANGS = {"ara", "heb", "fas", "urd", "syr", "yid"}

_LANG_LABELS = {
    "ara": "Arabic (عربي)", "eng": "English", "fra": "French (Français)",
    "deu": "German (Deutsch)", "spa": "Spanish (Español)", "por": "Portuguese",
    "ita": "Italian", "nld": "Dutch", "rus": "Russian", "tur": "Turkish",
    "fas": "Persian (فارسی)", "heb": "Hebrew (עברית)", "urd": "Urdu (اردو)",
    "chi_sim": "Chinese Simplified", "chi_tra": "Chinese Traditional",
    "jpn": "Japanese", "kor": "Korean", "pol": "Polish",
}


def _get_tess() -> "pytesseract | None":
    """Return the pytesseract module, trying a fresh import if startup failed."""
    if _HAS_OCR:
        import pytesseract as _t
        return _t
    try:
        import pytesseract as _t
        _tw = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.name == "nt" and os.path.isfile(_tw):
            _t.pytesseract.tesseract_cmd = _tw
        return _t
    except Exception:
        return None


def _tess_langs() -> list[str]:
    """Return installed Tesseract language codes, excluding osd."""
    t = _get_tess()
    if t is None:
        return ["eng"]
    try:
        return [l for l in t.get_languages() if l != "osd"]
    except Exception:
        return ["eng"]


def _show_text_rtl(text: str, lang: str, max_chars: int = 5000) -> None:
    """Display extracted text with RTL direction for Arabic/Hebrew etc."""
    preview = text[:max_chars]
    if any(c in _RTL_LANGS for c in lang.split("+")):
        escaped = (preview.replace("&", "&amp;")
                          .replace("<", "&lt;")
                          .replace(">", "&gt;"))
        st.html(
            f'<div style="direction:rtl;text-align:right;font-family:Arial,sans-serif;'
            f'font-size:14px;line-height:1.7;white-space:pre-wrap;'
            f'background:#1e1e2e;color:#cdd6f4;padding:12px;border-radius:6px;'
            f'max-height:400px;overflow-y:auto;">{escaped}</div>'
        )
    else:
        st.text_area("Preview extracted text", preview, height=300)


_PSM_OPTIONS = {
    "3 — Auto (recommended for most documents)": "3",
    "6 — Single uniform text block": "6",
    "11 — Sparse text, no order (best for packaging labels)": "11",
    "12 — Sparse text with orientation detection": "12",
    "4 — Single column, varying sizes": "4",
}


def _preprocess_ocr_image(pil: "Image.Image", enhance: bool) -> "Image.Image":
    """Grayscale → contrast boost → sharpen → binarize for cleaner OCR."""
    gray = pil.convert("L")
    if enhance:
        gray = ImageEnhance.Contrast(gray).enhance(2.5)
        gray = ImageEnhance.Sharpness(gray).enhance(2.0)
        gray = gray.point(lambda x: 255 if x > 127 else 0).convert("L")
    return gray


def _ocr_best_rotation(tess, img: "Image.Image", lang: str, config: str) -> str:
    """Try 0/90/180/270° rotations; return text from the angle with highest
    average Tesseract word confidence."""
    best_text, best_angle = "", 0
    top_conf = -1.0
    for angle in [0, 90, 180, 270]:
        rotated = img.rotate(angle, expand=True) if angle else img
        try:
            data = tess.image_to_data(
                rotated, lang=lang, config=config,
                output_type=tess.Output.DICT,
            )
            confs = [c for c in data["conf"] if isinstance(c, (int, float)) and c >= 0]
            avg = sum(confs) / len(confs) if confs else 0.0
            if avg > top_conf:
                top_conf = avg
                best_angle = angle
                best_text = tess.image_to_string(rotated, lang=lang, config=config)
        except Exception:
            pass
    return best_text.strip(), best_angle


def extract_text_tool():
    st.header("🔍 Extract Text from PDF")
    file = st.file_uploader("Upload PDF", type="pdf", key="extract_text_upload")

    use_ocr = st.checkbox(
        "Use OCR (Tesseract) — required for scanned/image-only PDFs",
        value=False,
        key="extract_text_use_ocr",
    )

    if use_ocr:
        available_langs = _tess_langs()
        lang_labels = [_LANG_LABELS.get(l, l) for l in available_langs]
        default_labels = [_LANG_LABELS.get("ara", "ara")] if "ara" in available_langs else [lang_labels[0]]
        chosen_labels = st.multiselect(
            "OCR language(s)",
            lang_labels,
            default=default_labels,
            key="extract_text_lang",
        )
        ocr_lang = "+".join(
            available_langs[lang_labels.index(l)] for l in chosen_labels
        ) if chosen_labels else "eng"

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            psm_label = st.selectbox(
                "Page segmentation mode",
                list(_PSM_OPTIONS.keys()),
                index=2,  # default: PSM 11 sparse
                key="extract_text_psm",
            )
            psm_val = _PSM_OPTIONS[psm_label]
        with c2:
            render_scale = st.selectbox(
                "Render scale (higher = sharper)",
                [2.0, 3.0, 4.0],
                index=1,
                key="extract_text_scale",
            )
        with c3:
            enhance_img = st.checkbox(
                "Enhance image\n(contrast + binarize)",
                value=True,
                key="extract_text_enhance",
            )
        with c4:
            auto_rotate = st.checkbox(
                "Auto-detect rotation\n(0/90/180/270°)",
                value=True,
                key="extract_text_autorotate",
                help="Tries all 4 angles and keeps the one with the highest Tesseract confidence. Fixes upside-down packaging scans.",
            )

        ocr_cfg = f"--oem 1 --psm {psm_val}"
    else:
        ocr_lang = "eng"
        render_scale = 2.0
        enhance_img = False
        auto_rotate = False
        ocr_cfg = "--oem 1 --psm 11"

    if file and perm_button("Extract Text", "btn_extract_text"):
        try:
            pdf_bytes = file.read()
            reader = PdfReader(io.BytesIO(pdf_bytes))
            if reader.is_encrypted:
                st.error(f"🔒 The file **{file.name}** is encrypted — cannot extract text.")
                return

            pages_text = []
            ocr_used_pages = []

            if use_ocr:
                _tess = _get_tess()
                if _tess is None:
                    import sys as _sys
                    st.error("Cannot load pytesseract.")
                    st.code(f"Python: {_sys.executable}", language=None)
                    st.info("Run: `C:\\Users\\DELL\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m pip install pytesseract`")
                    return

                import pypdfium2 as pdfium
                doc = pdfium.PdfDocument(io.BytesIO(_strip_cropbox(pdf_bytes)))
                rotation_notes = []
                with st.spinner(f"Running OCR ({ocr_lang}, PSM {psm_val}) on {len(doc)} page(s)…"):
                    for i, pg in enumerate(doc):
                        embedded = (reader.pages[i].extract_text() or "").strip() if i < len(reader.pages) else ""
                        if embedded:
                            pages_text.append(embedded)
                        else:
                            raw = pg.render(scale=render_scale).to_pil().convert("RGB")
                            processed = _preprocess_ocr_image(raw, enhance_img)
                            try:
                                if auto_rotate:
                                    ocr_text, used_angle = _ocr_best_rotation(
                                        _tess, processed, ocr_lang, ocr_cfg
                                    )
                                    if used_angle:
                                        rotation_notes.append(f"page {i+1}: rotated {used_angle}°")
                                else:
                                    ocr_text = _tess.image_to_string(
                                        processed, lang=ocr_lang, config=ocr_cfg
                                    ).strip()
                            except Exception as ocr_err:
                                st.warning(f"OCR failed on page {i+1}: {ocr_err}")
                                ocr_text = ""
                            pages_text.append(ocr_text)
                            if ocr_text:
                                ocr_used_pages.append(i + 1)
                doc.close()
                if ocr_used_pages:
                    note = f"📷 OCR ({ocr_lang}) on page(s): {', '.join(map(str, ocr_used_pages))}"
                    if rotation_notes:
                        note += f" — auto-rotated: {', '.join(rotation_notes)}"
                    st.caption(note)
                elif not any(pages_text):
                    st.warning("OCR found no text — try a different PSM mode or language.")
            else:
                pages_text = [page.extract_text() or "" for page in reader.pages]

            text = "\n".join(pages_text)
            if not text.strip():
                st.warning("No text found. Enable OCR for scanned PDFs.")
            else:
                st.download_button("📄 Download extracted text", text, "extracted.txt", "text/plain")
                _show_text_rtl(text, ocr_lang)

        except FileNotDecryptedError:
            st.error("🔒 File is encrypted — cannot extract text.")
        except Exception as e:
            st.error(f"❌ Failed to extract text: {e}")


def convert_to_images_tool():
    st.header("🖼 Convert PDF Pages to Images")
    file = st.file_uploader("Upload PDF", type="pdf", key="convert_image_upload")

    if file and perm_button("Convert", "btn_convert_images"):
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

    if file and perm_button("Compress", "btn_compress"):
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


def admin_dashboard_tool():
    st.header("🛡️ Admin Dashboard")

    dash_tab, perm_tab, users_tab, viewer_tab = st.tabs(["👥 Connected Users", "🔑 Permissions", "👤 User Management", "📁 Shared Viewer"])

    # ── Connected Users ───────────────────────────────────────────────────────
    with dash_tab:
        minutes = st.number_input("Last active (minutes)", 1, 1440, 5)
        active_users = db.get_active_users(minutes)
        if not active_users:
            st.info(f"No users active in the last {minutes} minutes.")
        else:
            st.write(f"Showing **{len(active_users)}** users active in the last {minutes} minutes:")
            data = []
            for u in active_users:
                role = "👑 Admin" if u['is_admin'] else "👤 User"
                data.append({"Username": u['username'], "Role": role, "Last Active (UTC)": u['last_seen']})
            st.table(data)
        if st.button("🔄 Refresh Data"):
            st.rerun()

    # ── Permissions Management ────────────────────────────────────────────────
    with perm_tab:
        st.subheader("Permission Management")
        all_users = db.get_all_users()
        non_admin_users = [u for u in all_users if not u['is_admin']]

        if not non_admin_users:
            st.info("No non-admin users found.")
        else:
            selected_user = st.selectbox(
                "Select user",
                [u['username'] for u in non_admin_users],
                key="perm_user_select"
            )

            if selected_user:
                saved_perms = db.get_user_permissions(selected_user)
                new_perms = {}

                st.markdown("#### Tab Access")
                st.caption("Controls which tabs are visible to this user.")
                tab_cols = st.columns(3)
                for idx, (pkey, label) in enumerate(TAB_PERMISSIONS):
                    current = saved_perms.get(pkey, True)
                    new_perms[pkey] = tab_cols[idx % 3].checkbox(
                        label, value=current, key=f"perm_{selected_user}_{pkey}"
                    )

                st.markdown("#### Action Permissions")
                st.caption("Controls which action buttons are enabled within each tab.")
                btn_cols = st.columns(3)
                for idx, (pkey, label) in enumerate(BTN_PERMISSIONS):
                    current = saved_perms.get(pkey, True)
                    new_perms[pkey] = btn_cols[idx % 3].checkbox(
                        label, value=current, key=f"perm_{selected_user}_{pkey}"
                    )

                if st.button("💾 Save Permissions", use_container_width=True, key="save_perms_btn"):
                    db.set_user_permissions(selected_user, new_perms)
                    st.success(f"✅ Permissions saved for **{selected_user}**.")
                    st.rerun()

    # ── User Management ───────────────────────────────────────────────────────
    with users_tab:
        st.subheader("All Users")
        all_users_mgmt = db.get_all_users()
        current_admin = st.session_state.current_user

        # Table of all users
        user_data = []
        for u in all_users_mgmt:
            role = "👑 Admin" if u['is_admin'] else "👤 User"
            user_data.append({"Username": u['username'], "Role": role, "Last Active (UTC)": u['last_seen'] or "—"})
        st.table(user_data)

        st.markdown("---")

        # ── Create new user ───────────────────────────────────────────────────
        with st.expander("➕ Create New User", expanded=False):
            with st.form("create_user_form"):
                new_uname = st.text_input("Username")
                new_pw    = st.text_input("Password", type="password")
                new_pw2   = st.text_input("Confirm Password", type="password")
                new_admin = st.checkbox("Grant admin privileges")
                submitted = st.form_submit_button("Create User", use_container_width=True)

            if submitted:
                if not new_uname.strip():
                    st.error("Username cannot be empty.")
                elif len(new_pw) < 4:
                    st.error("Password must be at least 4 characters.")
                elif new_pw != new_pw2:
                    st.error("Passwords do not match.")
                else:
                    err = db.create_user(new_uname.strip(), new_pw, new_admin)
                    if err:
                        st.error(f"❌ {err}")
                    else:
                        st.success(f"✅ User **{new_uname}** created successfully.")
                        st.rerun()

        # ── Reset password ────────────────────────────────────────────────────
        with st.expander("🔑 Reset User Password", expanded=False):
            other_users = [u['username'] for u in all_users_mgmt if u['username'] != current_admin]
            if not other_users:
                st.info("No other users to manage.")
            else:
                with st.form("reset_pw_form"):
                    reset_target = st.selectbox("Select user", other_users, key="reset_pw_select")
                    reset_pw     = st.text_input("New Password", type="password")
                    reset_pw2    = st.text_input("Confirm New Password", type="password")
                    reset_submit = st.form_submit_button("Reset Password", use_container_width=True)

                if reset_submit:
                    if len(reset_pw) < 4:
                        st.error("Password must be at least 4 characters.")
                    elif reset_pw != reset_pw2:
                        st.error("Passwords do not match.")
                    else:
                        db.change_password(reset_target, reset_pw)
                        st.success(f"✅ Password for **{reset_target}** has been reset.")

        # ── Toggle admin ──────────────────────────────────────────────────────
        with st.expander("⚙️ Toggle Admin Status", expanded=False):
            toggleable = [u for u in all_users_mgmt if u['username'] != current_admin]
            if not toggleable:
                st.info("No other users to manage.")
            else:
                with st.form("toggle_admin_form"):
                    toggle_target = st.selectbox(
                        "Select user",
                        [u['username'] for u in toggleable],
                        key="toggle_admin_select"
                    )
                    target_rec = next((u for u in toggleable if u['username'] == toggle_target), None)
                    current_role = "Admin" if (target_rec and target_rec['is_admin']) else "User"
                    st.caption(f"Current role: **{current_role}**")
                    make_admin = st.checkbox(
                        "Grant admin privileges",
                        value=bool(target_rec['is_admin']) if target_rec else False,
                        key="toggle_admin_ck"
                    )
                    toggle_submit = st.form_submit_button("Update Role", use_container_width=True)

                if toggle_submit:
                    db.set_admin(toggle_target, make_admin)
                    new_role = "Admin" if make_admin else "User"
                    st.success(f"✅ **{toggle_target}** is now a **{new_role}**.")
                    st.rerun()

        # ── Delete user ───────────────────────────────────────────────────────
        with st.expander("🗑️ Delete User", expanded=False):
            deletable = [u['username'] for u in all_users_mgmt if u['username'] != current_admin]
            if not deletable:
                st.info("No other users to delete.")
            else:
                del_target = st.selectbox("Select user to delete", deletable, key="del_user_select")
                st.warning(f"This will permanently delete **{del_target}** and all their settings.")
                confirm_del = st.checkbox(f"I confirm I want to delete **{del_target}**", key="confirm_del_ck")
                if st.button("🗑️ Delete User", disabled=not confirm_del, key="del_user_btn", use_container_width=True):
                    db.delete_user(del_target)
                    st.success(f"✅ User **{del_target}** has been deleted.")
                    st.rerun()

    # ── Shared Viewer Folder ──────────────────────────────────────────────────
    with viewer_tab:
        st.subheader("Shared Viewer Folder")
        st.caption("Choose a folder, then tick the PDFs you want employees to see on the home page.")

        current_shared = db.get_setting("__system__", "shared_viewer_folder", "") or ""
        new_shared = st.text_input("Folder Path", value=current_shared, key="shared_viewer_folder_input")

        col_save, col_clear = st.columns(2)
        with col_save:
            if st.button("💾 Save Path", use_container_width=True, key="save_shared_folder"):
                db.set_setting("__system__", "shared_viewer_folder", new_shared.strip())
                st.success("✅ Folder path saved.")
                st.rerun()
        with col_clear:
            if st.button("🗑️ Clear Path", use_container_width=True, key="clear_shared_folder"):
                db.set_setting("__system__", "shared_viewer_folder", "")
                db.set_setting("__system__", "shared_viewer_files", "[]")
                st.success("Shared folder cleared.")
                st.rerun()

        st.markdown("---")

        # Load current whitelist
        try:
            visible_files: list[str] = json.loads(
                db.get_setting("__system__", "shared_viewer_files", "[]") or "[]"
            )
        except (json.JSONDecodeError, TypeError):
            visible_files = []

        check = new_shared.strip() or current_shared
        if check:
            if os.path.isdir(check):
                all_pdfs = sorted(f for f in os.listdir(check) if f.lower().endswith(".pdf"))
                if all_pdfs:
                    st.markdown(f"**Select which PDFs to show on the home page** ({len(all_pdfs)} found):")
                    new_visible = []
                    for fname in all_pdfs:
                        checked = fname in visible_files
                        if st.checkbox(fname, value=checked, key=f"vf_{fname}"):
                            new_visible.append(fname)

                    if st.button("💾 Save visible files", use_container_width=True, key="save_visible_files"):
                        db.set_setting("__system__", "shared_viewer_files", json.dumps(new_visible))
                        st.success(f"✅ {len(new_visible)} file(s) will be shown on the home page.")
                        st.rerun()
                else:
                    st.info("Folder exists but contains no PDF files yet.")
            else:
                st.warning("⚠️ Folder path not found or not accessible.")


def readonly_pdf_tool():
    st.header("🔒 Bulk Read-Only Lockdown")
    st.write("Apply AES-256 restrictions and/or image flattening to PDF files. See the info box below for which mode to use based on your target viewer.")
    
    # Session state for RO path
    username = st.session_state.get("current_user", "Unknown")
    ss = st.session_state
    
    # 1. Target Directory Setting
    db_ro_export_path = db.get_setting(username, "ro_export_path", "")
    if "ro_export_path_input" not in ss:
        ss.ro_export_path_input = db_ro_export_path

    if "show_browse_ro" not in ss: ss.show_browse_ro = False
    
    c_ro_path, c_ro_browse, c_ro_save = st.columns([0.6, 0.2, 0.2])
    with c_ro_path:
        ro_export_path_val = st.text_input("Save Directory", key="ro_export_path_tab_input", value=ss.ro_export_path_input)
    
    with c_ro_browse:
        st.write("") # Padding
        if st.button("📂 Browse", key="ro_tab_browse_btn", use_container_width=True):
            ss.show_browse_ro = not ss.show_browse_ro
            st.rerun()
        
    with c_ro_save:
        st.write("") # Padding
        if st.button("💾 Set Default", key="ro_tab_save_db", use_container_width=True):
            db.set_setting(username, "ro_export_path", ro_export_path_val)
            st.success("Default path updated!")

    if ss.show_browse_ro:
        with st.container(border=True):
            new_path_ro = folder_picker_ui("ro_tab_p", ro_export_path_val or os.path.expanduser("~"))
            if new_path_ro != ro_export_path_val:
                st.session_state.ro_export_path_tab_input = new_path_ro
                st.rerun()
            if st.button("Close Browser", key="close_ro_tab"):
                ss.show_browse_ro = False
                st.rerun()
            
    # 2. File Upload
    files = st.file_uploader("Upload PDF(s) to process", type="pdf", accept_multiple_files=True, key="ro_bulk_upload")
    
    use_session_pdf = False
    if not files and ss.get("pdf_bytes"):
        use_session_pdf = st.checkbox("Use PDF currently uploaded in the Watermark tab", True)
        if use_session_pdf:
            st.info("The PDF from the Watermark tab will be processed.")

    # 3. Mode Options
    with st.expander("ℹ️ How PDF protection works — known limitations", expanded=False):
        st.markdown("""
**Firefox (built-in PDF viewer)**
Firefox's PDF.js viewer intentionally ignores PDF permission flags (no-print, no-copy, no-edit).
This is a deliberate browser design decision and **cannot be fixed at the PDF level**.
Enabling **Flatten mode** below prevents text selection/copying in Firefox (no text layer exists),
but Firefox can still save or print the file.

**Adobe Acrobat — "Export to Word"**
There are two conflicting strategies:

| Mode | What it does | Adobe "Export to Word" |
|---|---|---|
| **Flatten OFF** (permission-only) | Keeps text layer, enforces copy/edit restrictions via AES-256 | Blocked — `extract=False` prevents text export |
| **Flatten ON** (image rebuild) | All pages become images, no text layer | OCR is applied — Acrobat reads image pixels and converts to text |

**Recommendation:**
- To block Adobe "Export to Word": **uncheck Flatten** — the AES-256 restrictions will block text extraction in compliant viewers (Acrobat, Foxit, etc.)
- To prevent in-browser copy/paste (all viewers): **keep Flatten enabled**
- No PDF encryption scheme can fully prevent a determined user — true document security requires server-side access control.
        """)

    flatten_mode = st.checkbox(
        "Flatten PDF (rebuild pages as images — prevents text selection but allows OCR-based Export to Word in Acrobat)",
        value=False,
        help="ON: Converts pages to images — prevents text copy/paste in all viewers including Firefox, but Adobe Acrobat can still OCR the images when using Export to Word.\nOFF: Keeps text layer with AES-256 restrictions — blocks Adobe Export to Word, but Firefox ignores permission flags."
    )

    if flatten_mode:
        st.info("Flatten mode: Pages will be converted to images. Text copy/paste is blocked, but Adobe Acrobat's OCR-based 'Export to Word' can still extract content from images.")
    else:
        st.info("Permission-only mode: AES-256 encryption with no-extract restrictions. Adobe Acrobat will block 'Export to Word'. Firefox's built-in viewer ignores these restrictions by design.")

    if perm_button("🚀 Apply Read-Only Lockdown", "btn_readonly_apply", use_container_width=True):
        if not files and not use_session_pdf:
            st.error("Please upload at least one PDF or select the session PDF.")
            return
            
        if not ro_export_path_val or not ro_export_path_val.strip():
            st.error("Please specify a Save Directory.")
            return

        # Helper for saving (reuse logic)
        def safe_save_local(p_raw, data, default_name):
            p = os.path.normpath(os.path.expanduser(p_raw.strip()))
            if os.path.isdir(p):
                p = os.path.join(p, default_name)
            p_dir = os.path.dirname(p)
            if p_dir and not os.path.exists(p_dir):
                os.makedirs(p_dir, exist_ok=True)
            with open(p, "wb") as f_out:
                f_out.write(data)
            return p

        # Collect files to process
        to_process = []
        if files:
            for f in files:
                to_process.append((f.name, f.read()))
        elif use_session_pdf:
            # try to get name from ss
            orig_name = ss.get("last_uploaded_name", "session_file.pdf")
            to_process.append((orig_name, ss.pdf_bytes))

        progress_bar = st.progress(0)
        status_text = st.empty()
        
        results = []
        for i, (name, content) in enumerate(to_process):
            status_text.text(f"Processing {name}...")
            
            try:
                # --- FLATTENING (if enabled) ---
                if flatten_mode:
                    status_text.text(f"Flattening {name} (converting pages to images)...")
                    pdf_input = pdfium.PdfDocument(io.BytesIO(content))
                    output_buf = io.BytesIO()
                    c = rl_canvas.Canvas(output_buf)
                    
                    for pg in pdf_input:
                        # Render to high-res image (3.0 scale for professional quality)
                        pil_img = pg.render(scale=3.0).to_pil()
                        
                        # Set page size (pdfium uses points/pixels, we need to match)
                        w, h = pg.get_size()
                        c.setPageSize((w, h))
                        
                        # Draw image full page
                        # Convert PIL to bytes for ImageReader
                        img_byte_arr = io.BytesIO()
                        pil_img.save(img_byte_arr, format='PNG')
                        img_byte_arr.seek(0)
                        
                        c.drawImage(ImageReader(img_byte_arr), 0, 0, width=w, height=h)
                        c.showPage()
                    
                    c.save()
                    pdf_input.close()
                    content = output_buf.getvalue() # Content is now the flattened PDF

                # --- UPGRADE TO AES-256 (for best-effort block in Firefox) ---
                status_text.text(f"Applying 256-bit AES Lockdown to {name}...")
                with pikepdf.open(io.BytesIO(content)) as pdf:
                    # Very restrictive permissions
                    perms = pikepdf.Permissions(
                        modify_other=False,
                        extract=False,
                        modify_annotation=False,
                        modify_form=False,
                        accessibility=False,
                        modify_assembly=False,
                        print_lowres=False,
                        print_highres=False
                    )
                    
                    enc = pikepdf.Encryption(
                        owner=str(uuid.uuid4()),
                        user="", # Allow opening without password
                        allow=perms,
                        R=6 # AES-256
                    )
                    
                    final_buf = io.BytesIO()
                    pdf.save(final_buf, encryption=enc)

                # filename logic: original_readonly.pdf
                base, ext = os.path.splitext(name)
                ro_name = f"{base}_readonly{ext}"

                final_p = safe_save_local(ro_export_path_val, final_buf.getvalue(), ro_name)
                results.append(final_p)
            except Exception as e:
                st.error(f"Error processing {name}: {e}")
            
            progress_bar.progress((i + 1) / len(to_process))
            
        status_text.success(f"✅ Finished processing {len(results)} files.")
        if results:
            with st.expander("Show saved paths", expanded=True):
                for r in results:
                    st.write(f"- `{r}`")



def protected_pdf_tool():
    st.header("🔑 App-Protected PDF")
    st.write(
        "Lock a PDF with a secret password derived from this application's secret key. "
        "Any other viewer (Firefox, Acrobat, Chrome…) will show a password prompt the user cannot answer — "
        "only this app can open it automatically."
    )

    # ── Secret management ─────────────────────────────────────────────────────
    # Prefer env var; otherwise generate once and persist in DB
    raw_secret = os.environ.get("APP_PDF_SECRET") or db.get_setting("__system__", "app_pdf_secret", "")
    if not raw_secret:
        raw_secret = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char random
        db.set_setting("__system__", "app_pdf_secret", raw_secret)

    # Derive stable AES-256 passwords from the secret (never shown to users)
    USER_PASS  = hashlib.sha256((raw_secret + ":user").encode()).hexdigest()[:32]
    OWNER_PASS = hashlib.sha256((raw_secret + ":owner").encode()).hexdigest()[:32]

    lock_tab, view_tab = st.tabs(["🔒 Lock PDF", "👁️ View Protected PDF"])

    # ── LOCK ──────────────────────────────────────────────────────────────────
    with lock_tab:
        st.subheader("Lock PDF(s)")
        st.info(
            "The PDF will be encrypted with AES-256 using a password known only to this application. "
            "Opening the file in any other viewer will display a password prompt that the user cannot bypass."
        )

        files = st.file_uploader(
            "Upload PDF(s) to lock", type="pdf",
            accept_multiple_files=True, key="protect_lock_upload"
        )

        if files and perm_button("🔒 Lock PDF(s)", "btn_lock_pdf", use_container_width=True, key="do_lock_btn"):
            perms = pikepdf.Permissions(
                modify_other=False,
                extract=False,
                modify_annotation=False,
                modify_form=False,
                accessibility=False,
                modify_assembly=False,
                print_lowres=False,
                print_highres=False,
            )
            enc = pikepdf.Encryption(
                owner=OWNER_PASS,
                user=USER_PASS,
                allow=perms,
                R=6,
            )
            for f in files:
                try:
                    with pikepdf.open(io.BytesIO(f.read())) as pdf:
                        out_buf = io.BytesIO()
                        pdf.save(out_buf, encryption=enc)
                    base, ext = os.path.splitext(f.name)
                    locked_name = f"{base}_applock{ext}"
                    st.download_button(
                        f"📥 Download {locked_name}",
                        out_buf.getvalue(),
                        locked_name,
                        "application/pdf",
                        key=f"dl_lock_{f.name}",
                    )
                    st.success(f"✅ {f.name} locked.")
                except Exception as e:
                    st.error(f"❌ Failed to lock {f.name}: {e}")

    # ── VIEW ──────────────────────────────────────────────────────────────────
    with view_tab:
        st.subheader("View a Protected PDF")
        st.info(
            "Upload a PDF that was locked by this application. "
            "Pages are displayed as images inside this viewer — no decrypted file is ever sent to the browser."
        )

        if not can_do("btn_view_protected"):
            st.warning("🔒 You don't have permission to view protected PDFs.")
            return
        locked_file = st.file_uploader(
            "Upload protected PDF", type="pdf", key="protect_view_upload"
        )

        if locked_file:
            content = locked_file.read()
            try:
                # Decrypt using owner password → strip encryption → render pages
                with pikepdf.open(io.BytesIO(content), password=OWNER_PASS) as pdf:
                    plain_buf = io.BytesIO()
                    pdf.save(plain_buf)

                plain_buf.seek(0)
                pdf_doc = pdfium.PdfDocument(plain_buf)
                num_pages = len(pdf_doc)
                st.success(f"✅ PDF unlocked — {num_pages} page(s)")

                page_idx = st.number_input(
                    "Page", min_value=1, max_value=num_pages,
                    value=1, step=1, key="protect_view_page"
                ) - 1

                page = pdf_doc[page_idx]
                pil_img = page.render(scale=2.0).to_pil()
                pdf_doc.close()

                # Convert image to base64 for protected HTML rendering
                img_buf = io.BytesIO()
                pil_img.save(img_buf, format="PNG")
                img_b64 = base64.b64encode(img_buf.getvalue()).decode()

                # Estimate iframe height from image aspect ratio (~700px container width)
                img_w, img_h = pil_img.size
                display_h = int(700 * img_h / img_w) + 30

                protected_html = f"""
<style>
  body {{ margin: 0; padding: 0; background: #0e1117; }}
  .wrap {{
    position: relative;
    user-select: none;
    -webkit-user-select: none;
    -moz-user-select: none;
  }}
  .wrap img {{
    width: 100%;
    display: block;
    -webkit-user-drag: none;
    user-drag: none;
    pointer-events: none;
  }}
  .overlay {{
    position: absolute;
    inset: 0;
    background: transparent;
    z-index: 10;
  }}
  .caption {{
    text-align: center;
    color: #aaa;
    font-family: sans-serif;
    font-size: 13px;
    padding: 6px 0;
  }}
</style>
<div class="wrap">
  <img src="data:image/png;base64,{img_b64}" draggable="false" />
  <div class="overlay" oncontextmenu="return false;"></div>
</div>
<div class="caption">Page {page_idx + 1} / {num_pages}</div>
<script>
  // Block right-click everywhere in this frame (removes "Inspect", "Save image", etc.)
  document.addEventListener('contextmenu', function(e) {{
    e.preventDefault(); return false;
  }}, true);

  // Block DevTools + save shortcuts
  document.addEventListener('keydown', function(e) {{
    var ctrl = e.ctrlKey || e.metaKey;
    var shift = e.shiftKey;

    // Ctrl+S, Ctrl+U, Ctrl+A, Ctrl+P (save/source/select/print)
    if (ctrl && !shift && ['s','S','u','U','a','A','p','P'].includes(e.key)) {{
      e.preventDefault(); return false;
    }}
    // Ctrl+Shift+I — DevTools Inspector
    // Ctrl+Shift+J — DevTools Console
    // Ctrl+Shift+C — DevTools Element picker
    if (ctrl && shift && ['i','I','j','J','c','C'].includes(e.key)) {{
      e.preventDefault(); return false;
    }}
    // Ctrl+I — also opens Inspector in some browsers
    if (ctrl && !shift && ['i','I'].includes(e.key)) {{
      e.preventDefault(); return false;
    }}
    // F12 — DevTools
    if (e.key === 'F12') {{
      e.preventDefault(); return false;
    }}
  }});

  // Block drag-and-drop of the image
  document.addEventListener('dragstart', function(e) {{
    e.preventDefault(); return false;
  }});
</script>
"""
                components.html(protected_html, height=display_h)

            except pikepdf.PasswordError:
                st.error("❌ This PDF was not locked by this application, or the application secret has changed.")
            except Exception as e:
                st.error(f"❌ Could not open file: {e}")


# ── Pantone database (approximate sRGB for coated paper) ──────────────────────
_PANTONE_DB: dict[str, tuple[int,int,int]] = {
    "PANTONE 100 C":(244,237,124),"PANTONE 101 C":(244,234,94),"PANTONE 102 C":(250,224,0),
    "PANTONE 109 C":(255,209,0),  "PANTONE 116 C":(255,196,37),"PANTONE 123 C":(255,199,44),
    "PANTONE 012 C":(255,210,0),  "PANTONE 1235 C":(255,184,28),
    "PANTONE 144 C":(237,109,0),  "PANTONE 151 C":(255,130,0), "PANTONE 158 C":(233,131,0),
    "PANTONE 165 C":(255,103,31), "PANTONE 021 C":(254,80,0),
    "PANTONE 485 C":(218,41,28),  "PANTONE 032 C":(239,51,64), "PANTONE 185 C":(229,0,51),
    "PANTONE 186 C":(200,16,46),  "PANTONE 193 C":(189,16,88), "PANTONE 199 C":(206,22,55),
    "PANTONE 1797 C":(211,47,47), "PANTONE 484 C":(167,41,32), "PANTONE 1525 C":(195,82,36),
    "PANTONE 7417 C":(207,96,76),
    "PANTONE 355 C":(0,158,73),   "PANTONE 340 C":(0,168,89),  "PANTONE 347 C":(0,155,72),
    "PANTONE 361 C":(0,175,65),   "PANTONE 369 C":(84,182,72), "PANTONE 376 C":(120,190,32),
    "PANTONE 279 C":(0,145,223),  "PANTONE 285 C":(0,114,206), "PANTONE 286 C":(0,51,160),
    "PANTONE 292 C":(100,160,220),"PANTONE 298 C":(100,191,230),"PANTONE 301 C":(0,83,155),
    "PANTONE 3005 C":(0,133,202), "PANTONE 541 C":(0,62,128),  "PANTONE 2945 C":(0,87,154),
    "PANTONE 072 C":(0,20,168),   "PANTONE Reflex Blue C":(0,20,137),
    "PANTONE Process Blue C":(0,133,202),"PANTONE 801 C":(0,179,227),
    "PANTONE 265 C":(138,102,204),"PANTONE 2685 C":(53,14,153),"PANTONE 2736 C":(67,55,188),
    "PANTONE 2745 C":(40,11,147),
    "PANTONE Black C":(30,30,30), "PANTONE Cool Gray 10 C":(99,102,106),
    "PANTONE Cool Gray 5 C":(178,180,178),"PANTONE Cool Gray 2 C":(215,216,214),
    "PANTONE 877 C":(141,143,145),"PANTONE 871 C":(172,148,100),"PANTONE 872 C":(192,158,82),
    "PANTONE 9100 C":(243,243,237),
    # Pastel & Neon P-series (approximate)
    "PANTONE P 158 C":(245,155,0),"PANTONE P 185 C":(229,0,51),"PANTONE P 355 C":(0,158,73),
    "PANTONE P 801 C":(0,179,227),"PANTONE P 2945 C":(0,87,154),"PANTONE P 3591 C":(200,220,120),
    "PANTONE 7547 C":(43,46,55),  "PANTONE 425 C":(84,88,90),
}

def _rgb_to_lab(r: int, g: int, b: int) -> tuple[float,float,float]:
    """sRGB (0-255) → CIE L*a*b* (D65)."""
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    R, G, B = lin(r), lin(g), lin(b)
    X = R*0.4124564 + G*0.3575761 + B*0.1804375
    Y = R*0.2126729 + G*0.7151522 + B*0.0721750
    Z = R*0.0193339 + G*0.1191920 + B*0.9503041
    X /= 0.95047; Z /= 1.08883
    def f(t): return t**(1/3) if t > 0.008856 else 7.787*t + 16/116
    return 116*f(Y)-16, 500*(f(X)-f(Y)), 200*(f(Y)-f(Z))

def _delta_e(rgb1: tuple, rgb2: tuple) -> float:
    L1,a1,b1 = _rgb_to_lab(*rgb1); L2,a2,b2 = _rgb_to_lab(*rgb2)
    return ((L1-L2)**2+(a1-a2)**2+(b1-b2)**2)**0.5

def _nearest_pantone(rgb: tuple) -> tuple[str, float]:
    best_name, best_de = "Unknown", float("inf")
    for name, prgb in _PANTONE_DB.items():
        de = _delta_e(rgb, prgb)
        if de < best_de:
            best_de = de; best_name = name
    return best_name, best_de

def _dominant_colors(img: Image.Image, n: int = 8) -> list[tuple]:
    """Return list of (R,G,B, pct%) dominant colors."""
    small = img.resize((150, 150), Image.LANCZOS)
    q = small.quantize(colors=n, method=2)
    pal = q.getpalette()[:n*3]
    counts: dict[int,int] = {}
    for px in q.getdata(): counts[px] = counts.get(px, 0) + 1
    total = sum(counts.values())
    result = []
    for idx, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        if idx*3+2 < len(pal):
            result.append((pal[idx*3], pal[idx*3+1], pal[idx*3+2], cnt/total*100))
    return result[:n]

def _grid_analysis(img_a: Image.Image, img_b: Image.Image, n: int = 6) -> list[dict]:
    """Divide images into n×n grid and compute per-cell similarity."""
    w, h = img_a.size
    cw, ch = w//n, h//n
    cells = []
    for row in range(n):
        for col in range(n):
            x1,y1 = col*cw, row*ch
            x2,y2 = min(x1+cw,w), min(y1+ch,h)
            ca = img_a.crop((x1,y1,x2,y2)); cb = img_b.crop((x1,y1,x2,y2))
            gray = ImageChops.difference(ca,cb).convert("L")
            pix  = sum(1 for px in gray.getdata() if px > 8)
            sim  = 1 - pix/((x2-x1)*(y2-y1))
            cells.append({"row":row,"col":col,"x1":x1,"y1":y1,"x2":x2,"y2":y2,
                          "sim":sim,"ca":ca,"cb":cb})
    return cells

def _best_transform(crop_a: Image.Image, crop_b: Image.Image) -> tuple[str, float]:
    """Try all 8 D4 transforms (rotations + flips) of crop_a vs crop_b.
    Returns (transform_name, best_similarity_0_to_1)."""
    tw, th = crop_b.size
    transforms = [
        ("none",              lambda img: img),
        ("rotate 90°",       lambda img: img.rotate(90,  expand=True)),
        ("rotate 180°",      lambda img: img.rotate(180, expand=True)),
        ("rotate 270°",      lambda img: img.rotate(270, expand=True)),
        ("flip horizontal",   lambda img: img.transpose(Image.FLIP_LEFT_RIGHT)),
        ("flip vertical",     lambda img: img.transpose(Image.FLIP_TOP_BOTTOM)),
        ("flip H + rotate 90°",  lambda img: img.transpose(Image.FLIP_LEFT_RIGHT).rotate(90,  expand=True)),
        ("flip H + rotate 270°", lambda img: img.transpose(Image.FLIP_LEFT_RIGHT).rotate(270, expand=True)),
    ]
    best_sim, best_name = 0.0, "none"
    for name, fn in transforms:
        try:
            t = fn(crop_a).resize((tw, th), Image.LANCZOS)
            gray = ImageChops.difference(t, crop_b).convert("L")
            pix  = sum(1 for px in gray.getdata() if px > 8)
            sim  = 1 - pix / (tw * th)
            if sim > best_sim:
                best_sim, best_name = sim, name
        except Exception:
            pass
    return best_name, best_sim


def _fit_pad(img: Image.Image, target_w: int, target_h: int,
             bg: tuple = (0, 0, 0)) -> Image.Image:
    """Fit image into target canvas preserving aspect ratio; pad with bg colour."""
    scale = min(target_w / img.width, target_h / img.height)
    new_w, new_h = int(img.width * scale), int(img.height * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas  = Image.new("RGB", (target_w, target_h), bg)
    canvas.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return canvas


def _strip_cropbox(pdf_bytes: bytes) -> bytes:
    """Remove CropBox from every page so pypdfium2 renders the full MediaBox."""
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                if "/CropBox" in page:
                    del page["/CropBox"]
            buf = io.BytesIO()
            pdf.save(buf)
            return buf.getvalue()
    except Exception:
        return pdf_bytes  # fall back to original on any error


def pdf_compare_tool():
    st.header("🔎 Compare PDFs")
    st.caption("Upload two PDFs to detect visual and text differences page by page.")
    ss = st.session_state

    col_a, col_b = st.columns(2)
    with col_a:
        file_a = st.file_uploader("PDF A", type="pdf", key="cmp_file_a")
    with col_b:
        file_b = st.file_uploader("PDF B", type="pdf", key="cmp_file_b")

    if not file_a or not file_b:
        st.info("Upload both PDFs to begin comparison.")
        return

    bytes_a = _strip_cropbox(file_a.read())
    bytes_b = _strip_cropbox(file_b.read())

    # Page counts
    try:
        doc_a = pdfium.PdfDocument(io.BytesIO(bytes_a))
        doc_b = pdfium.PdfDocument(io.BytesIO(bytes_b))
        n_a, n_b = len(doc_a), len(doc_b)
    except Exception as e:
        st.error(f"Could not open PDFs: {e}")
        return

    max_pages = max(n_a, n_b)

    pc, rc_a, rc_b = st.columns([0.4, 0.3, 0.3])
    with pc:
        page_idx = st.number_input(
            f"Page (A has {n_a}, B has {n_b})",
            min_value=1, max_value=max_pages, value=1, step=1,
            key="cmp_page"
        ) - 1
    with rc_a:
        rot_a = st.selectbox("Rotate PDF A", [0, 90, 180, 270], index=0, key="cmp_rot_a")
    with rc_b:
        rot_b = st.selectbox("Rotate PDF B", [0, 90, 180, 270], index=0, key="cmp_rot_b")

    if st.button("🔎 Compare", use_container_width=True, key="cmp_run"):
        img_a_c = img_b_c = None

        with st.spinner("Rendering pages…"):
            def render_page(doc, idx, total, rotation):
                if idx >= total:
                    return None
                pg = doc[idx]
                pil = pg.render(scale=1.5).to_pil().convert("RGB")
                if rotation:
                    pil = pil.rotate(-rotation, expand=True)
                return pil

            img_a = render_page(doc_a, page_idx, n_a, rot_a)
            img_b = render_page(doc_b, page_idx, n_b, rot_b)

        if img_a is None:
            st.warning(f"PDF A does not have page {page_idx + 1}.")
        if img_b is None:
            st.warning(f"PDF B does not have page {page_idx + 1}.")

        if img_a and img_b:
            if (img_a.width, img_a.height) != (img_b.width, img_b.height):
                cw = max(img_a.width, img_b.width)
                ch = max(img_a.height, img_b.height)
                ratio_a = img_a.width / img_a.height
                ratio_b = img_b.width / img_b.height
                if abs(ratio_a - ratio_b) > 0.05:
                    st.warning(
                        f"⚠️ Different aspect ratios: "
                        f"PDF A {img_a.width}×{img_a.height} (ratio {ratio_a:.2f}) vs "
                        f"PDF B {img_b.width}×{img_b.height} (ratio {ratio_b:.2f}). "
                        "Images are letterboxed for comparison — padding shows as red diff."
                    )
                img_a_c = _fit_pad(img_a, cw, ch)
                img_b_c = _fit_pad(img_b, cw, ch)
            else:
                img_a_c = img_a
                img_b_c = img_b
            w, h = img_a_c.size

            raw_diff = ImageChops.difference(img_a_c, img_b_c)
            gray_diff = raw_diff.convert("L")
            changed = sum(1 for px in gray_diff.getdata() if px > 8)
            total_px = w * h
            similarity = (1 - changed / total_px) * 100

            dimmed = ImageEnhance.Brightness(img_b_c).enhance(0.45)
            red_layer = Image.new("RGB", (w, h), (220, 50, 50))
            mask = gray_diff.point(lambda x: min(255, x * 8))
            diff_view = dimmed.copy()
            diff_view.paste(red_layer, mask=mask)
            amp_diff = ImageEnhance.Brightness(raw_diff).enhance(6.0)

            ss.cmp_img_a = img_a_c
            ss.cmp_img_b = img_b_c
            ss.cmp_similarity = similarity
            ss.cmp_changed = changed
            ss.cmp_total_px = total_px
            ss.cmp_diff_view = diff_view
            ss.cmp_amp_diff = amp_diff
            ss.cmp_page_label = f"page {page_idx + 1}"

            with st.spinner("Running grid & color analysis…"):
                ss.cmp_grid = _grid_analysis(img_a_c, img_b_c, n=6)
                ss.cmp_colors_a = _dominant_colors(img_a_c, n=6)
                ss.cmp_colors_b = _dominant_colors(img_b_c, n=6)

        # ── Text comparison (store in session state) ────────────────────────────────────────────────
        def _ocr_image(pil_img):
            if not _HAS_OCR or pil_img is None:
                return ""
            try:
                return pytesseract.image_to_string(pil_img).strip()
            except Exception:
                return ""

        ss.cmp_text_a = ""
        ss.cmp_text_b = ""
        ss.cmp_diff_lines = []
        ss.cmp_ocr_used = False
        ss.cmp_text_err = ""
        ss.cmp_no_text = False
        ss.cmp_no_ocr = False

        try:
            reader_a = PdfReader(io.BytesIO(bytes_a))
            reader_b = PdfReader(io.BytesIO(bytes_b))
            text_a = (reader_a.pages[page_idx].extract_text() or "").strip() if page_idx < len(reader_a.pages) else ""
            text_b = (reader_b.pages[page_idx].extract_text() or "").strip() if page_idx < len(reader_b.pages) else ""

            if not text_a and not text_b:
                if _HAS_OCR and img_a_c is not None and img_b_c is not None:
                    with st.spinner("No embedded text — running OCR on scanned pages…"):
                        text_a = _ocr_image(img_a_c)
                        text_b = _ocr_image(img_b_c)
                    ss.cmp_ocr_used = bool(text_a or text_b)
                    if not (text_a or text_b):
                        ss.cmp_no_text = True
                elif not _HAS_OCR:
                    ss.cmp_no_ocr = True
                else:
                    ss.cmp_no_text = True

            if text_a or text_b:
                ss.cmp_text_a = text_a
                ss.cmp_text_b = text_b
                ss.cmp_diff_lines = list(difflib.unified_diff(
                    text_a.splitlines(keepends=True),
                    text_b.splitlines(keepends=True),
                    fromfile=f"PDF A – page {page_idx + 1}",
                    tofile=f"PDF B – page {page_idx + 1}",
                    lineterm=""
                ))
        except Exception as e:
            ss.cmp_text_err = str(e)

    # ── Persistent Compare Results ───────────────────────────────────────────────────────────────────
    if ss.get("cmp_similarity") is not None:
        st.markdown("---")
        st.subheader(
            f"Similarity: {ss.cmp_similarity:.1f}%  —  "
            f"{ss.cmp_changed:,} / {ss.cmp_total_px:,} pixels differ "
            f"({ss.get('cmp_page_label', 'page 1')})"
        )
        ca, cb, cc, cd = st.columns(4)
        with ca:
            st.caption("**PDF A**")
            st.image(ss.cmp_img_a, use_container_width=True)
        with cb:
            st.caption("**PDF B**")
            st.image(ss.cmp_img_b, use_container_width=True)
        with cc:
            st.caption("**Differences (highlighted)**")
            st.image(ss.cmp_diff_view, use_container_width=True)
        with cd:
            st.caption("**Raw pixel diff (amplified)**")
            st.image(ss.cmp_amp_diff, use_container_width=True)

        # ── Grid Heatmap ───────────────────────────────────────────────────────────────────
        if ss.get("cmp_grid"):
            st.markdown("---")
            st.subheader("🟩 Region Similarity Heatmap")
            cells = ss.cmp_grid
            n_grid = 6
            cell_disp = 90
            hmap = Image.new("RGB", (n_grid * cell_disp, n_grid * cell_disp), (30, 30, 30))
            draw = ImageDraw.Draw(hmap)
            worst_cells = []
            for c in cells:
                sim = c["sim"]
                x1_d = c["col"] * cell_disp
                y1_d = c["row"] * cell_disp
                r_col = int((1 - sim) * 220)
                g_col = int(sim * 200)
                draw.rectangle([x1_d, y1_d, x1_d + cell_disp - 1, y1_d + cell_disp - 1], fill=(r_col, g_col, 40))
                draw.text((x1_d + 6, y1_d + 6), f"{sim * 100:.0f}%", fill=(255, 255, 255))
                if sim < 0.85:
                    worst_cells.append(c)

            hcol, ncol = st.columns([1, 2])
            with hcol:
                st.image(hmap, caption="Grid heatmap (green=similar, red=different)", use_container_width=True)
            with ncol:
                if not worst_cells:
                    st.success("All regions are highly similar (>85%).")
                else:
                    row_labels = ["Top", "Upper", "Middle", "Lower", "Bottom", "Last"]
                    col_labels = ["Left", "Center-left", "Center", "Center-right", "Right", "Far-right"]
                    st.warning(f"{len(worst_cells)} region(s) with low similarity detected:")
                    # Per-cell transform detection
                    for c in sorted(worst_cells, key=lambda x: x["sim"])[:6]:
                        tname, tsim = _best_transform(c["ca"], c["cb"])
                        rl = row_labels[min(c["row"], 5)]
                        cl = col_labels[min(c["col"], 5)]
                        base = c["sim"] * 100
                        tsim_pct = tsim * 100
                        note = f"**{rl}-{cl}**: {base:.0f}% similar"
                        if tname != "none" and tsim_pct > base + 10:
                            note += f" — **{tname}** improves to **{tsim_pct:.0f}%**"
                        st.markdown(f"- {note}")

                    # Merged 2×2 cluster detection for logos spanning multiple cells
                    worst_set = {(c["row"], c["col"]) for c in worst_cells}
                    n_grid = 6
                    reported_clusters = set()
                    for row in range(n_grid - 1):
                        for col in range(n_grid - 1):
                            quad = {(row+dr, col+dc) for dr in range(2) for dc in range(2)}
                            if quad.issubset(worst_set) and (row, col) not in reported_clusters:
                                reported_clusters.add((row, col))
                                group = [c for c in cells if (c["row"], c["col"]) in quad]
                                x1m = min(c["x1"] for c in group)
                                y1m = min(c["y1"] for c in group)
                                x2m = max(c["x2"] for c in group)
                                y2m = max(c["y2"] for c in group)
                                ca_m = ss.cmp_img_a.crop((x1m, y1m, x2m, y2m))
                                cb_m = ss.cmp_img_b.crop((x1m, y1m, x2m, y2m))
                                tname_m, tsim_m = _best_transform(ca_m, cb_m)
                                rl0 = row_labels[min(row, 5)]
                                cl0 = col_labels[min(col, 5)]
                                if tname_m != "none" and tsim_m * 100 > 40:
                                    st.info(
                                        f"🧩 2×2 cluster at {rl0}-{cl0}: "
                                        f"merged region is **{tname_m}** "
                                        f"(match {tsim_m*100:.0f}%)"
                                    )

        # ── Color / Pantone Analysis ───────────────────────────────────────────────────────────────
        if ss.get("cmp_colors_a") and ss.get("cmp_colors_b"):
            st.markdown("---")
            st.subheader("🎨 Color & Pantone Analysis")

            def _render_color_table(colors, label):
                st.caption(f"**{label}** — dominant colors")
                for (r, g, b, pct) in colors:
                    if pct < 0.5:
                        continue
                    name, de = _nearest_pantone((r, g, b))
                    hex_col = f"#{r:02x}{g:02x}{b:02x}"
                    swatch = (
                        f'<span style="display:inline-block;width:20px;height:20px;'
                        f'background:{hex_col};border:1px solid #555;border-radius:3px;'
                        f'vertical-align:middle;margin-right:8px;"></span>'
                    )
                    badge = "✅" if de < 5 else ("⚠️" if de < 15 else "❌")
                    st.markdown(
                        f'{swatch} `{hex_col}` {pct:.1f}% — **{name}** ΔE={de:.1f} {badge}',
                        unsafe_allow_html=True,
                    )

            c_left, c_right = st.columns(2)
            with c_left:
                _render_color_table(ss.cmp_colors_a, "PDF A")
            with c_right:
                _render_color_table(ss.cmp_colors_b, "PDF B")

            st.caption("**Colors in PDF A not found in PDF B (ΔE > 10):**")
            mismatches = []
            for (ra, ga, ba, pcta) in ss.cmp_colors_a:
                if pcta < 1.0:
                    continue
                best_de = min(
                    _delta_e((ra, ga, ba), (rb, gb, bb))
                    for (rb, gb, bb, _) in ss.cmp_colors_b
                )
                if best_de > 10:
                    name_a, _ = _nearest_pantone((ra, ga, ba))
                    hex_a = f"#{ra:02x}{ga:02x}{ba:02x}"
                    mismatches.append(
                        f'`{hex_a}` ({pcta:.1f}%) **{name_a}** — no close match in PDF B (ΔE={best_de:.1f})'
                    )
            if mismatches:
                for m in mismatches:
                    st.markdown(f"- {m}")
            else:
                st.success("All major colors in PDF A have close matches in PDF B.")

        # ── Text Diff ───────────────────────────────────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("📄 Text Content Differences")
        if ss.get("cmp_text_err"):
            st.error(f"Text extraction failed: {ss.cmp_text_err}")
        elif ss.get("cmp_no_ocr"):
            st.info("No embedded text found. Install **Tesseract** and `pytesseract` to enable OCR on scanned pages.")
            if _OCR_ERR:
                st.caption(f"OCR load error: `{_OCR_ERR}`")
        elif ss.get("cmp_no_text"):
            st.info("No extractable text found on this page in either PDF.")
        if ss.get("cmp_ocr_used"):
            st.caption("📷 Text extracted via OCR (Tesseract)")

        _text_a = ss.get("cmp_text_a", "")
        _text_b = ss.get("cmp_text_b", "")
        _diff_lines = ss.get("cmp_diff_lines", [])
        if _text_a or _text_b:
            if not _diff_lines:
                st.success("✅ Text content is identical on this page.")
            else:
                added   = sum(1 for l in _diff_lines if l.startswith("+") and not l.startswith("+++"))
                removed = sum(1 for l in _diff_lines if l.startswith("-") and not l.startswith("---"))
                st.warning(f"{added} line(s) added, {removed} line(s) removed.")
                with st.expander("Show full text diff", expanded=True):
                    html_lines = ["<pre style='font-size:13px;line-height:1.5;white-space:pre-wrap;'>"]
                    for line in _diff_lines:
                        esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        if line.startswith("+++") or line.startswith("---"):
                            html_lines.append(f"<span style='color:#888'>{esc}</span>")
                        elif line.startswith("+"):
                            html_lines.append(f"<span style='background:#1a3a1a;color:#6fdc8c'>{esc}</span>")
                        elif line.startswith("-"):
                            html_lines.append(f"<span style='background:#3a1a1a;color:#ff8389'>{esc}</span>")
                        elif line.startswith("@@"):
                            html_lines.append(f"<span style='color:#78a9ff'>{esc}</span>")
                        else:
                            html_lines.append(f"<span style='color:#ccc'>{esc}</span>")
                    html_lines.append("</pre>")
                    st.html("\n".join(html_lines))
            with st.expander("Show extracted text side by side"):
                ta, tb = st.columns(2)
                with ta:
                    st.caption("PDF A")
                    st.text(_text_a or "(no text)")
                with tb:
                    st.caption("PDF B")
                    st.text(_text_b or "(no text)")

    doc_a.close()
    doc_b.close()

    # ── Drag Alignment ─────────────────────────────────────────────────────────
    if ss.get("cmp_img_a") is not None and ss.get("cmp_img_b") is not None:
        st.markdown("---")
        st.subheader("🖱️ Drag Alignment")
        st.caption(
            "Drag **PDF A** (top layer) over **PDF B** to align them visually, "
            "adjust opacity and scale, then click **Save Alignment** → **Compare Aligned**."
        )

        # Downscale both images to max 900px wide for the HTML overlay
        def _b64_resized(img: Image.Image, max_w: int = 900) -> tuple[str, float]:
            scale_f = min(1.0, max_w / img.width)
            disp = img.resize((int(img.width * scale_f), int(img.height * scale_f)), Image.LANCZOS)
            buf = io.BytesIO()
            disp.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode(), scale_f

        b64_a, scale_f_a = _b64_resized(ss.cmp_img_a)
        b64_b, scale_f_b = _b64_resized(ss.cmp_img_b)
        disp_w_b = int(ss.cmp_img_b.width  * scale_f_b)
        disp_h_b = int(ss.cmp_img_b.height * scale_f_b)

        # Read saved alignment from URL params (pixels in original image space)
        saved_ox = int(st.query_params.get("cmp_ox", 0))
        saved_oy = int(st.query_params.get("cmp_oy", 0))
        saved_sc = float(st.query_params.get("cmp_sc", "1.0"))

        overlay_html = f"""
<style>
  html, body {{ margin: 0; padding: 0; background: #0e1117; font-family: sans-serif; }}
  #controls {{
    display: flex; flex-wrap: wrap; align-items: center; gap: 14px;
    padding: 8px 12px; background: rgba(20,20,35,0.9);
    color: #ccc; font-size: 13px;
  }}
  #controls label {{ display: flex; align-items: center; gap: 6px; }}
  #controls input[type=range] {{ width: 130px; }}
  #status {{ color: #78a9ff; font-size: 12px; min-width: 220px; }}
  .btn {{
    background: rgba(40,80,160,0.85); color: #ddd;
    border: 1px solid #557; border-radius: 5px;
    padding: 5px 14px; cursor: pointer; font-size: 13px;
  }}
  .btn:hover {{ background: rgba(60,110,200,0.95); }}
  .btn.reset {{ background: rgba(80,40,40,0.85); }}
  .btn.reset:hover {{ background: rgba(140,50,50,0.95); }}
  #wrap {{
    position: relative;
    width: {disp_w_b}px; height: {disp_h_b}px;
    overflow: hidden; cursor: crosshair;
  }}
  #base {{ position: absolute; top:0; left:0; width:{disp_w_b}px; height:{disp_h_b}px; }}
  #ovl  {{ position: absolute; top:0; left:0; cursor: grab; opacity: 0.55; transform-origin: top left; }}
  #ovl.drag {{ cursor: grabbing; }}
</style>

<div id="controls">
  <label>Opacity <input type="range" id="op" min="0" max="100" value="55"
    oninput="ovl.style.opacity=this.value/100; document.getElementById('op-v').textContent=this.value+'%'">
    <span id="op-v">55%</span></label>
  <label>Scale <input type="range" id="sc" min="10" max="300" value="{int(saved_sc*100)}"
    oninput="setScale(this.value)">
    <span id="sc-v">{saved_sc:.2f}×</span></label>
  <span id="status">X: {saved_ox}px &nbsp; Y: {saved_oy}px &nbsp; Scale: {saved_sc:.2f}×</span>
  <button class="btn" onclick="saveAlign()">✓ Save Alignment</button>
  <button class="btn reset" onclick="resetAlign()">↺ Reset</button>
</div>

<div id="wrap">
  <img id="base" src="data:image/png;base64,{b64_b}" draggable="false"/>
  <img id="ovl"  src="data:image/png;base64,{b64_a}" draggable="false"/>
</div>

<script>
  var DISP_SCALE_B = {scale_f_b};   // display px / original px  for img_b canvas
  var DISP_SCALE_A = {scale_f_a};   // display px / original px  for img_a
  var ovl = document.getElementById('ovl');

  // Initialise from saved alignment (convert original px → display px)
  var ox = {saved_ox} * DISP_SCALE_B;
  var oy = {saved_oy} * DISP_SCALE_B;
  var sc = {saved_sc};

  function applyTransform() {{
    ovl.style.transform = 'translate('+ox+'px,'+oy+'px) scale('+sc+')';
    document.getElementById('status').innerHTML =
      'X: '+Math.round(ox/DISP_SCALE_B)+'px &nbsp; Y: '+Math.round(oy/DISP_SCALE_B)+'px &nbsp; Scale: '+sc.toFixed(2)+'&times;';
  }}

  function setScale(val) {{
    sc = val / 100;
    document.getElementById('sc-v').textContent = sc.toFixed(2)+'×';
    applyTransform();
  }}

  // ── Drag ──────────────────────────────────────────────────────────────────
  var drag = false, sx = 0, sy = 0;
  ovl.addEventListener('mousedown', function(e) {{
    drag = true; sx = e.clientX - ox; sy = e.clientY - oy;
    ovl.classList.add('drag'); e.preventDefault();
  }});
  document.addEventListener('mousemove', function(e) {{
    if (!drag) return;
    ox = e.clientX - sx; oy = e.clientY - sy;
    applyTransform();
  }});
  document.addEventListener('mouseup', function() {{
    drag = false; ovl.classList.remove('drag');
  }});

  // Touch support
  ovl.addEventListener('touchstart', function(e) {{
    var t = e.touches[0];
    drag = true; sx = t.clientX - ox; sy = t.clientY - oy; e.preventDefault();
  }}, {{passive: false}});
  document.addEventListener('touchmove', function(e) {{
    if (!drag) return;
    var t = e.touches[0];
    ox = t.clientX - sx; oy = t.clientY - sy;
    applyTransform(); e.preventDefault();
  }}, {{passive: false}});
  document.addEventListener('touchend', function() {{ drag = false; }});

  // ── Save to parent URL params ─────────────────────────────────────────────
  function saveAlign() {{
    var realOx = Math.round(ox / DISP_SCALE_B);
    var realOy = Math.round(oy / DISP_SCALE_B);
    try {{
      var url = new URL(window.parent.location.href);
      url.searchParams.set('cmp_ox', realOx);
      url.searchParams.set('cmp_oy', realOy);
      url.searchParams.set('cmp_sc', sc.toFixed(4));
      window.parent.history.replaceState({{}}, '', url.toString());
      document.getElementById('status').innerHTML =
        '&#10003; Saved &mdash; X: '+realOx+'px &nbsp; Y: '+realOy+'px &nbsp; Scale: '+sc.toFixed(2)+'&times;';
    }} catch(e) {{
      document.getElementById('status').textContent = 'Could not save (cross-origin). Use sliders below.';
    }}
  }}

  function resetAlign() {{
    ox = 0; oy = 0; sc = 1.0;
    document.getElementById('sc').value = 100;
    document.getElementById('sc-v').textContent = '1.00×';
    applyTransform();
  }}

  // ── iframe auto-height ────────────────────────────────────────────────────
  function sendH() {{
    var h = document.body.scrollHeight;
    window.parent.postMessage({{type:'streamlit:setFrameHeight', height:h}}, '*');
  }}
  document.getElementById('base').onload = sendH;
  if (document.getElementById('base').complete) sendH();
  window.addEventListener('resize', function(){{ setTimeout(sendH,50); }});

  applyTransform();
</script>
"""
        components.html(overlay_html, height=disp_h_b + 80)

        st.caption(f"Saved alignment: **X={saved_ox}px, Y={saved_oy}px, Scale={saved_sc:.2f}×** — click Save Alignment in the overlay, then press the button below.")

        if st.button("🔎 Compare Aligned", use_container_width=True, key="cmp_aligned_run"):
            img_a_src = ss.cmp_img_a
            img_b_src = ss.cmp_img_b

            iw_a, ih_a = img_a_src.size
            new_w = max(1, int(iw_a * saved_sc))
            new_h = max(1, int(ih_a * saved_sc))
            img_a_scaled = img_a_src.resize((new_w, new_h), Image.LANCZOS)

            bw, bh = img_b_src.size
            ox_, oy_ = saved_ox, saved_oy

            # Overlapping region in img_b coordinates
            left   = max(0, ox_);        top    = max(0, oy_)
            right  = min(bw, ox_ + new_w); bottom = min(bh, oy_ + new_h)

            if right <= left or bottom <= top:
                st.warning("No overlapping area — drag PDF A so it overlaps with PDF B.")
            else:
                crop_b = img_b_src.crop((left, top, right, bottom))
                crop_a = img_a_scaled.crop((left - ox_, top - oy_, right - ox_, bottom - oy_))

                raw   = ImageChops.difference(crop_a, crop_b)
                gray  = raw.convert("L")
                thr   = 8
                changed = sum(1 for px in gray.getdata() if px > thr)
                total   = (right - left) * (bottom - top)
                sim     = (1 - changed / total) * 100

                dimmed    = ImageEnhance.Brightness(crop_b).enhance(0.45)
                red_layer = Image.new("RGB", crop_b.size, (220, 50, 50))
                mask      = gray.point(lambda v: min(255, v * 8))
                hi_diff   = dimmed.copy(); hi_diff.paste(red_layer, mask=mask)
                amp       = ImageEnhance.Brightness(raw).enhance(6.0)

                st.markdown(f"**Aligned region similarity: {sim:.1f}%** — {changed:,} / {total:,} pixels differ")
                c1, c2, c3, c4 = st.columns(4)
                with c1: st.caption("PDF A (aligned crop)"); st.image(crop_a, use_container_width=True)
                with c2: st.caption("PDF B (same region)");  st.image(crop_b, use_container_width=True)
                with c3: st.caption("Diff (highlighted)");   st.image(hi_diff, use_container_width=True)
                with c4: st.caption("Raw diff (amplified)"); st.image(amp, use_container_width=True)

    # ── Region-of-Interest comparison ──────────────────────────────────────────
    if ss.get("cmp_img_a") is not None and ss.get("cmp_img_b") is not None:
        st.markdown("---")
        st.subheader("🔍 Region of Interest Comparison")
        st.caption("Adjust the sliders to frame a specific area, then click **Compare Region**.")

        ref = ss.cmp_img_a
        iw, ih = ref.size

        c_prev, c_ctrl = st.columns([0.55, 0.45])
        with c_ctrl:
            left_pct   = st.slider("Left %",   0, 100,  0, key="roi_left")
            right_pct  = st.slider("Right %",  0, 100, 100, key="roi_right")
            top_pct    = st.slider("Top %",    0, 100,  0, key="roi_top")
            bottom_pct = st.slider("Bottom %", 0, 100, 100, key="roi_bottom")
            run_roi    = st.button("🔍 Compare Region", use_container_width=True, key="roi_run")

        # Pixel coordinates
        x1 = int(left_pct   / 100 * iw)
        x2 = int(right_pct  / 100 * iw)
        y1 = int(top_pct    / 100 * ih)
        y2 = int(bottom_pct / 100 * ih)

        # Draw live rectangle on PDF A preview
        preview = ref.copy()
        draw = ImageDraw.Draw(preview)
        draw.rectangle([x1, y1, x2, y2], outline=(255, 50, 50), width=max(2, iw // 150))

        with c_prev:
            st.caption("**PDF A** — red box = selected region")
            st.image(preview, use_container_width=True)

        if run_roi:
            if x2 <= x1 or y2 <= y1:
                st.warning("Region has zero area — adjust the sliders so Right > Left and Bottom > Top.")
            else:
                crop_a = ss.cmp_img_a.crop((x1, y1, x2, y2))
                crop_b = ss.cmp_img_b.crop((x1, y1, x2, y2))

                raw   = ImageChops.difference(crop_a, crop_b)
                gray  = raw.convert("L")
                threshold = 8
                changed   = sum(1 for px in gray.getdata() if px > threshold)
                total     = (x2 - x1) * (y2 - y1)
                sim       = (1 - changed / total) * 100

                dimmed    = ImageEnhance.Brightness(crop_b).enhance(0.45)
                red_layer = Image.new("RGB", crop_b.size, (220, 50, 50))
                mask      = gray.point(lambda v: min(255, v * 8))
                roi_diff  = dimmed.copy()
                roi_diff.paste(red_layer, mask=mask)
                amp       = ImageEnhance.Brightness(raw).enhance(6.0)

                st.markdown(f"**Region similarity: {sim:.1f}%** — {changed:,} / {total:,} pixels differ")
                r1, r2, r3, r4 = st.columns(4)
                with r1:
                    st.caption("Region — PDF A")
                    st.image(crop_a, use_container_width=True)
                with r2:
                    st.caption("Region — PDF B")
                    st.image(crop_b, use_container_width=True)
                with r3:
                    st.caption("Diff (highlighted)")
                    st.image(roi_diff, use_container_width=True)
                with r4:
                    st.caption("Raw diff (amplified)")
                    st.image(amp, use_container_width=True)


_TAB_FUNC_MAP = {
    "tab_watermark":        run_watermark_tool,
    "tab_merge":            merge_pdf_tool,
    "tab_split":            split_pdf_tool,
    "tab_extract_pages":    extract_pages_tool,
    "tab_extract_text":     extract_text_tool,
    "tab_convert_images":   convert_to_images_tool,
    "tab_compress":         compress_pdf_tool,
    "tab_readonly":         readonly_pdf_tool,
    "tab_protected_viewer": protected_pdf_tool,
    "tab_compare":          pdf_compare_tool,
    "tab_admin":            admin_dashboard_tool,
}

_render_topbar()
for _i, _pkey in enumerate(_tab_perm_keys):
    with tabs[_i]:
        _TAB_FUNC_MAP[_pkey]()