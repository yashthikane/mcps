# tools/image_tools.py — Image generation (models and providers live in donna/images.py)

from donna import images
from mcp_instance import mcp


@mcp.tool(description=images.tool_description())
async def generate_image(prompt: str, model: str = "") -> str:
    try:
        name, used, seconds = await images.generate(prompt, model)
    except images.ImageError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001 - tools return errors as text, never raise
        return f"Error: image generation failed ({type(e).__name__}: {str(e)[:200]})."
    # The chat finds the picture by this file name and shows it; the model must not repeat it.
    return (f"Image created: {name} ({used.label}, {seconds:.0f} s). It is already shown to the user in the chat, "
            f"so don't add a link or the file name; reply with one short line. Prompt used: {' '.join(prompt.split())[:300]}")
