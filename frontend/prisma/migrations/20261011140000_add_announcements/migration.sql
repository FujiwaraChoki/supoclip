CREATE TABLE IF NOT EXISTS "announcements" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "title" VARCHAR(120) NOT NULL,
    "description" VARCHAR(2000) NOT NULL,
    "href" VARCHAR(500) NOT NULL,
    "action" VARCHAR(80) NOT NULL,
    "is_published" BOOLEAN NOT NULL DEFAULT false,
    "published_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS "announcements_is_published_published_at_idx"
    ON "announcements" ("is_published", "published_at");

-- Preserve the first announcement's ID so existing read badges stay cleared.
INSERT INTO "announcements" ("id", "title", "description", "href", "action", "is_published", "published_at")
VALUES (
    'creator-program-2026-10',
    'The creator program is open',
    'Make content about SupoClip and get hosted Pro for free while participating as an approved creator. Your audience gets 20% off their first three months. No cash payments or referral commissions.',
    '/affiliate',
    'Explore the creator program',
    true,
    '2026-10-09T00:00:00Z'
)
ON CONFLICT ("id") DO NOTHING;
