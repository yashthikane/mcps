"""Documents Donna makes: PDF and Markdown files from Markdown that the chat model writes.

The tool (tools/file_tools.py) passes a title and Markdown; this module saves `data/files/<name>`
and returns the name. The chat shows a file card from that name (Chat.tsx `Files`), channels send
the file as a document, and GET /api/v1/files/{name} serves it. Like pictures, no file content
goes back through the model.

PDFs are drawn with fpdf2 from the Markdown rendered to HTML (markdown-it-py), in the bundled Noto
fonts (donna/fonts, SIL Open Font License), so ₹ and Devanagari (Hindi, Marathi) print correctly on
Windows and on the Linux server alike.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from . import DATA_DIR

DIR = DATA_DIR / "files"
FONTS = Path(__file__).parent / "fonts"
# doc_<12 hex>-<slug of the title>.<ext>: the slug gives the download a readable name.
NAME = re.compile(r"doc_[0-9a-f]{12}(?:-[a-z0-9-]{1,60})?\.(?:pdf|md)")
FORMATS = ("pdf", "md")
MIMETYPES = {"pdf": "application/pdf", "md": "text/markdown"}
MAX_CHARS = 200_000


class FileError(Exception):
    """A failure the user can act on; the message is shown as it is."""


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:60].strip("-")


def path(name: str) -> Path | None:
    """The saved file for a name this module created; None for anything else (no path tricks)."""
    if not NAME.fullmatch(name or ""):
        return None
    target = DIR / name
    return target if target.is_file() else None


def download_name(name: str) -> str:
    """doc_0123456789ab-sprint-notes.pdf → sprint-notes.pdf (or donna-document.pdf without a title)."""
    stem, ext = name.rsplit(".", 1)
    title = stem.partition("-")[2]
    return f"{title or 'donna-document'}.{ext}"


def clear() -> int:
    count = 0
    for f in DIR.glob("doc_*") if DIR.is_dir() else ():
        if NAME.fullmatch(f.name):
            f.unlink(missing_ok=True)
            count += 1
    return count


def human_size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


# ------------------------------------------------------------------ PDF
def _html(markdown: str) -> str:
    from markdown_it import MarkdownIt
    md = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])
    html = md.render(markdown)
    # fpdf2's HTML renderer has no <del> and wants tables with a visible border
    html = html.replace("<del>", "").replace("</del>", "")
    return html.replace("<table>", '<br><table border="1" width="100%" cellpadding="3">').replace("</table>", "</table><br>")


def render_pdf(title: str, markdown: str) -> tuple[bytes, int]:
    """(PDF bytes, page count) for a title and Markdown body."""
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    pdf = FPDF(format="A4")
    pdf.set_title(title)
    pdf.set_author("Donna")
    pdf.set_creator("Donna")
    pdf.add_font("Noto", "", str(FONTS / "NotoSans-Regular.ttf"))
    pdf.add_font("Noto", "B", str(FONTS / "NotoSans-Bold.ttf"))
    pdf.add_font("Noto", "I", str(FONTS / "NotoSans-Italic.ttf"))
    pdf.add_font("Noto", "BI", str(FONTS / "NotoSans-BoldItalic.ttf"))
    pdf.add_font("NotoDeva", "", str(FONTS / "NotoSansDevanagari-Regular.ttf"))
    pdf.add_font("NotoDeva", "B", str(FONTS / "NotoSansDevanagari-Bold.ttf"))
    pdf.add_font("NotoMono", "", str(FONTS / "NotoSansMono-Regular.ttf"))
    pdf.set_fallback_fonts(["NotoDeva"], exact_match=False)
    try:
        pdf.set_text_shaping(True)  # joins Devanagari letters correctly (needs uharfbuzz)
    except Exception:  # noqa: BLE001 - without it Latin text still renders fine
        pass
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, 18)
    pdf.alias_nb_pages()

    def footer():
        pdf.set_y(-12)
        pdf.set_font("Noto", "", 8)
        pdf.set_text_color(140, 140, 150)
        pdf.cell(0, 6, f"{pdf.page_no()} / {{nb}}", align="C")
    pdf.footer = footer
    pdf.add_page()

    pdf.set_font("Noto", "B", 19)
    pdf.set_text_color(30, 20, 60)
    pdf.multi_cell(0, 9, title)
    pdf.ln(4)
    pdf.set_font("Noto", "", 11)
    pdf.set_text_color(25, 25, 30)
    styles = {
        "h1": FontFace(family="Noto", emphasis="BOLD", size_pt=16, color=(30, 20, 60)),
        "h2": FontFace(family="Noto", emphasis="BOLD", size_pt=14, color=(30, 20, 60)),
        "h3": FontFace(family="Noto", emphasis="BOLD", size_pt=12, color=(50, 40, 80)),
        "code": FontFace(family="NotoMono", size_pt=9.5, color=(60, 30, 110)),
        "pre": FontFace(family="NotoMono", size_pt=9, color=(40, 40, 50)),
        "a": FontFace(color=(84, 36, 168), emphasis="UNDERLINE"),
    }
    try:
        pdf.write_html(_html(markdown), font_family="Noto", tag_styles=styles, ul_bullet_char="•",
                       li_prefix_color=(84, 36, 168))
    except Exception:  # noqa: BLE001 - an HTML construct fpdf2 can't lay out: fall back to plain paragraphs
        pdf.set_font("Noto", "", 11)
        for para in re.split(r"\n{2,}", markdown):
            pdf.multi_cell(0, 6, re.sub(r"[*_`#>|]", "", para).strip())
            pdf.ln(2)
    return bytes(pdf.output()), pdf.page_no()


# ------------------------------------------------------------------ the tool's work
def create(title: str, content: str, fmt: str = "pdf") -> tuple[str, int, int]:
    """Save a document. Returns (file name, size in bytes, pages; 0 pages for Markdown)."""
    title = " ".join((title or "").split())[:120] or "Document"
    fmt = (fmt or "pdf").lower().lstrip(".").strip()
    fmt = {"markdown": "md"}.get(fmt, fmt)
    if fmt not in FORMATS:
        raise FileError(f"Unknown format '{fmt}'. Use pdf or md.")
    content = (content or "").strip()
    if not content:
        raise FileError("The document is empty. Write its content in Markdown and call the tool again.")
    if len(content) > MAX_CHARS:
        raise FileError(f"The document is too long ({len(content):,} characters; the limit is {MAX_CHARS:,}).")
    if fmt == "pdf":
        data, pages = render_pdf(title, content)
    else:
        body = content if content.lstrip().startswith("#") else f"# {title}\n\n{content}"
        data, pages = (body + "\n").encode("utf-8"), 0
    DIR.mkdir(parents=True, exist_ok=True)
    name = f"doc_{uuid.uuid4().hex[:12]}" + (f"-{slug(title)}" if slug(title) else "") + f".{fmt}"
    (DIR / name).write_bytes(data)
    return name, len(data), pages
