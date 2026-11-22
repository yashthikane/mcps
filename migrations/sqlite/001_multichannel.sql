-- Multi-channel messaging: channels, contacts and their permissions, channel conversations,
-- event deduplication, memories, persisted approvals and the audit log.
-- Never edit this file once applied; add 002_*.sql instead.

-- Where a conversation came from. The web sidebar lists only 'web'; channel chats
-- ('whatsapp', 'telegram') reuse messages/history/FTS through their own rows.
ALTER TABLE conversations ADD COLUMN source TEXT NOT NULL DEFAULT 'web';

CREATE TABLE channels (
  id TEXT PRIMARY KEY,                       -- one account per type for now: 'whatsapp', 'telegram'
  type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,        -- connected by the owner (disconnect clears it)
  paused INTEGER NOT NULL DEFAULT 0,         -- channel-level pause: nothing is processed or sent
  status TEXT NOT NULL DEFAULT 'disconnected',
  config TEXT NOT NULL DEFAULT '{}',         -- non-secret adapter state; secrets live in the vault
  last_error TEXT NOT NULL DEFAULT '',
  last_activity_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE contacts (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'disabled' CHECK (mode IN ('disabled', 'suggest', 'auto', 'human_first')),
  is_owner INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0,
  discovered INTEGER NOT NULL DEFAULT 0,     -- created from an unknown sender, not added by the owner
  language TEXT NOT NULL DEFAULT '',
  tone TEXT NOT NULL DEFAULT '',
  timezone TEXT NOT NULL DEFAULT '',
  working_hours TEXT NOT NULL DEFAULT '{}',  -- {"days": [0-6, Monday=0], "start": "09:00", "end": "20:00"}; {} = any time
  share_memory INTEGER NOT NULL DEFAULT 1,   -- 0: memories stay with the channel they were learnt on
  notes TEXT NOT NULL DEFAULT '',
  last_interaction_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX contacts_one_owner ON contacts(is_owner) WHERE is_owner = 1;

CREATE TABLE contact_identities (
  id TEXT PRIMARY KEY,
  contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  channel_id TEXT NOT NULL,
  external_id TEXT NOT NULL,                 -- e.g. 919812345678@c.us, an @lid id, a Telegram user id
  phone TEXT NOT NULL DEFAULT '',            -- digits only, when known
  display_name TEXT NOT NULL DEFAULT '',
  verified INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  UNIQUE (channel_id, external_id)
);
CREATE INDEX contact_identities_phone ON contact_identities(channel_id, phone);
CREATE INDEX contact_identities_contact ON contact_identities(contact_id);

CREATE TABLE contact_policies (
  contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  channel_id TEXT NOT NULL DEFAULT '',       -- '' = every channel; a channel row overrides it
  capabilities TEXT NOT NULL DEFAULT '{}',   -- {"calendar.availability": "on" | "approval" | "off", ...}
  updated_at REAL NOT NULL,
  PRIMARY KEY (contact_id, channel_id)
);

CREATE TABLE channel_conversations (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL,
  contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  external_chat_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  escalated INTEGER NOT NULL DEFAULT 0,
  unread INTEGER NOT NULL DEFAULT 0,
  summary TEXT NOT NULL DEFAULT '',
  summary_upto REAL NOT NULL DEFAULT 0,      -- messages up to this time are folded into the summary
  last_message_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE (channel_id, external_chat_id)
);
CREATE INDEX channel_conversations_contact ON channel_conversations(contact_id);

CREATE TABLE channel_messages (
  id TEXT PRIMARY KEY,
  channel_conversation_id TEXT NOT NULL REFERENCES channel_conversations(id) ON DELETE CASCADE,
  event_id TEXT,                             -- channel_events.id of an inbound message
  external_id TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
  author TEXT NOT NULL,                      -- contact | donna | owner
  message_type TEXT NOT NULL DEFAULT 'text',
  content TEXT NOT NULL DEFAULT '',
  attachments TEXT NOT NULL DEFAULT '[]',
  -- in:  received | processing | answered | drafted | escalated | ignored | failed
  -- out: queued | sent | delivered | read | failed
  status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX channel_messages_conv ON channel_messages(channel_conversation_id, created_at);
CREATE INDEX channel_messages_external ON channel_messages(external_id);
CREATE INDEX channel_messages_event ON channel_messages(event_id);

CREATE TABLE channel_events (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL,
  external_event_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'received' CHECK (status IN ('received', 'processing', 'done', 'ignored', 'failed')),
  payload TEXT NOT NULL DEFAULT '{}',        -- the normalised IncomingMessage, so a failed event can be retried
  result TEXT NOT NULL DEFAULT '',
  received_at REAL NOT NULL,
  processed_at REAL,
  UNIQUE (channel_id, external_event_id)
);
CREATE INDEX channel_events_status ON channel_events(status);

CREATE TABLE memories (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL CHECK (scope IN ('global', 'contact')),
  contact_id TEXT REFERENCES contacts(id) ON DELETE CASCADE,
  channel_id TEXT NOT NULL DEFAULT '',       -- '' = every channel of the contact
  content TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'owner',      -- owner | donna
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX memories_contact ON memories(contact_id);

CREATE TABLE approvals (
  id TEXT PRIMARY KEY,                       -- short code the owner can type: "approve A7K2"
  kind TEXT NOT NULL CHECK (kind IN ('tool', 'reply')),
  audience TEXT NOT NULL DEFAULT 'contact',  -- who asked: owner | contact
  channel_id TEXT NOT NULL DEFAULT '',
  contact_id TEXT,
  channel_conversation_id TEXT,
  tool TEXT NOT NULL DEFAULT '',
  args TEXT NOT NULL DEFAULT '{}',
  payload_hash TEXT NOT NULL,                -- sha256 of kind + tool + args: approval is bound to this exact payload
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'executing', 'executed', 'failed', 'rejected', 'expired')),
  result TEXT NOT NULL DEFAULT '',
  expires_at REAL NOT NULL,
  created_at REAL NOT NULL,
  decided_at REAL,
  decided_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX approvals_status ON approvals(status, created_at);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  actor TEXT NOT NULL,                       -- owner | system | donna | contact:<id> | channel:<id>
  action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL DEFAULT 'ok',        -- ok | denied | failed | rejected
  detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_log_at ON audit_log(at);
