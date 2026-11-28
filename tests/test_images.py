"""Image generation: the model registry, the Cloudflare requests, the tool and the HTTP routes."""
import base64

import httpx
import pytest

from donna import agent, images, llm, permissions, vault
from donna.store import DEFAULT_SETTINGS

ACCOUNT, TOKEN = "0123456789abcdef0123456789abcdef", "cf_token_" + "x" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(llm, "FAKE", True)


@pytest.fixture
def connected():
    vault.set(vault.CLOUDFLARE_ACCOUNT, ACCOUNT)
    vault.set(vault.CLOUDFLARE_TOKEN, TOKEN)


@pytest.fixture
async def hub(store):
    import server  # noqa: F401 - registers tools
    from donna.hub import MCPHub
    from mcp_instance import mcp
    h = MCPHub(store, mcp)
    await h.start()
    yield h
    await h.stop()


def cloudflare(monkeypatch, handler):
    """Route images' HTTP calls to `handler(request) -> httpx.Response`."""
    monkeypatch.setattr(images.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


def test_registry_is_consistent():
    assert DEFAULT_SETTINGS["image_model"] == images.DEFAULT_MODEL
    assert {images.DEFAULT_MODEL, images.VERIFY_MODEL} <= set(images.MODELS)
    assert all(m.provider in images.PROVIDERS and m.request in ("json", "multipart") for m in images.MODELS.values())
    assert len(images.tool_description()) <= 600  # hub.Tool.spec cuts the description there
    assert all(m.id in images.tool_description() for m in images.MODELS.values())
    assert set(images.catalog()[0]) == {"id", "label", "note", "daily", "seconds"}
    assert all(m.seconds > 0 for m in images.MODELS.values())


def test_save_and_path_only_serve_our_own_files():
    name = images.save(JPEG)
    assert name.endswith(".jpg") and images.path(name).read_bytes() == JPEG
    assert images.save(images.FAKE_IMAGE).endswith(".png")
    with pytest.raises(images.ImageError):
        images.save(b"<html>not an image</html>")
    for bad in ("", "../donna.db", "img_zz.jpg", "img_0123456789ab.exe", "img_0123456789ab.jpg/..", "img_000000000000.jpg"):
        assert images.path(bad) is None
    assert images.clear() == 2 and images.path(name) is None


async def test_cloudflare_request_shapes(monkeypatch, connected):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"image": base64.b64encode(JPEG).decode()}, "success": True})
    cloudflare(monkeypatch, handler)

    name, model, _ = await images.generate("a red  scooter\nin the rain", "flux-1-schnell")
    assert model.id == "flux-1-schnell" and images.path(name)
    assert str(seen[0].url) == f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert seen[0].headers["content-type"] == "application/json" and b'"a red scooter in the rain"' in seen[0].content

    _, model, _ = await images.generate("a cat")  # no store: the default model
    assert model.id == images.DEFAULT_MODEL and seen[1].url.path.endswith("flux-2-klein-4b")
    assert seen[1].headers["content-type"].startswith("multipart/form-data")
    assert b'name="prompt"' in seen[1].content and b"a cat" in seen[1].content and b'name="width"' in seen[1].content


@pytest.mark.parametrize("status, body, expected", [
    (401, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}, "rejected the API token"),
    (429, {"success": False, "errors": [{"message": "daily free allocation used"}]}, "daily free allocation used"),
    (200, {"success": True, "result": {}}, "couldn't make the image"),
    (502, None, "HTTP 502"),
])
async def test_cloudflare_errors_are_explained(monkeypatch, connected, status, body, expected):
    cloudflare(monkeypatch, lambda request: httpx.Response(status, json=body) if body else httpx.Response(status, text="bad gateway"))
    with pytest.raises(images.ImageError, match=expected):
        await images.generate("a cat")


async def test_generate_checks_its_input(connected, fake_llm):
    with pytest.raises(images.ImageError, match="empty"):
        await images.generate("   ")
    with pytest.raises(images.ImageError, match="Unknown image model"):
        await images.generate("a cat", "dall-e")
    images.disconnect()
    with pytest.raises(images.ImageError, match="isn't connected"):
        await images.generate("a cat")


async def test_connect_keeps_credentials_only_when_the_test_picture_works(monkeypatch):
    for account, token in (("not-an-id", TOKEN), (ACCOUNT, "short"), (ACCOUNT, "has a space " + "x" * 30)):
        with pytest.raises(images.ImageError):
            await images.connect(account, token)
    cloudflare(monkeypatch, lambda request: httpx.Response(403, json={"success": False, "errors": []}))
    with pytest.raises(images.ImageError, match="rejected"):
        await images.connect(ACCOUNT, TOKEN)
    assert not images.connected()

    def ok(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}" and ACCOUNT in str(request.url)
        return httpx.Response(200, json={"result": {"image": base64.b64encode(JPEG).decode()}, "success": True})
    cloudflare(monkeypatch, ok)
    name, model, _ = await images.connect(f"  {ACCOUNT.upper()} ", f" {TOKEN}\n")
    assert model.id == images.VERIFY_MODEL and images.path(name) and images.connected()
    assert vault.get(vault.CLOUDFLARE_ACCOUNT) == ACCOUNT and vault.get(vault.CLOUDFLARE_TOKEN) == TOKEN


async def test_tool_follows_the_chosen_model(store, hub, fake_llm, connected, monkeypatch):
    tool = hub.find("generate_image", store.settings())
    assert tool.group == "images" and tool.writes and not tool.confirm
    assert set(tool.schema["properties"]) == {"prompt", "model"} and tool.schema["required"] == ["prompt"]

    monkeypatch.setattr(images, "store", store)
    ok, text = await hub.call(tool, {"prompt": "a lighthouse at dusk"})
    assert ok and "FLUX.2 klein 4B" in text and "Prompt used: a lighthouse at dusk" in text
    assert images.path(images.NAME.search(text).group(0))

    store.set_settings(image_model="flux-1-schnell")
    assert "FLUX.1 schnell" in (await hub.call(tool, {"prompt": "a lighthouse"}))[1]
    assert "FLUX.2 klein 9B" in (await hub.call(tool, {"prompt": "a lighthouse", "model": "flux-2-klein-9b"}))[1]

    ok, text = await hub.call(tool, {"prompt": "a lighthouse", "model": "nope"})
    assert not ok and text.startswith("Error: Unknown image model")
    images.disconnect()
    ok, text = await hub.call(tool, {"prompt": "a lighthouse"})
    assert not ok and "Connections" in text


async def test_chat_makes_a_picture_but_not_in_plan_mode_or_on_channels(store, hub, fake_llm, connected):
    async def run(mode="manual", ctx=None):
        conv = store.create_conversation()
        return [e async for e in agent.run_chat(store, hub, agent.Run(conv["id"], mode=mode, ctx=ctx), "make a picture of a red kite", set())]

    end = next(e for e in await run() if e["type"] == "tool.end")
    assert end["name"] == "generate_image" and end["ok"] and end["args"] == {"prompt": "a red kite"}
    assert images.path(images.NAME.search(end["preview"]).group(0))  # the chat shows the picture from this name

    # plan mode, and the owner's own phone chat (contacts with "Make pictures" on: tests/test_channels.py)
    for events in (await run(mode="plan"), await run(ctx=permissions.TurnContext(channel_id="whatsapp"))):
        assert not any(e["type"].startswith("tool.") for e in events)


def test_keywords_pick_the_images_group():
    from donna.hub import Tool
    mk = lambda n, g: Tool(n, n, "donna", g, "", {}, False)  # noqa: E731
    tools = [mk("generate_image", "images"), mk("get_weather", "weather")]
    for text in ("draw a cat", "make me a logo for my cafe", "generate an image of a boat", "a poster for Diwali"):
        assert [t.name for t in agent.select_tools(tools, text, set(), set())] == ["generate_image"], text
    assert [t.name for t in agent.select_tools(tools, "weather in Pune?", set(), set())] == ["get_weather"]


def test_http_routes(tmp_path, fake_llm):
    from fastapi.testclient import TestClient
    from donna.app import app
    app.state.db_path = tmp_path / "api.db"
    with TestClient(app) as c:
        conn = lambda: {x["id"]: x for x in c.get("/api/v1/connections").json()}["images"]  # noqa: E731
        assert conn()["state"] == "not_set_up" and conn()["setup"] == "images" and [t["name"] for t in conn()["tools"]] == ["generate_image"]

        assert c.post("/api/v1/connections/images", json={"account_id": "nope", "token": TOKEN}).status_code == 400
        done = c.post("/api/v1/connections/images", json={"account_id": ACCOUNT, "token": TOKEN}).json()
        assert done["ok"] and done["model"] == "FLUX.1 schnell"
        picture = c.get(f"/api/v1/images/{done['image']}")
        assert picture.status_code == 200 and picture.headers["content-type"] == "image/png" and picture.content == images.FAKE_IMAGE
        assert c.get("/api/v1/images/img_000000000000.png").status_code == 404
        assert c.get("/api/v1/images/api.db").status_code == 404
        assert conn()["state"] == "connected" and "FLUX.2 klein 4B" in conn()["detail"]

        listed = c.get("/api/v1/images/models").json()
        assert listed["current"] == "flux-2-klein-4b" and [m["id"] for m in listed["models"]] == list(images.MODELS)
        assert c.put("/api/v1/settings", json={"image_model": "dall-e"}).status_code == 400
        assert c.put("/api/v1/settings", json={"image_model": "flux-1-schnell"}).json()["image_model"] == "flux-1-schnell"
        assert c.get("/api/v1/images/models").json()["current"] == "flux-1-schnell" and "FLUX.1 schnell" in conn()["detail"]

        c.post("/api/v1/wipe")
        assert c.get(f"/api/v1/images/{done['image']}").status_code == 404
        assert c.delete("/api/v1/connections/images").json()["ok"] and conn()["state"] == "not_set_up"
