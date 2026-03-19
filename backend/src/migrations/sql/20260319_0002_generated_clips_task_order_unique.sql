DELETE FROM generated_clips gc
USING generated_clips newer
WHERE gc.task_id = newer.task_id
  AND gc.clip_order = newer.clip_order
  AND (
    gc.created_at < newer.created_at
    OR (gc.created_at = newer.created_at AND gc.id < newer.id)
  );

UPDATE tasks t
SET generated_clips_ids = sub.clip_ids,
    updated_at = CURRENT_TIMESTAMP
FROM (
    SELECT task_id, ARRAY_AGG(id ORDER BY clip_order ASC, created_at ASC) AS clip_ids
    FROM generated_clips
    GROUP BY task_id
) sub
WHERE t.id = sub.task_id;

UPDATE tasks
SET generated_clips_ids = NULL,
    updated_at = CURRENT_TIMESTAMP
WHERE generated_clips_ids IS NOT NULL
  AND id NOT IN (SELECT DISTINCT task_id FROM generated_clips);

CREATE UNIQUE INDEX IF NOT EXISTS uq_generated_clips_task_order
ON generated_clips(task_id, clip_order);
