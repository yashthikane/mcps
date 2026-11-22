-- Donna's identity per contact (communication profiles) and request management.
-- Never edit this file once applied; add 003_*.sql instead.

-- Communication profile. Empty values fall back to the relationship's defaults (donna/identity.py).
ALTER TABLE contacts ADD COLUMN relationship TEXT NOT NULL DEFAULT '';         -- family | friend | client | colleague | vendor | other
ALTER TABLE contacts ADD COLUMN relationship_label TEXT NOT NULL DEFAULT '';   -- e.g. "Mother", "Client at ABC Ltd"
ALTER TABLE contacts ADD COLUMN address_as TEXT NOT NULL DEFAULT '';           -- e.g. "Aai"
ALTER TABLE contacts ADD COLUMN script TEXT NOT NULL DEFAULT '';               -- '' (match them) | latin | devanagari
ALTER TABLE contacts ADD COLUMN formality TEXT NOT NULL DEFAULT '';            -- '' (relationship default) | casual | neutral | formal
ALTER TABLE contacts ADD COLUMN greeting TEXT NOT NULL DEFAULT '';             -- greeting style, e.g. "Namaskar"
ALTER TABLE contacts ADD COLUMN intro_message TEXT NOT NULL DEFAULT '';        -- '' = generated from language/relationship
ALTER TABLE contacts ADD COLUMN background TEXT NOT NULL DEFAULT '';           -- context for understanding only
ALTER TABLE contacts ADD COLUMN special_instructions TEXT NOT NULL DEFAULT '';
ALTER TABLE contacts ADD COLUMN allowed_requests TEXT NOT NULL DEFAULT '[]';   -- request types Donna may take; [] = all

-- When Donna last introduced herself in a chat (so she doesn't repeat it in an active conversation).
ALTER TABLE channel_conversations ADD COLUMN introduced_at REAL;

CREATE TABLE requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,                 -- shown as R<id>
  contact_id TEXT REFERENCES contacts(id) ON DELETE CASCADE,
  channel_id TEXT NOT NULL DEFAULT '',
  channel_conversation_id TEXT REFERENCES channel_conversations(id) ON DELETE SET NULL,
  type TEXT NOT NULL,                                   -- callback | meeting | business_enquiry | … (donna/requests.py)
  summary TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '{}',                   -- extracted details, e.g. {"preferred_time": "tomorrow 4 PM"}
  urgency TEXT NOT NULL DEFAULT 'normal' CHECK (urgency IN ('low', 'normal', 'high', 'urgent')),
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'information_required', 'waiting_for_owner', 'approved',
                                                      'in_progress', 'completed', 'rejected', 'cancelled')),
  required_action TEXT NOT NULL DEFAULT '',
  missing TEXT NOT NULL DEFAULT '',                     -- details Donna still needs from the contact
  outcome TEXT NOT NULL DEFAULT '',
  owner_notified INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX requests_status ON requests(status, updated_at);
CREATE INDEX requests_conversation ON requests(channel_conversation_id);
