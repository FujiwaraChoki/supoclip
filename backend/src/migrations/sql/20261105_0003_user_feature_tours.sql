-- One row per account and feature tour (frontend/src/lib/feature-tours.ts).
-- A row means the tour was shown, and how it ended says whether it helped.
CREATE TABLE IF NOT EXISTS user_feature_tours (
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tour_id VARCHAR(64) NOT NULL,
    shown_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    dismissed_at TIMESTAMP WITH TIME ZONE,
    -- The last step reached, counted from the first real step, or null until the tour ends.
    last_step INTEGER,
    PRIMARY KEY (user_id, tour_id)
);
CREATE INDEX IF NOT EXISTS ix_user_feature_tours_tour ON user_feature_tours (tour_id);
