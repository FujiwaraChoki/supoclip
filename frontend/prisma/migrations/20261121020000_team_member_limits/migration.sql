-- Optional per-member monthly generation rules (NULL = unlimited up to the team pool).
ALTER TABLE team_members
  ADD COLUMN IF NOT EXISTS monthly_minute_limit INTEGER CHECK (monthly_minute_limit IS NULL OR monthly_minute_limit >= 0),
  ADD COLUMN IF NOT EXISTS monthly_ai_edit_limit INTEGER CHECK (monthly_ai_edit_limit IS NULL OR monthly_ai_edit_limit >= 0);
