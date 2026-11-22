-- The calendar event a meeting request was booked as, so a correction moves that event instead of
-- creating another one (and the contact keeps the same Meet link).
-- Never edit this file once applied; add 005_*.sql instead.
ALTER TABLE requests ADD COLUMN event_id TEXT NOT NULL DEFAULT '';
ALTER TABLE requests ADD COLUMN event_start TEXT NOT NULL DEFAULT '';   -- as booked, e.g. 2026-11-22T19:00
ALTER TABLE requests ADD COLUMN event_end TEXT NOT NULL DEFAULT '';
ALTER TABLE requests ADD COLUMN meet_link TEXT NOT NULL DEFAULT '';
