-- Per-task secondary (active-word highlight) and tertiary (text outline/stroke)
-- caption colours, alongside the existing font_color (primary text).
-- NULL means "use the caption template's baked colour", so existing tasks are
-- unaffected. No DEFAULT for that reason.

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS highlight_color VARCHAR(7);

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS stroke_color VARCHAR(7);
