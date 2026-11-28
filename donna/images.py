"""Image generation: a registry of models and the providers that run them.

Everything about a model lives in one MODELS line; the tool (tools/image_tools.py), the
Connections card and the model picker all read this registry. To add a model, add a line. To add
another service, write one `async def _name(model, prompt, **creds) -> bytes`, register it in
PROVIDERS and point a model's `provider` at it.

Pictures are saved under data/images and served by GET /api/v1/images/{name}. The model that
writes the prompt only ever sees the file name, never the image bytes.
"""
import asyncio
import base64
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from . import DATA_DIR, llm, vault

DIR = DATA_DIR / "images"
NAME = re.compile(r"img_[0-9a-f]{12}\.(jpg|png|webp)")
MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG", "png"), (b"RIFF", "webp"))
MIMETYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
PROMPT_CHARS = 2000  # Cloudflare caps FLUX.1 schnell prompts at 2048
NOT_CONNECTED = "Image generation isn't connected yet. Open Donna → Connections → Image generation and add your Cloudflare token."

store = None  # set by the app's lifespan; the terminal client runs without it and uses DEFAULT_MODEL


class ImageError(Exception):
    """A failure the user can act on; the message is shown as it is."""


@dataclass(frozen=True)
class Model:
    id: str          # saved in the `image_model` setting
    label: str
    provider: str    # key in PROVIDERS
    remote: str      # the provider's own name for the model
    note: str        # one line for the model picker
    daily: str       # estimated images per day on the free allowance (10,000 Neurons, resets 00:00 UTC)
    seconds: int     # usual time for one picture; the chat's percentage is paced by it until it has timed a real one
    request: str = "json"  # how Cloudflare takes the input: json | multipart


DEFAULT_MODEL = "flux-2-klein-4b"  # also the default of the `image_model` setting (store.DEFAULT_SETTINGS)
VERIFY_MODEL = "flux-1-schnell"    # the quick, cheap model the Connect step tests the token with
MODELS = {m.id: m for m in (
    Model("flux-2-klein-4b", "FLUX.2 klein 4B", "cloudflare", "@cf/black-forest-labs/flux-2-klein-4b",
          "Follows detailed prompts best. About 15 seconds per image.", "about 95", 15, "multipart"),
    Model("flux-1-schnell", "FLUX.1 schnell", "cloudflare", "@cf/black-forest-labs/flux-1-schnell",
          "Fastest, about 3 seconds. Can drop or merge subjects in busy scenes.", "65 to 170", 3),
    Model("flux-2-klein-9b", "FLUX.2 klein 9B", "cloudflare", "@cf/black-forest-labs/flux-2-klein-9b",
          "Highest quality of the three, but uses the free allowance quickly.", "about 7", 20, "multipart"),  # 20 s is a guess: never timed
)}

# A 1×1 PNG for DONNA_FAKE_LLM runs (tests, UI end-to-end), so they never call the network.
FAKE_IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


# ------------------------------------------------------------------ providers
async def _cloudflare(model: Model, prompt: str, account: str | None = None, token: str | None = None) -> bytes:
    """Cloudflare Workers AI. FLUX.1 takes JSON, FLUX.2 takes multipart form fields; both answer
    {"result": {"image": "<base64>"}, "success": true}."""
    account = account or vault.get(vault.CLOUDFLARE_ACCOUNT)
    token = token or vault.get(vault.CLOUDFLARE_TOKEN)
    if not account or not token:
        raise ImageError(NOT_CONNECTED)
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model.remote}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=80) as client:  # under hub.call's 90 s, so our message wins
            if model.request == "multipart":
                fields = {"prompt": prompt, "width": "1024", "height": "1024"}
                r = await client.post(url, headers=headers, files={k: (None, v) for k, v in fields.items()})
            else:
                r = await client.post(url, headers=headers, json={"prompt": prompt, "steps": 4})
    except httpx.TimeoutException as e:
        raise ImageError(f"{model.label} took too long to answer. Try again, or pick a faster model.") from e
    except httpx.HTTPError as e:
        raise ImageError("Can't reach Cloudflare. Check your internet connection.") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code in (401, 403):
        raise ImageError("Cloudflare rejected the API token. Create a new Workers AI token and reconnect.")
    image = (data.get("result") or {}).get("image") if isinstance(data.get("result"), dict) else None
    if r.status_code >= 400 or not image:
        detail = "; ".join(str(e.get("message", "")) for e in data.get("errors") or [] if isinstance(e, dict))
        raise ImageError(f"Cloudflare couldn't make the image ({detail[:300] or f'HTTP {r.status_code}'}).")
    try:
        return base64.b64decode(image)
    except ValueError as e:
        raise ImageError("Cloudflare returned an image that couldn't be read.") from e


PROVIDERS = {"cloudflare": _cloudflare}


# ------------------------------------------------------------------ files
def save(data: bytes) -> str:
    ext = next((e for magic, e in MAGIC if data.startswith(magic)), None)
    if not ext:
        raise ImageError("The image service returned something that isn't an image.")
    DIR.mkdir(parents=True, exist_ok=True)
    name = f"img_{uuid.uuid4().hex[:12]}.{ext}"
    (DIR / name).write_bytes(data)
    return name


def path(name: str) -> Path | None:
    """The saved file for a name this module created; None for anything else (no path tricks)."""
    if not NAME.fullmatch(name or ""):
        return None
    target = DIR / name
    return target if target.is_file() else None


def clear() -> int:
    """Delete every saved picture (the Settings "wipe" does this along with the conversations)."""
    count = 0
    for f in DIR.glob("img_*") if DIR.is_dir() else ():
        if NAME.fullmatch(f.name):
            f.unlink(missing_ok=True)
            count += 1
    return count


# ------------------------------------------------------------------ the connection
def connected() -> bool:
    return bool(vault.get(vault.CLOUDFLARE_ACCOUNT) and vault.get(vault.CLOUDFLARE_TOKEN))


def current() -> Model:
    chosen = store.settings().get("image_model") if store else DEFAULT_MODEL
    return MODELS.get(chosen) or MODELS[DEFAULT_MODEL]


def catalog() -> list[dict]:
    return [{k: v for k, v in asdict(m).items() if k in ("id", "label", "note", "daily", "seconds")} for m in MODELS.values()]


async def generate(prompt: str, model_id: str = "") -> tuple[str, Model, float]:
    """Make one picture and save it. Returns (file name, the model used, seconds taken)."""
    prompt = " ".join((prompt or "").split())[:PROMPT_CHARS]
    if not prompt:
        raise ImageError("The prompt is empty. Describe the picture to make.")
    model = MODELS.get(model_id.strip()) if model_id and model_id.strip() else current()
    if not model:
        raise ImageError(f"Unknown image model '{model_id}'. Use one of: {', '.join(MODELS)}, or leave it empty.")
    if not connected():
        raise ImageError(NOT_CONNECTED)
    started = time.perf_counter()
    if llm.FAKE:  # UI tests set DONNA_FAKE_IMAGE_SECONDS to watch the chat's loading state
        await asyncio.sleep(float(os.getenv("DONNA_FAKE_IMAGE_SECONDS", "0")))
        data = FAKE_IMAGE
    else:
        data = await PROVIDERS[model.provider](model, prompt)
    return save(data), model, time.perf_counter() - started


async def connect(account: str, token: str) -> tuple[str, Model, float]:
    """Check the credentials by making one small picture, and keep them only if that worked."""
    account, token = account.strip().lower(), token.strip()
    if not re.fullmatch(r"[0-9a-f]{32}", account):
        raise ImageError("That doesn't look like a Cloudflare Account ID. It is 32 letters and digits, shown on the Workers AI page.")
    if len(token) < 20 or re.search(r"\s", token):
        raise ImageError("That token looks incomplete. Copy the whole Workers AI API token.")
    model = MODELS[VERIFY_MODEL]
    started = time.perf_counter()
    data = FAKE_IMAGE if llm.FAKE else await PROVIDERS[model.provider](
        model, "a small paper boat on a calm lake at sunrise, watercolor painting", account=account, token=token)
    name = save(data)
    vault.set(vault.CLOUDFLARE_ACCOUNT, account)
    vault.set(vault.CLOUDFLARE_TOKEN, token)
    return name, model, time.perf_counter() - started


def disconnect() -> None:
    vault.delete(vault.CLOUDFLARE_ACCOUNT)
    vault.delete(vault.CLOUDFLARE_TOKEN)


def tool_description() -> str:
    """What the chat model reads (hub.Tool.spec keeps the first 600 characters)."""
    models = "; ".join(f"{m.id} ({m.note.split('.')[0].lower()})" for m in MODELS.values())
    return ("Create one picture and show it in the chat. Write `prompt` yourself, in English, from what the user asked: "
            "start with the style (photo, cartoon, watercolor), give exact counts, say where each subject is (left, centre, "
            "right, background) and what it wears or holds, then the setting and lighting. "
            f"Leave `model` empty unless the user names one: {models}.")
