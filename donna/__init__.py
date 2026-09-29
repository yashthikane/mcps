"""Donna web app: FastAPI backend that serves the React UI and runs the Groq + MCP agent."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# DONNA_DATA_DIR lets tests run against a throwaway folder.
DATA_DIR = Path(os.getenv("DONNA_DATA_DIR") or ROOT / "data")

# The tool modules import `mcp_instance` from the repo root.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
