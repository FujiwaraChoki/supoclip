-- Content policy detection: per-user editable word lists (regex engine),
-- per-project enable/sensitivity settings, and a per-clip cache of flagged
-- spans so re-opening the editor never re-scans. `words`/`categories_enabled`
-- are JSON-encoded text, same convention as project_templates.settings.
CREATE TABLE IF NOT EXISTS content_policy_word_lists (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    category VARCHAR(20) NOT NULL,
    words TEXT NOT NULL,
    is_default_reset BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_content_policy_word_lists_user_category
    ON content_policy_word_lists(user_id, category);

-- No SQL-level default for categories_enabled: it's a JSON blob (would need
-- awkward colon-escaping in a raw text() DEFAULT literal, since SQLAlchemy's
-- text() parses bare ":" as a bind-parameter marker). ContentPolicyRepository
-- always writes an explicit value on insert and supplies the equivalent
-- Python-level default (DEFAULT_CATEGORIES_ENABLED) when no row exists yet.
CREATE TABLE IF NOT EXISTS content_policy_project_settings (
    task_id VARCHAR(36) PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    sensitivity VARCHAR(10) NOT NULL DEFAULT 'medium',
    categories_enabled TEXT,
    ollama_borderline_check_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- JSON array of {word, category, start, end, severity, source} spans found
-- in this clip's caption text, same TEXT-JSON pattern as
-- generated_clips.reactions/hook_title_variants.
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS content_policy_flags TEXT DEFAULT '[]';
