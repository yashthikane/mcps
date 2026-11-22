"""The contract every messaging channel implements, and the messages that cross it.

An adapter only translates: platform events in → IncomingMessage / StatusUpdate / DeliveryUpdate,
OutgoingMessage → the platform's send API. It never decides who may talk to Donna, never calls the
model and never touches tools; the ChannelManager and the agent runtime do that for every channel.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import ClassVar

from pydantic import BaseModel, Field


class IncomingMessage(BaseModel):
    event_id: str                      # the platform's delivery/idempotency key: deduplication
    channel_id: str
    channel_type: str
    sender_id: str                     # identity on the platform (reply here)
    sender_name: str | None = None
    sender_phone: str = ""             # digits, when the platform knows it
    chat_id: str                       # the conversation on the platform (a private chat = the sender)
    message_id: str
    message_type: str = "text"         # text | image | audio | video | document | sticker | location | other
    text: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    attachments: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)  # platform-specific extras, never read by the agent


class OutgoingMessage(BaseModel):
    channel_id: str
    recipient_id: str
    message_type: str = "text"
    text: str
    attachments: list[dict] = Field(default_factory=list)
    reply_to_message_id: str | None = None
    idempotency_key: str = ""          # our message id: a retried send isn't delivered twice
    metadata: dict = Field(default_factory=dict)


class SendResult(BaseModel):
    ok: bool
    external_id: str = ""
    error: str = ""
    retryable: bool = False   # only when the platform certainly didn't get it: retrying can't duplicate
    uncertain: bool = False   # the platform failed after accepting it: it may have been delivered


class StatusUpdate(BaseModel):
    """The connection changed: connected | qr | connecting | disconnected | error."""
    status: str
    detail: str = ""
    account: str = ""                  # e.g. the WhatsApp number or bot username once connected


class DeliveryUpdate(BaseModel):
    external_id: str
    status: str                        # sent | delivered | read | failed


ChannelEvent = IncomingMessage | StatusUpdate | DeliveryUpdate


class ChannelError(Exception):
    """A user-facing channel failure. `status` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class WebhookRejected(ChannelError):
    """Authentication, size or replay checks failed: the event is dropped and audited."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message, status)


class BaseChannel(ABC):
    type: ClassVar[str]
    label: ClassVar[str]
    max_text: ClassVar[int] = 4000

    def __init__(self, channel_id: str, config: dict, settings: Callable[[], dict],
                 on_events: Callable[[list[ChannelEvent]], Awaitable[None]], save_config: Callable[[dict], None]):
        self.id = channel_id
        self.config = dict(config or {})
        self.settings = settings          # current Donna settings
        self.on_events = on_events        # push events from a background loop (polling channels)
        self._save_config = save_config

    def save_config(self, **values) -> None:
        self.config.update(values)
        self._save_config(self.config)

    # ------------------------------------------------------------------ lifecycle
    @abstractmethod
    async def connect(self) -> StatusUpdate:
        """Set the account up (sessions, webhooks, tokens). Idempotent."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Stop receiving; keep credentials so connect() can resume."""

    @abstractmethod
    async def health_check(self) -> StatusUpdate:
        """The live connection state, asked of the platform."""

    async def start(self) -> None:
        """Begin background work (a polling loop). Webhook channels need none."""

    async def stop(self) -> None:
        """End background work."""

    async def login_code(self) -> dict:
        """What the owner needs to link the account (a WhatsApp QR code). {} when not applicable."""
        return {}

    # ------------------------------------------------------------------ messages
    def parse_webhook(self, raw: bytes, headers: dict[str, str]) -> list[ChannelEvent]:
        """Verify and normalise one webhook delivery. Raises WebhookRejected."""
        raise WebhookRejected("This channel doesn't accept webhooks.", 404)

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> SendResult:
        """Deliver one message (text at most max_text characters)."""

    def identity_for(self, value: str) -> tuple[str, str]:
        """(external_id, phone digits) for what the owner typed when adding a contact."""
        return value.strip(), ""

    def format_text(self, markdown: str) -> str:
        """Donna writes Markdown; most chat apps want plain text."""
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", markdown)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
        text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)
        return text.strip()
