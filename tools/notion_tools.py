# tools/notion_tools.py — Notion workspace management tools

import os
from dotenv import load_dotenv
from notion_client import AsyncClient
from mcp_instance import mcp

load_dotenv()

# Initialize Notion client
NOTION_API_KEY = os.getenv("NOTION_API_KEY") or os.getenv("INTERNAL_INTERGRATION_TOKEN")
if not NOTION_API_KEY:
    raise ValueError("NOTION_API_KEY or INTERNAL_INTERGRATION_TOKEN environment variable is not set.")

notion = AsyncClient(auth=NOTION_API_KEY)


@mcp.tool()
async def list_pages() -> str:
    """
    List all pages and databases accessible by the Notion integration.
    Returns their IDs, titles, and object type (page or database).
    """
    try:
        results = []
        has_more = True
        next_cursor = None

        while has_more:
            if next_cursor:
                response = await notion.search(start_cursor=next_cursor)
            else:
                response = await notion.search()

            for item in response.get("results", []):
                obj_type = item.get("object")
                item_id = item.get("id")

                # Extract title safely
                title = "Untitled"
                if obj_type == "page":
                    properties = item.get("properties", {})
                    for prop_name, prop_val in properties.items():
                        if prop_val.get("id") == "title":
                            title_arr = prop_val.get("title", [])
                            if title_arr:
                                title = "".join([t.get("plain_text", "") for t in title_arr])
                            break
                elif obj_type == "database":
                    title_arr = item.get("title", [])
                    if title_arr:
                        title = "".join([t.get("plain_text", "") for t in title_arr])

                results.append(f"[{obj_type.upper()}] ID: {item_id} | Title: {title}")

            has_more = response.get("has_more", False)
            next_cursor = response.get("next_cursor")

        if not results:
            return "No pages or databases found. Please ensure they are connected to the Notion Integration."

        return "\n".join(results)
    except Exception as e:
        return f"Error listing pages: {str(e)}"


@mcp.tool()
async def read_page_content(page_id: str) -> str:
    """
    Read the properties and block contents of a Notion page.
    """
    try:
        page = await notion.pages.retrieve(page_id=page_id)

        # Read blocks with pagination
        blocks_data = []
        has_more = True
        next_cursor = None

        while has_more:
            if next_cursor:
                response = await notion.blocks.children.list(block_id=page_id, start_cursor=next_cursor)
            else:
                response = await notion.blocks.children.list(block_id=page_id)

            blocks_data.extend(response.get("results", []))
            has_more = response.get("has_more", False)
            next_cursor = response.get("next_cursor")

        # Format output
        output = ["--- PAGE PROPERTIES ---"]
        for prop_name, prop_data in page.get("properties", {}).items():
            output.append(f"{prop_name} ({prop_data.get('type')}): {str(prop_data)}")

        output.append("\n--- PAGE CONTENT BLOCKS ---")
        if not blocks_data:
            output.append("(No blocks found in this page)")
        else:
            for i, block in enumerate(blocks_data):
                block_type = block.get("type")
                output.append(f"[{i+1}] {block_type}: {str(block.get(block_type))}")

        return "\n".join(output)
    except Exception as e:
        return f"Error reading page content: {str(e)}"


@mcp.tool()
async def create_page(parent_id: str, title: str, parent_type: str = "page_id") -> str:
    """
    Create a new page in Notion.
    parent_type must be either "page_id" or "database_id".
    """
    try:
        parent = {parent_type: parent_id}
        properties = {
            "title": {
                "title": [{"text": {"content": title}}]
            }
        }

        new_page = await notion.pages.create(parent=parent, properties=properties)
        return f"Successfully created page '{title}' with ID: {new_page.get('id')}"
    except Exception as e:
        return f"Error creating page: {str(e)}"


@mcp.tool()
async def update_page_title(page_id: str, new_title: str) -> str:
    """
    Update the title of an existing Notion page.
    """
    try:
        properties = {
            "title": {
                "title": [{"text": {"content": new_title}}]
            }
        }
        await notion.pages.update(page_id=page_id, properties=properties)
        return f"Successfully updated page {page_id} title to '{new_title}'"
    except Exception as e:
        return f"Error updating page: {str(e)}"


@mcp.tool()
async def append_text_to_page(page_id: str, text_content: str) -> str:
    """
    Append a new paragraph text block to an existing Notion page.
    """
    try:
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": text_content}}]
                }
            }
        ]
        await notion.blocks.children.append(block_id=page_id, children=children)
        return f"Successfully appended text to page {page_id}"
    except Exception as e:
        return f"Error appending blocks: {str(e)}"


@mcp.tool()
async def delete_page(page_id: str) -> str:
    """
    Archive/Delete a page in Notion (moves it to trash).
    """
    try:
        await notion.pages.update(page_id=page_id, archived=True)
        return f"Successfully archived/deleted page {page_id}"
    except Exception as e:
        return f"Error deleting page: {str(e)}"
