-- Migration: Add completion notification fields to tasks/users tables
-- This migration can be run on existing databases

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'notify_on_completion'
    ) THEN
        ALTER TABLE users ADD COLUMN notify_on_completion BOOLEAN NOT NULL DEFAULT true;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tasks' AND column_name = 'completion_notification_sent_at'
    ) THEN
        ALTER TABLE tasks ADD COLUMN completion_notification_sent_at TIMESTAMP WITH TIME ZONE;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tasks' AND column_name = 'notify_on_completion'
    ) THEN
        UPDATE users AS u
        SET notify_on_completion = COALESCE(
            (
                SELECT t.notify_on_completion
                FROM tasks AS t
                WHERE t.user_id = u.id
                ORDER BY t.created_at DESC NULLS LAST, t.updated_at DESC NULLS LAST
                LIMIT 1
            ),
            u.notify_on_completion
        );

        ALTER TABLE tasks DROP COLUMN notify_on_completion;
    END IF;
END $$;

COMMENT ON COLUMN users.notify_on_completion IS 'Whether to email the user when clip generation completes';
COMMENT ON COLUMN tasks.completion_notification_sent_at IS 'When the completion email was successfully sent';
