"""Channel types Donna knows. Adding a platform = one BaseChannel subclass registered here; the
manager, contacts, permissions, approvals and the agent runtime don't change.

Not built yet (extension points, see docs/ARCHITECTURE.md §Channels):
- slack:   Socket Mode (app-level token, no public URL), events → IncomingMessage, chat.postMessage
- discord: Gateway bot (DMs only), MESSAGE_CREATE → IncomingMessage, channel messages API
"""
from .base import BaseChannel
from .telegram import TelegramChannel
from .whatsapp import WhatsAppChannel

REGISTRY: dict[str, type[BaseChannel]] = {
    WhatsAppChannel.type: WhatsAppChannel,
    TelegramChannel.type: TelegramChannel,
}
