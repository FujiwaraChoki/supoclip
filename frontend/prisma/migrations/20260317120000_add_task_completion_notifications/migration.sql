ALTER TABLE "users"
ADD COLUMN IF NOT EXISTS "notify_on_completion" BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE "tasks"
ADD COLUMN IF NOT EXISTS "completion_notification_sent_at" TIMESTAMPTZ;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'tasks' AND column_name = 'notify_on_completion'
  ) THEN
    UPDATE "users" AS u
    SET "notify_on_completion" = COALESCE(
      (
        SELECT t.notify_on_completion
        FROM "tasks" AS t
        WHERE t.user_id = u.id
        ORDER BY t.created_at DESC NULLS LAST, t.updated_at DESC NULLS LAST
        LIMIT 1
      ),
      u."notify_on_completion"
    );

    ALTER TABLE "tasks" DROP COLUMN "notify_on_completion";
  END IF;
END $$;
