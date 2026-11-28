# tools/drive_tools.py — Google Drive, limited to files Donna creates (scope drive.file)
#
# Uploads go into a folder ("Donna" by default) that Donna creates on first use. With drive.file,
# Donna can only see that folder and the files she uploaded, never the rest of the user's Drive.

import asyncio

from donna import files, images
from mcp_instance import mcp
from tools.google_auth import DRIVE, NO_DRIVE, GoogleAuthError, has_scope, service

FOLDER = "application/vnd.google-apps.folder"


def _drive():
    if not has_scope(DRIVE):
        raise GoogleAuthError(NO_DRIVE)
    return service("drive", "v3")


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _folder(svc, name: str) -> str:
    """The ID of Donna's folder `name`, created when it doesn't exist yet."""
    found = svc.files().list(q=f"mimeType='{FOLDER}' and name='{_q(name)}' and trashed=false",
                             fields="files(id)", pageSize=1, spaces="drive").execute().get("files", [])
    if found:
        return found[0]["id"]
    return svc.files().create(body={"name": name, "mimeType": FOLDER}, fields="id").execute()["id"]


def _source(file_name: str):
    """(path, mimetype, name on Drive) for a document or picture Donna made."""
    if p := files.path(file_name):
        ext = p.suffix.lstrip(".")
        return p, files.MIMETYPES[ext], files.download_name(file_name)
    if p := images.path(file_name):
        return p, images.MIMETYPES[p.suffix.lstrip(".")], f"donna-{file_name}"
    return None


async def _run(fn, what: str) -> str:
    try:
        return await asyncio.to_thread(fn)
    except GoogleAuthError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        text = str(e)
        if "accessNotConfigured" in text or "has not been used" in text or "disabled" in text.lower():
            return f"Error: {NO_DRIVE}"
        return f"Error {what}: {text[:300]}"


@mcp.tool()
async def upload_to_drive(file_name: str, folder: str = "Donna", title: str = "") -> str:
    """
    Upload a file Donna made (a document from create_document, or a picture from generate_image) to Google Drive.
    - file_name: the file name from that tool's result, e.g. doc_0123456789ab-sprint-notes.pdf.
    - folder: Drive folder name (created if needed). Default "Donna".
    - title: optional name on Drive; default is the document's own name.
    Returns the Drive link, which can be shared with the user.
    """
    from googleapiclient.http import MediaFileUpload

    src = _source(file_name.strip())
    if not src:
        return "Error: no such file. Use the exact file name from create_document or generate_image."
    path, mimetype, name = src

    def go():
        svc = _drive()
        parent = _folder(svc, (folder or "Donna").strip()[:100])
        body = {"name": (title.strip() or name)[:200], "parents": [parent]}
        made = svc.files().create(body=body, media_body=MediaFileUpload(str(path), mimetype=mimetype),
                                  fields="id, name, webViewLink").execute()
        return f"Uploaded to Drive folder '{folder or 'Donna'}': {made['name']}\nLink: {made['webViewLink']}"
    return await _run(go, "uploading to Drive")


@mcp.tool()
async def list_drive_files(folder: str = "Donna", max_results: int = 10) -> str:
    """List the files Donna uploaded to a Drive folder (newest first), with their links."""
    def go():
        svc = _drive()
        found = svc.files().list(q=f"mimeType='{FOLDER}' and name='{_q(folder or 'Donna')}' and trashed=false",
                                 fields="files(id)", pageSize=1).execute().get("files", [])
        if not found:
            return f"No Drive folder '{folder}' made by Donna yet."
        items = svc.files().list(q=f"'{found[0]['id']}' in parents and trashed=false", orderBy="createdTime desc",
                                 pageSize=max(1, min(50, max_results)),
                                 fields="files(name, webViewLink, createdTime)").execute().get("files", [])
        if not items:
            return f"The folder '{folder}' is empty."
        return "\n".join(f"- {f['name']} ({f['createdTime'][:10]}) {f['webViewLink']}" for f in items)
    return await _run(go, "listing Drive files")
