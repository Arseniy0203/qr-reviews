"""QR code rendering: PNG / SVG / PDF for one point and A4 print sheets with crop marks.

Print requirements:
- error correction level Q (25%), never boosted, so the result is predictable;
- standard quiet zone of 4 modules;
- PNG is at least 1000 px wide (the QR itself, caption excluded);
- under each code: "branch · point" and the short link as plain text, in case the
  camera cannot read the code.
"""

import io
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

import segno
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4, A6
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from app.schemas.qr_point import PrintLayout

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_REGULAR = FONTS_DIR / "DejaVuSans.ttf"
FONT_BOLD = FONTS_DIR / "DejaVuSans-Bold.ttf"
PDF_FONT = "DejaVuSans"
PDF_FONT_BOLD = "DejaVuSans-Bold"

ERROR_LEVEL = "q"
QUIET_ZONE = 4  # modules
MIN_PNG_SIZE = 1000  # px

LAYOUTS: dict[str, tuple[int, int]] = {"1x1": (1, 1), "2x3": (2, 3), "3x4": (3, 4)}  # cols, rows
SHEET_MARGIN = 14 * mm
CROP_MARK_GAP = 2 * mm
CROP_MARK_LENGTH = 6 * mm


@dataclass(frozen=True)
class QRLabel:
    url: str  # encoded into the code
    short_url: str  # printed under the code
    caption: str  # "Филиал · Точка"


def make_qr(url: str) -> segno.QRCode:
    return segno.make(url, error=ERROR_LEVEL, micro=False, boost_error=False)


def _dark_runs(qr: segno.QRCode) -> Iterator[tuple[int, int, int]]:
    """Yield (row, start_col, length) of horizontal dark runs, quiet zone included."""
    for y, row in enumerate(qr.matrix_iter(scale=1, border=QUIET_ZONE)):
        x = 0
        cells = list(row)
        while x < len(cells):
            if cells[x]:
                start = x
                while x < len(cells) and cells[x]:
                    x += 1
                yield y, start, x - start
            else:
                x += 1


def _symbol_modules(qr: segno.QRCode) -> int:
    return qr.symbol_size(scale=1, border=QUIET_ZONE)[0]


# --------------------------------------------------------------------------- PNG


@lru_cache(maxsize=16)
def _pil_font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def _fit_pil(draw: ImageDraw.ImageDraw, text: str, bold: bool, size: int, max_width: int):
    """Largest font (down to 60% of `size`) that fits; then truncate with an ellipsis."""
    for candidate in range(size, max(int(size * 0.6), 8) - 1, -2):
        font = _pil_font(bold, candidate)
        if draw.textlength(text, font=font) <= max_width:
            return text, font
    font = _pil_font(bold, max(int(size * 0.6), 8))
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…", font


def render_png(label: QRLabel, with_caption: bool = True) -> bytes:
    qr = make_qr(label.url)
    scale = math.ceil(MIN_PNG_SIZE / _symbol_modules(qr))
    raw = io.BytesIO()
    qr.save(raw, kind="png", scale=scale, border=QUIET_ZONE)
    if not with_caption:
        return raw.getvalue()

    code = Image.open(io.BytesIO(raw.getvalue())).convert("L")
    width = code.width
    margin = scale * QUIET_ZONE
    caption_size, link_size = round(width * 0.045), round(width * 0.034)
    caption_height = round(caption_size * 1.5 + link_size * 1.5 + margin * 0.6)
    image = Image.new("L", (width, code.height + caption_height), 255)
    image.paste(code, (0, 0))
    draw = ImageDraw.Draw(image)
    max_text = width - 2 * margin
    y = code.height  # text starts below the quiet zone, never inside it
    for text, bold, size in (
        (label.caption, True, caption_size),
        (label.short_url, False, link_size),
    ):
        fitted, font = _fit_pil(draw, text, bold, size, max_text)
        draw.text((width / 2, y), fitted, font=font, fill=0, anchor="mt")
        y += round(size * 1.5)

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


# --------------------------------------------------------------------------- SVG


def render_svg(label: QRLabel, with_caption: bool = True) -> bytes:
    """Vector QR in module units; captions use DejaVu Sans with system fallbacks."""
    qr = make_qr(label.url)
    size = _symbol_modules(qr)
    path = "".join(f"M{x} {y}h{n}v1h-{n}z" for y, x, n in _dark_runs(qr))
    caption_height = 7 if with_caption else 0
    height = size + caption_height
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {height}" '
        f'width="{size * 10}" height="{height * 10}" shape-rendering="crispEdges">',
        f'<rect width="{size}" height="{height}" fill="#fff"/>',
        f'<path fill="#000" d="{path}"/>',
    ]
    if with_caption:
        font = 'font-family="\'DejaVu Sans\', Arial, sans-serif" text-anchor="middle"'
        center = size / 2
        parts.append(
            f'<text x="{center}" y="{size + 1.5}" font-size="2" font-weight="bold" {font}>'
            f"{escape(label.caption)}</text>"
        )
        parts.append(
            f'<text x="{center}" y="{size + 4.2}" font-size="1.5" {font}>'
            f"{escape(label.short_url)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts).encode()


# --------------------------------------------------------------------------- PDF


@lru_cache(maxsize=1)
def _register_pdf_fonts() -> None:
    pdfmetrics.registerFont(TTFont(PDF_FONT, str(FONT_REGULAR)))
    pdfmetrics.registerFont(TTFont(PDF_FONT_BOLD, str(FONT_BOLD)))


def _fit_pdf(text: str, font: str, size: float, max_width: float) -> tuple[str, float]:
    min_size = max(size * 0.6, 5)
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 0.5
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text, size
    while text and pdfmetrics.stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text + "…", size


def _draw_qr(canvas: Canvas, qr: segno.QRCode, x: float, y_top: float, side: float) -> None:
    """Vector modules; runs are merged and slightly overlapped to avoid hairline seams."""
    module = side / _symbol_modules(qr)
    canvas.setFillGray(0)
    for row, col, length in _dark_runs(qr):
        canvas.rect(
            x + col * module,
            y_top - (row + 1) * module,
            length * module,
            module + 0.02,
            stroke=0,
            fill=1,
        )


def _draw_label(
    canvas: Canvas, label: QRLabel, x: float, y: float, width: float, height: float
) -> None:
    """Draw QR + captions centered in the box whose lower-left corner is (x, y)."""
    padding = min(width, height) * 0.06
    caption_size = min(max(width * 0.045, 7), 22)
    link_size = caption_size * 0.8
    caption_block = caption_size * 2.7
    side = min(width - 2 * padding, height - 2 * padding - caption_block)
    qr_x = x + (width - side) / 2
    qr_top = y + height - padding
    _draw_qr(canvas, make_qr(label.url), qr_x, qr_top, side)

    # Captions start right below the quiet zone (glyph tops stay outside it).
    text_width = width - 2 * padding
    baseline = qr_top - side - caption_size
    caption, size = _fit_pdf(label.caption, PDF_FONT_BOLD, caption_size, text_width)
    canvas.setFont(PDF_FONT_BOLD, size)
    canvas.drawCentredString(x + width / 2, baseline, caption)
    link, size = _fit_pdf(label.short_url, PDF_FONT, link_size, text_width)
    canvas.setFont(PDF_FONT, size)
    canvas.drawCentredString(x + width / 2, baseline - caption_size * 1.35, link)


def _new_canvas(buffer: io.BytesIO, pagesize: tuple[float, float]) -> Canvas:
    _register_pdf_fonts()
    canvas = Canvas(buffer, pagesize=pagesize, initialFontName=PDF_FONT)
    canvas.setTitle("QR-коды для отзывов")
    canvas.setCreator("QR Отзывы")
    return canvas


def render_pdf(label: QRLabel) -> bytes:
    """A single code on an A6 page (fits a table tent or a counter stand)."""
    buffer = io.BytesIO()
    canvas = _new_canvas(buffer, A6)
    width, height = A6
    _draw_label(canvas, label, 0, 0, width, height)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _draw_crop_marks(
    canvas: Canvas, left: float, bottom: float, cell_w: float, cell_h: float, cols: int, rows: int
) -> None:
    """Marks in the page margins on every cut line, never over the printed area."""
    right, top = left + cols * cell_w, bottom + rows * cell_h
    canvas.setLineWidth(0.3)
    canvas.setStrokeGray(0)
    start, end = CROP_MARK_GAP, CROP_MARK_GAP + CROP_MARK_LENGTH
    for i in range(cols + 1):
        x = left + i * cell_w
        canvas.line(x, top + start, x, top + end)
        canvas.line(x, bottom - start, x, bottom - end)
    for j in range(rows + 1):
        y = bottom + j * cell_h
        canvas.line(left - start, y, left - end, y)
        canvas.line(right + start, y, right + end, y)


def render_sheet(labels: Sequence[QRLabel], layout: PrintLayout) -> bytes:
    """A4 sheets: cells laid out edge to edge, cut along the crop marks."""
    cols, rows = LAYOUTS[layout]
    page_w, page_h = A4
    cell_w = (page_w - 2 * SHEET_MARGIN) / cols
    cell_h = (page_h - 2 * SHEET_MARGIN) / rows
    per_page = cols * rows

    buffer = io.BytesIO()
    canvas = _new_canvas(buffer, A4)
    for page_start in range(0, len(labels), per_page):
        for index, label in enumerate(labels[page_start : page_start + per_page]):
            col, row = index % cols, index // cols
            x = SHEET_MARGIN + col * cell_w
            y = page_h - SHEET_MARGIN - (row + 1) * cell_h
            _draw_label(canvas, label, x, y, cell_w, cell_h)
        _draw_crop_marks(canvas, SHEET_MARGIN, SHEET_MARGIN, cell_w, cell_h, cols, rows)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
