-- The away greeting and its quick menu (donna/availability.py).
-- Never edit this file once applied; add 006_*.sql instead.
ALTER TABLE channel_conversations ADD COLUMN greeted_key TEXT NOT NULL DEFAULT '';  -- the status period last greeted for
ALTER TABLE channel_conversations ADD COLUMN greeted_at REAL;                       -- when the greeting + menu went out
ALTER TABLE channel_conversations ADD COLUMN menu_options TEXT NOT NULL DEFAULT '[]'; -- option ids, in the order sent
ALTER TABLE channel_conversations ADD COLUMN menu_flow TEXT NOT NULL DEFAULT '';    -- the option being worked through
ALTER TABLE channel_conversations ADD COLUMN menu_flow_at REAL;                     -- last message in that flow
