-- Re-runnable because both the API and Prisma apply this schema change.
ALTER TABLE users ADD COLUMN IF NOT EXISTS product_updates_unsubscribed_at TIMESTAMP WITH TIME ZONE;
