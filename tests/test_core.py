"""Unit tests: secrets vault, SQLite store, tool helpers."""
import base64
import json
from unittest.mock import MagicMock

from donna import vault


def test_vault_roundtrip_short_and_chunked(memory_keyring):
    vault.set("k", "short")
    assert vault.get("k") == "short"
    long = "x" * 1234 + "END"
    vault.set("k", long)
    assert vault.get("k") == long
    assert ("donna", "k#count") in memory_keyring.data
    assert ("donna", "k") not in memory_keyring.data  # the short value was replaced
    vault.delete("k")
    assert vault.get("k") is None
    assert not memory_keyring.data


def test_vault_imports_env_once(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_env")
    monkeypatch.setenv("INTERNAL_INTERGRATION_TOKEN", "ntn_env")
    assert sorted(vault.import_from_env()) == [vault.GROQ_KEY, vault.NOTION_TOKEN]
    assert vault.get(vault.GROQ_KEY) == "gsk_env"
    assert vault.import_from_env() == []


def test_store_conversations_search_pin_export_wipe(store):
    a = store.create_conversation()
    b = store.create_conversation("Trip planning")
    store.add_message(a["id"], "user", "Summarize the Acme invoice email")
    store.add_message(a["id"], "assistant", "The Acme invoice is due Oct 5", [{"name": "search_emails", "preview": "3"}])
    store.update_conversation(a["id"], title="Invoices")
    store.update_conversation(b["id"], pinned=True)

    convs = store.list_conversations()
    assert convs[0]["id"] == b["id"] and convs[0]["pinned"] is True  # pinned first
    assert {c["id"] for c in store.search("acme")} == {a["id"]}
    assert {c["id"] for c in store.search("trip")} == {b["id"]}  # title match
    assert store.search('bad "quote') == [] or isinstance(store.search('bad "quote'), list)
    msgs = store.messages(a["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["tool_events"][0]["name"] == "search_emails"

    exported = store.export_all()
    assert len(exported["conversations"]) == 2
    store.delete_conversation(b["id"])
    assert store.get_conversation(b["id"]) is None
    assert store.wipe() == 1
    assert store.list_conversations() == [] and store.search("acme") == []


def test_store_settings_and_usage(store):
    assert store.settings()["model"] == "openai/gpt-oss-120b"
    store.set_settings(timezone="Asia/Kolkata", unknown="ignored")
    assert store.settings()["timezone"] == "Asia/Kolkata" and "unknown" not in store.settings()
    store.bump_usage(500)
    store.bump_usage(250)
    u = store.usage()
    assert u["requests"] == 2 and u["tokens"] == 750


def test_store_mcp_servers(store):
    s = store.add_mcp_server("Files", "stdio", "npx", ["-y", "pkg"], env_keys=["TOKEN"])
    assert store.list_mcp_servers()[0]["args"] == ["-y", "pkg"]
    store.update_mcp_server(s["id"], enabled=False, disabled_tools=["write_file"])
    got = store.get_mcp_server(s["id"])
    assert got["enabled"] is False and got["disabled_tools"] == ["write_file"]
    store.delete_mcp_server(s["id"])
    assert store.list_mcp_servers() == []


def test_notion_helpers():
    from tools.notion_tools import _id, blocks_from_text
    assert _id("https://www.notion.so/ws/Cafe-Plans-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d?v=9") == "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    assert _id("1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d") == "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    blocks = blocks_from_text("# Title\n\n## Sub\n- a\n* b\n1. one\n[ ] todo\n- [x] done\n> quote\nplain")
    assert [b["type"] for b in blocks] == ["heading_1", "heading_2", "bulleted_list_item", "bulleted_list_item",
                                          "numbered_list_item", "to_do", "to_do", "quote", "paragraph"]
    assert blocks[6]["to_do"]["checked"] is True and blocks[5]["to_do"]["checked"] is False
    long = blocks_from_text("x" * 4500)[0]["paragraph"]["rich_text"]
    assert [len(t["text"]["content"]) for t in long] == [2000, 2000, 500]


def test_calendar_time_helpers():
    from tools.calendar_tools import _rfc3339, _time_field
    assert _time_field("2026-10-01", "Asia/Kolkata") == {"date": "2026-10-01"}
    assert _time_field("2026-10-01T09:00", "Asia/Kolkata") == {"dateTime": "2026-10-01T09:00:00", "timeZone": "Asia/Kolkata"}
    assert _rfc3339("2026-10-01", "Asia/Kolkata") == "2026-10-01T00:00:00+05:30"
    assert _rfc3339("2026-10-01", "Asia/Kolkata", end_of_day=True) == "2026-10-02T00:00:00+05:30"


def test_gmail_reply_threads_correctly():
    from tools.gmail_tools import _reply_message
    svc = MagicMock()
    svc.users().messages().get().execute.return_value = {
        "threadId": "T1",
        "payload": {"headers": [
            {"name": "Message-ID", "value": "<abc@mail>"}, {"name": "Subject", "value": "Invoice"},
            {"name": "From", "value": "Acme <billing@acme.com>"}, {"name": "References", "value": "<prev@mail>"}]}}
    m, thread = _reply_message(svc, "m1", "Thanks, paid.", reply_all=False)
    assert thread == "T1"
    assert m["Subject"] == "Re: Invoice" and m["To"] == "Acme <billing@acme.com>"
    assert m["In-Reply-To"] == "<abc@mail>" and m["References"] == "<prev@mail> <abc@mail>"
    assert "Thanks, paid." in m.get_content()


def test_google_client_file_validation(memory_keyring):
    import pytest
    from tools import google_auth
    good = json.dumps({"installed": {"client_id": "id.apps.googleusercontent.com", "client_secret": "s", "project_id": "p"}})
    assert google_auth.save_client(good)["project_id"] == "p"
    assert google_auth.status(max_age=0)["state"] == "needs_auth"
    with pytest.raises(ValueError):
        google_auth.parse_client_file(b"not json")
    with pytest.raises(ValueError):
        google_auth.parse_client_file(json.dumps({"type": "service_account"}))
    google_auth.disconnect(forget_client=True)
    assert google_auth.status(max_age=0)["state"] == "not_set_up"
    assert base64  # keep import used
