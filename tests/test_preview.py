"""Verify lazy single-page preview rendering against a 120-page PDF.

Note: get_pdf_preview_info / render_pdf_page_to_image are nested inside
run_watermark_tool() and wrapped in st.cache_data, so they cannot be imported
directly. The two helpers below mirror their bodies; keep them in sync if the
originals in index.py change.
"""
import io, time
import pypdfium2 as pdfium
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import A4

PREVIEW_LIMIT = 100
SCALE = 1.8

# Build a 120-page PDF (longer than the limit, to exercise truncation)
buf = io.BytesIO()
c = rl_canvas.Canvas(buf, pagesize=A4)
for i in range(120):
    c.drawString(100, 700, f"Page {i+1} of 120")
    c.showPage()
c.save()
pdf_bytes = buf.getvalue()
print(f"Test PDF: {len(pdf_bytes)/1024:.0f} KB, 120 pages\n")

# --- mirrors get_pdf_preview_info ---
def preview_info(data, limit):
    pdf = pdfium.PdfDocument(io.BytesIO(data))
    total = len(pdf)
    pages = min(total, limit)
    first = pdf.get_page(0)
    size = first.get_size()
    first.close(); pdf.close()
    return pages, total, size

# --- mirrors render_pdf_page_to_image ---
def render_page(data, scale, idx):
    pdf = pdfium.PdfDocument(io.BytesIO(data))
    if not (0 <= idx < len(pdf)):
        pdf.close(); return None
    pg = pdf.get_page(idx)
    img = pg.render(scale=scale).to_pil()
    pg.close(); pdf.close()
    return img

fails = []
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond: fails.append(name)

print("== metadata read (no rasterizing) ==")
t0 = time.perf_counter()
pages, total, size = preview_info(pdf_bytes, PREVIEW_LIMIT)
t_info = time.perf_counter() - t0
check(f"capped at limit (got {pages})", pages == 100)
check(f"true total reported (got {total})", total == 120)
check("page size looks like A4 pt", 590 < size[0] < 600 and 835 < size[1] < 848)
check(f"metadata read is fast ({t_info*1000:.0f} ms)", t_info < 1.0)

print("\n== single-page render ==")
for idx in (0, 49, 99):
    t0 = time.perf_counter()
    img = render_page(pdf_bytes, SCALE, idx)
    dt = time.perf_counter() - t0
    check(f"page {idx+1} renders ({img.size[0]}x{img.size[1]}, {dt*1000:.0f} ms)", img is not None)

print("\n== out-of-range guard ==")
check("negative index returns None", render_page(pdf_bytes, SCALE, -1) is None)
check("index past end returns None", render_page(pdf_bytes, SCALE, 999) is None)

print("\n== cost: lazy vs eager ==")
# Measure the retained decoded bitmaps, not tracemalloc: PIL pixel buffers are C
# allocations that Python's allocator tracing does not see.
def bitmap_mb(imgs):
    return sum(i.size[0] * i.size[1] * len(i.getbands()) for i in imgs) / 1024 / 1024

t0 = time.perf_counter()
one = render_page(pdf_bytes, SCALE, 0)
t_lazy = time.perf_counter() - t0
m_lazy = bitmap_mb([one])

t0 = time.perf_counter()
pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
eager = [pdf.get_page(i).render(scale=SCALE).to_pil() for i in range(PREVIEW_LIMIT)]
t_eager = time.perf_counter() - t0
m_eager = bitmap_mb(eager)
pdf.close()

print(f"  lazy  (1 page):    {t_lazy:6.2f}s   retained bitmaps {m_lazy:7.1f} MB")
print(f"  eager (100 pages): {t_eager:6.2f}s   retained bitmaps {m_eager:7.1f} MB")
print(f"  -> first paint {t_eager/t_lazy:.0f}x faster, {m_eager-m_lazy:.0f} MB less held per cached PDF")
check("lazy render is much faster than eager", t_lazy * 10 < t_eager)
check("lazy render retains far less bitmap memory", m_lazy * 10 < m_eager)

print("\n" + ("ALL PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
raise SystemExit(1 if fails else 0)
