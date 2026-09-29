"""Browser end-to-end test of the real Donna app (FastAPI + React) with the scripted fake model.

Start the server first (see README "Testing"), then:
    python tests/e2e_ui.py [base_url]
Uses the installed Microsoft Edge through Playwright (channel="msedge").
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8799"
SHOTS = Path(os.getenv("E2E_SHOTS", tempfile.gettempdir()))
results, errors = [], []


def check(name, fn, page=None, reset=True):
    try:
        if page is not None and reset:  # start each scenario with no dialog left open by a failed one
            page.evaluate("document.querySelectorAll('dialog[open]').forEach(d => d.close())")
        fn()
        results.append((name, "PASS", ""))
    except Exception as e:  # noqa: BLE001 - report every failure at the end
        line = next((f.lineno for f in reversed(traceback.extract_tb(e.__traceback__)) if f.filename.endswith("e2e_ui.py")), 0)
        if page is not None:
            try:
                page.screenshot(path=str(SHOTS / f"fail_{name[:2].strip()}.png"))
            except Exception:  # noqa: BLE001
                pass
        results.append((name, "FAIL", f"{type(e).__name__} (line {line}): {(str(e).splitlines() or [''])[0][:180]}"))


def has_text(page, selector, needle, timeout=15000):
    page.wait_for_function(
        "([s, n]) => [...document.querySelectorAll(s)].some(e => e.innerText.toLowerCase().includes(n.toLowerCase()))",
        arg=[selector, needle], timeout=timeout)


def send(page, text):
    page.fill("#prompt", text)
    page.press("#prompt", "Enter")


def idle(page, timeout=30000):
    page.wait_for_selector('button.send[aria-label="Send"]', timeout=timeout)


def card(page, name):
    return page.locator(".ccard", has=page.locator(f"h3:text-is('{name}')"))


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE)

    def onboarding():
        page.wait_for_selector("dialog[open] #ob-title")
        page.fill("#ob-key", "bad-key")
        page.click("text=Test")
        has_text(page, "dialog[open] .result", "rejected")
        page.fill("#ob-key", "gsk_test_key_1234")
        page.click("text=Test")
        has_text(page, "dialog[open] .result", "Key works")
        for _ in range(3):
            page.click("dialog[open] .dlg-foot .btn-primary")
        page.click("dialog[open] >> text=What's the weather in Pune right now?")
        page.wait_for_selector("dialog[open]", state="detached", timeout=5000)
        page.wait_for_selector(".toolcall .tc-name:text('get_weather')", timeout=15000)
        has_text(page, ".toolcall.s-done", "get_weather", 20000)
        idle(page)
        has_text(page, ".msg-bot .bot-text", "Pune")
        has_text(page, ".conv-title", "Pune")
    check("01 onboarding: key validation, steps, first prompt runs a real tool", onboarding, page, reset=False)

    def streaming():
        send(page, "what is the square of 9")
        idle(page)
        has_text(page, ".msg-bot .bot-text", "81")
        assert page.locator(".toolcall.s-done .tc-name:text('square')").count() == 1
        page.click('[role="tab"]:text("Tool calls")')
        has_text(page, "#runpanel .tree", "square")
        page.click('[role="tab"]:text("Usage")')
        page.wait_for_selector("#runpanel .stat .num")
        page.click('[role="tab"]:text("Tool calls")')
    check("02 chat: streamed reply, tool card, activity panel", streaming, page)

    def approval():
        send(page, "create event for prep")
        page.wait_for_selector(".approval h3:text('Create calendar event')")
        page.click(".approval button:text('Reject')")
        idle(page)
        page.wait_for_selector(".toolcall.s-rejected")
        send(page, "create event again")
        page.wait_for_selector(".approval button:text('Approve')")
        page.click(".approval button:text('Approve')")
        idle(page)
        has_text(page, ".toolcall.s-error", "create_event")
        has_text(page, ".tc-body pre", "Google isn't connected")  # failed calls open expanded
    check("03 approvals: reject skips the tool; approve runs it (Google not connected → clear error)", approval, page)

    def stop():
        send(page, "tell me something slow")
        page.wait_for_selector('button.send[aria-label="Stop"]')
        page.wait_for_timeout(600)
        page.click('button.send[aria-label="Stop"]')
        idle(page, 10000)
        has_text(page, ".msg-bot .bot-text", "[stopped]")
    check("04 stop: cancels a streaming reply", stop, page)

    def sidebar():
        page.fill("#conv-search", "square")
        page.wait_for_timeout(500)
        titles = page.locator(".conv-title").all_inner_texts()
        assert len(titles) == 1, titles  # full-text search matched the message, not just the title
        page.fill("#conv-search", "")
        page.wait_for_timeout(400)
        conv = page.locator(".conv").first
        conv.hover()
        conv.locator('button[aria-label^="Pin"]').click()
        has_text(page, ".cgroup", "Pinned")
        page.locator(".conv").first.hover()
        page.locator(".conv").first.locator('button[aria-label^="Rename"]').click()
        page.fill('[id^="ren-"]', "Weather and maths")
        page.keyboard.press("Enter")
        has_text(page, ".conv-title", "Weather and maths")
        page.reload()
        has_text(page, ".conv-title", "Weather and maths")  # persisted
        page.locator(".conv").first.click()
        page.wait_for_selector(".msg-user")
        page.locator(".conv").first.hover()
        page.locator(".conv").first.locator('button[aria-label^="Delete"]').click()
        page.click(".conv-del button:text('Delete')")
        page.wait_for_function("() => !document.querySelector('.conv')")
        page.wait_for_selector(".empty h2")
    check("05 conversations: search, pin, rename (persisted), delete", sidebar, page)

    def palette():
        page.keyboard.press("Control+K")
        page.wait_for_selector("dialog[open] #pal-q")
        page.fill("#pal-q", "connections")
        page.keyboard.press("Enter")
        page.wait_for_selector("#view-connections h1:text('Connections')")
    check("06 command palette: Ctrl+K → Open Connections", palette, page)

    def notion():
        card(page, "Notion").locator("button:text('Connect')").click()
        page.check("dialog[open] input[type=checkbox]")
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.check("dialog[open] input[type=checkbox]")
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.fill("#n-secret", "short-secret")
        page.click("dialog[open] button:text('Connect')")
        has_text(page, "dialog[open] .result.err", "too short")
        page.fill("#n-secret", "ntn_" + "x" * 40)
        page.click("dialog[open] button:text('Connect')")
        has_text(page, "dialog[open] .result", "Connected")
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.wait_for_selector("dialog[open]", state="detached")
        card(page, "Notion").locator(".pill:text('Connected')").wait_for()
    check("07 Notion wizard: bad secret rejected, good secret connects", notion, page)

    def tools_and_toggles():
        card(page, "Notion").locator("button:text('Tools')").click()
        page.wait_for_selector("dialog[open] .toolrow")
        page.locator("dialog[open] .toolrow", has=page.locator(".mono:text-is('delete_page')")).locator(".switch").click()
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        has_text(page, ".ccard", "7 of 8 tools on")
        card(page, "Weather").locator(".switch").click()
        card(page, "Weather").locator(".pill:text('Off')").wait_for()
        card(page, "Weather").locator(".switch").click()
        card(page, "Weather").locator(".pill:text('Connected')").wait_for()
    check("08 tools dialog: per-tool switch; connection on/off", tools_and_toggles, page)

    def google():
        card(page, "Gmail").locator("button:text('Connect')").click()
        for _ in range(3):
            page.check("dialog[open] ul.checks input[type=checkbox] >> nth=%d" % _) if _ < 3 else None
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.check("dialog[open] ul.checks input[type=checkbox]")
        page.click("dialog[open] .dlg-foot .btn-primary")
        bad = Path(tempfile.gettempdir()) / "not-oauth.json"
        bad.write_text(json.dumps({"type": "service_account"}))
        page.set_input_files("dialog[open] input[type=file]", str(bad))
        has_text(page, "dialog[open] .result.err", "Desktop app")
        good = Path(tempfile.gettempdir()) / "client_secret_test.json"
        good.write_text(json.dumps({"installed": {"client_id": "123.apps.googleusercontent.com", "client_secret": "s", "project_id": "donna-e2e"}}))
        page.set_input_files("dialog[open] input[type=file]", str(good))
        has_text(page, "dialog[open] .result", "donna-e2e")
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.wait_for_selector("dialog[open] button:text('Authorize with Google')")
        page.keyboard.press("Escape")
        card(page, "Gmail").locator(".pill:text('Sign in')").wait_for()
        card(page, "Gmail").locator("button:text('Sign in')").wait_for()
    check("09 Google wizard: steps, credentials validation, upload → ready to authorize", google, page)

    def mcp():
        folder = Path(tempfile.mkdtemp(prefix="donna-fs-"))
        (folder / "hello.txt").write_text("hi from the e2e test")
        page.click("button:text('Add MCP server') >> nth=0")
        page.click("dialog[open] .preset:has-text('Filesystem')")
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.fill("#m-args", f'-y @modelcontextprotocol/server-filesystem "{folder}"')
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.click("dialog[open] button:has-text('Run test')")
        has_text(page, "dialog[open] .result", "tools found", 180000)
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.click("dialog[open] .dlg-foot .btn-primary")
        page.wait_for_selector("dialog[open]", state="detached", timeout=90000)
        card(page, "Filesystem").locator(".pill:text('Connected')").wait_for(timeout=90000)
        page.click('.tab:has-text("Chat")')
        send(page, "list folders you can access")
        idle(page, 60000)
        has_text(page, ".toolcall.s-done", "filesystem__list_allowed_directories", 5000)
        has_text(page, ".msg-bot .bot-text", folder.name)
    check("10 MCP wizard: real Filesystem server added and used from chat", mcp, page)

    def settings():
        page.click('.tab:has-text("Settings")')
        page.wait_for_selector("#view-settings h1")
        page.select_option("#m-effort", "low")
        page.locator(".scard", has=page.locator("b:text('Pixel grid background')")).locator(".switch").first.click()
        page.wait_for_function("() => !document.body.classList.contains('grid-on')")
        page.locator(".scard", has=page.locator("b:text('Pixel grid background')")).locator(".switch").first.click()
        page.wait_for_function("() => document.body.classList.contains('grid-on')")
        page.click("text=Test key")
        has_text(page, ".scard", "Key works")
        with page.expect_download() as dl:
            page.click("text=Export conversations")
        data = json.loads(Path(dl.value.path()).read_text())
        assert len(data["conversations"]) >= 1
        page.click("text=Wipe all conversations")
        page.click("text=Wipe everything")
        has_text(page, ".toast", "Deleted")
        page.click('.tab:has-text("Chat")')
        page.wait_for_function("() => !document.querySelector('.conv')")
    check("11 settings: effort, switches, key test, export download, wipe", settings, page)

    def offline():
        ctx.set_offline(True)
        page.evaluate("window.dispatchEvent(new Event('offline'))")
        has_text(page, ".banner", "OFFLINE", 5000)
        assert page.is_disabled("#prompt")
        ctx.set_offline(False)
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.wait_for_selector(".banner", state="detached", timeout=10000)
    check("12 offline banner: shows and blocks sending; clears when back online", offline, page)

    def shots():
        page.click('.tab:has-text("Chat")')
        send(page, "what is the square of 12")
        idle(page)
        page.screenshot(path=str(SHOTS / "app_desktop_chat.png"))
        page.click('.tab:has-text("Connections")')
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "app_desktop_connections.png"))
    check("-- screenshots", shots, page)

    m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2).new_page()
    m.on("pageerror", lambda e: errors.append(str(e)))
    m.goto(BASE)

    def mobile():
        m.wait_for_selector(".composer")
        m.wait_for_timeout(800)
        assert m.evaluate("document.documentElement.scrollWidth") <= 390
        m.click('button[aria-label="Show conversations"]')
        m.wait_for_timeout(350)
        assert m.evaluate("document.querySelector('.side').getBoundingClientRect().left") >= -1
        m.keyboard.press("Escape")
        m.wait_for_timeout(350)
        m.click('button[aria-label="Toggle activity panel"]')
        m.wait_for_timeout(350)
        r = m.evaluate("(() => { const b = document.querySelector('#runpanel').getBoundingClientRect(); return [b.left, b.right] })()")
        assert r[0] >= 0 and r[1] <= 391, r
        m.screenshot(path=str(SHOTS / "app_mobile.png"))
    check("13 mobile 390px: drawers, no horizontal scroll", mobile, m)
    browser.close()

w = max(len(r[0]) for r in results)
for name, status, msg in results:
    print(f"{status}  {name.ljust(w)}  {msg}")
print(f"\n{sum(r[1] == 'PASS' for r in results)}/{len(results)} passed · console errors: {len(set(errors))}")
for e in sorted(set(errors))[:10]:
    print("  -", e[:220])
