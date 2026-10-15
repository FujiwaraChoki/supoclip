CREATE TABLE IF NOT EXISTS "service_statuses" (
    "id" VARCHAR(80) NOT NULL PRIMARY KEY,
    "name" VARCHAR(120) NOT NULL,
    "status" VARCHAR(20) NOT NULL CHECK ("status" IN ('operational', 'degraded', 'outage', 'maintenance')),
    "headline" VARCHAR(120) NOT NULL,
    "description" VARCHAR(2000) NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO "service_statuses" ("id", "name", "status", "headline", "description")
VALUES (
    'video-downloads',
    'Video downloads',
    'degraded',
    'Investigating',
    'We are investigating slow video downloads. Importing videos from YouTube may take longer than usual or fail with a timeout. Please avoid repeatedly retrying the same video while we investigate. We will update this page when we have more information.'
)
ON CONFLICT ("id") DO NOTHING;
