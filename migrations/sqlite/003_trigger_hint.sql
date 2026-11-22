-- When Donna told this chat once that she answers only to the trigger word (e.g. @donna).
-- Never edit this file once applied; add 004_*.sql instead.
ALTER TABLE channel_conversations ADD COLUMN trigger_hint_at REAL;
