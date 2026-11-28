# tools/file_tools.py — PDF and Markdown documents (the rendering lives in donna/files.py)

import asyncio

from donna import files
from mcp_instance import mcp


@mcp.tool()
async def create_document(title: str, content: str, format: str = "pdf") -> str:
    """
    Make a downloadable document (PDF or Markdown file) and show it in the chat as a file card.
    Use it whenever the user asks for a PDF, a document, a report or a .md file; don't paste the content instead.
    - title: the document's title, e.g. "EDTR sprint notes" (also its file name).
    - content: the full body in Markdown: ## headings, - lists, 1. lists, **bold**, *italic*, | tables |,
      `code`, > quotes, [links](https://...). Don't repeat the title as a heading.
    - format: "pdf" (default) or "md".
    The result names the file; upload_to_drive can take that name.
    """
    try:
        name, size, pages = await asyncio.to_thread(files.create, title, content, format)
    except files.FileError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return f"Error: the document couldn't be made ({type(e).__name__}: {str(e)[:200]})."
    what = f"{pages} page{'s' if pages != 1 else ''}, " if pages else ""
    return (f"Document created: {name} ({what}{files.human_size(size)}). It is already shown to the user as a file "
            "with a Download button, so don't paste its content or the file name; reply with one short line.")
