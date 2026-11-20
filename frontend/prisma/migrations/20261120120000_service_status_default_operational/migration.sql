-- The October 2026 download incident was seeded as this row's starting value, so
-- every install created since showed YouTube as degraded with nobody investigating.
-- Reset only that untouched seed. A row an operator has edited, including a real
-- incident, is left as it is.
UPDATE "service_statuses"
SET "status" = 'operational',
    "headline" = 'Operational',
    "description" = 'Video downloads are working normally.',
    "updated_at" = CURRENT_TIMESTAMP
WHERE "id" = 'video-downloads'
  AND "status" = 'degraded'
  AND "headline" = 'Investigating'
  AND "description" = 'We are investigating slow video downloads. Importing videos from YouTube may take longer than usual or fail with a timeout. Please avoid repeatedly retrying the same video while we investigate. We will update this page when we have more information.';
