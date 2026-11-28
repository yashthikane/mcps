"""Who may use which tool, enforced in code (never left to the model's judgement).

A turn runs for an *audience*:
- owner:   the web chat, or the owner's own WhatsApp/Telegram. Every tool; approval follows the
           permission mode (plan / manual / auto) exactly like the web chat.
- contact: someone else writing on a channel. Only tools whose capability the owner switched on for
           that contact; each capability is off | on | approval. Tools that send, change or delete
           (hub.CONFIRM) are never "on" for a contact: they always need the owner's approval.

`decide` is applied twice: when the tool list is built (denied tools are never shown to the model)
and again when a call arrives (defense in depth against a model that invents a tool name).
"""
from __future__ import annotations

from dataclasses import dataclass, field

LEVELS = ("off", "on", "approval")

# Capability → the tools it unlocks for a contact. Tools not listed here are never available to
# contacts (deleting email, Notion deletes, cancelling tasks, the owner-only runtime tools).
CAPABILITIES: dict[str, dict] = {
    "general": {"label": "General help", "help": "Small talk, weather and simple utilities.",
                "tools": {"get_weather", "square", "get_jokes"}},
    "calendar.availability": {"label": "Check your availability", "help": "Free/busy times only, never event details.",
                              "tools": {"check_availability"}},
    "calendar.events": {"label": "See your calendar events", "help": "Event titles, times, attendees.",
                        "tools": {"get_events", "list_events"}},
    "calendar.book": {"label": "Book or move meetings", "help": "Creates events, with Google Meet when asked. Always needs your approval.",
                      "tools": {"create_event", "update_event"}},
    "calendar.cancel": {"label": "Cancel meetings", "help": "Always needs your approval.", "tools": {"delete_event"}},
    "email.read": {"label": "Read your email", "help": "Search and read your Gmail.",
                   "tools": {"list_emails", "read_email", "search_emails", "get_unread_emails"}},
    "email.draft": {"label": "Draft emails", "help": "Creates Gmail drafts; doesn't send.", "tools": {"create_draft", "list_drafts"}},
    "email.send": {"label": "Send emails", "help": "Always needs your approval.",
                   "tools": {"send_email", "reply_email", "forward_email", "send_draft"}},
    "tasks.read": {"label": "See your reminders", "help": "Lists your scheduled tasks.", "tools": {"list_scheduled_tasks"}},
    "tasks.create": {"label": "Create reminders for you", "help": "Reminders only, never AI tasks.", "tools": {"schedule_task"}},
    "notion.read": {"label": "Read Notion", "help": "Search and read pages.",
                    "tools": {"search_notion", "list_pages", "read_page_content", "query_database"}},
    "notion.write": {"label": "Write to Notion", "help": "Create pages and add text.",
                     "tools": {"create_page", "update_page_title", "append_text_to_page"}},
    "images": {"label": "Make pictures", "help": "Generates pictures they ask for and sends them in the chat. Uses your "
               "free Cloudflare allowance, so each contact gets a few a day (Settings → Messaging).",
               "tools": {"generate_image"}},
    "documents": {"label": "Make documents", "help": "PDFs and Markdown files they ask for, sent in the chat.",
                  "tools": {"create_document"}},
    "news": {"label": "News", "help": "Latest headlines on topics they ask about.", "tools": {"get_news"}},
    "jira.read": {"label": "See Jira progress", "help": "Status, assignee, subtasks and comments, only in the projects "
                  "you shared (Connections → Jira).",
                  "tools": {"list_jira_projects", "search_jira_issues", "get_jira_issue", "my_jira_issues"}},
    "jira.comment": {"label": "Comment on Jira", "help": "Adds a comment from your account, marked as from them. "
                     "Always needs your approval.", "tools": {"add_jira_comment"}},
    "memory": {"label": "Remember things about them", "help": "Notes about this contact only.", "tools": {"remember"}},
    "requests": {"label": "Take requests for you", "help": "Callbacks, meeting requests, enquiries and messages: Donna "
                 "records them and tells you. Which types is set in their profile.",
                 "tools": {"record_request", "update_request", "suggest_meeting_slots"}},
    "initiate": {"label": "Message them first", "help": "Lets automations send this contact messages.", "tools": set()},
}
# External MCP servers (a future expense tracker, a task manager…) get one capability each.
EXTERNAL_PREFIX = "external:"
CONTACT_DEFAULTS = {"general": "on", "memory": "on", "requests": "on"}
# Runtime tools only the owner may use, whatever a contact's settings say.
OWNER_ONLY = {"list_memories", "forget_memory", "list_contacts", "read_chat", "send_message", "list_requests", "resolve_request"}
# Runtime tools only a contact's turn may use (they act on that contact's own conversation).
CONTACT_ONLY = {"record_request", "update_request", "suggest_meeting_slots"}
# Tools that write only on this computer and whose result rides along with the reply: they may run while Donna
# drafts a reply for approval, because nothing reaches the contact until the owner sends the draft.
DRAFTABLE = {"generate_image", "create_document"}


@dataclass
class TurnContext:
    """Everything about who a turn is for. None (the web chat's default) means the owner on the web."""
    audience: str = "owner"                 # owner | contact
    channel_id: str = ""                    # "" = web chat
    channel_label: str = ""
    contact: dict | None = None
    channel_conversation_id: str = ""
    capabilities: dict = field(default_factory=dict)  # resolved policy (contacts)
    suggest_only: bool = False              # drafting a reply for approval: nothing that writes may run
    no_tools: bool = False                  # a follow-up message after an approval
    on_gated: str = "approval"              # approval: persist for the owner | confirm: live web prompt
    persist_reply: bool = True              # False while drafting: the reply is saved when it's sent
    persist_user: bool = True               # False for system follow-ups that aren't the person's words
    extra_prompt: str = ""                  # e.g. the conversation summary
    history_since: float = 0                # history before this is in the summary
    intro_needed: bool = False              # Donna hasn't introduced herself in this chat yet
    approvals: list = field(default_factory=list)  # approvals created during the turn

    @property
    def is_contact(self) -> bool:
        return self.audience == "contact"


def capability_of(tool) -> str | None:
    if tool.server not in ("donna", "runtime"):
        return EXTERNAL_PREFIX + tool.server
    return next((cap for cap, spec in CAPABILITIES.items() if tool.name in spec["tools"]), None)


def valid_capability(cap: str) -> bool:
    return cap in CAPABILITIES or (cap.startswith(EXTERNAL_PREFIX) and len(cap) > len(EXTERNAL_PREFIX))


def clean_capabilities(caps: dict) -> dict:
    """Validate a capabilities patch from the dashboard. Raises ValueError."""
    out = {}
    for cap, level in (caps or {}).items():
        if not valid_capability(cap):
            raise ValueError(f"Unknown capability: {cap}")
        if level not in LEVELS:
            raise ValueError(f"{cap} must be off, on or approval")
        out[cap] = level
    return out


def forced_approval(cap: str, confirm_tools: set[str]) -> bool:
    """True when every tool of the capability sends, changes or deletes: it can never be plain "on"."""
    tools = CAPABILITIES.get(cap, {}).get("tools", set())
    return bool(tools) and tools <= confirm_tools


def decide(ctx: TurnContext | None, tool, args: dict | None = None) -> str:
    """allow | approve | deny for this tool (and, when known, these arguments)."""
    if ctx is None or ctx.audience == "owner":
        # The owner's approvals follow the permission mode in agent._run_tool.
        return "deny" if tool.name in CONTACT_ONLY else "allow"
    if ctx.no_tools or tool.name in OWNER_ONLY:
        return "deny"
    cap = capability_of(tool)
    level = ctx.capabilities.get(cap, "off") if cap else "off"
    if level not in ("on", "approval"):
        return "deny"
    if ctx.suggest_only and tool.writes and tool.name not in DRAFTABLE:
        return "deny"
    if tool.name == "schedule_task" and args is not None:
        # A contact may leave the owner a reminder, never an AI task: its prompt would run later,
        # unattended, with the owner's permissions.
        if (str(args.get("kind", "reminder")).lower() != "reminder" or str(args.get("permission_mode", "manual")).lower() != "manual"
                or str(args.get("deliver_to", "")).strip()):  # nor message someone else on the owner's behalf
            return "deny"
    if level == "approval" or tool.confirm:
        return "approve"
    return "allow"
