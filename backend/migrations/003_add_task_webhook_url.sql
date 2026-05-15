-- Migration: Per-task webhook callback URL
-- Adds an optional outbound webhook target so external systems (e.g. Brand
-- Ninja) can be notified on terminal task status without polling.
-- This migration can be run on existing databases.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tasks' AND column_name = 'webhook_url'
    ) THEN
        ALTER TABLE tasks ADD COLUMN webhook_url VARCHAR(2048);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tasks' AND column_name = 'webhook_delivered_at'
    ) THEN
        ALTER TABLE tasks ADD COLUMN webhook_delivered_at TIMESTAMP WITH TIME ZONE;
    END IF;
END $$;
