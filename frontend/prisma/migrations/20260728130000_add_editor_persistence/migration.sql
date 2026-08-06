-- Versioned editor documents and task-scoped user media.
-- Generated clips intentionally remain outside editor_assets.
CREATE TABLE IF NOT EXISTS "editor_projects" (
    "task_id" VARCHAR(36) NOT NULL,
    "project" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "version" INTEGER NOT NULL DEFAULT 1,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "editor_projects_pkey" PRIMARY KEY ("task_id"),
    CONSTRAINT "editor_projects_version_check" CHECK ("version" >= 1),
    CONSTRAINT "editor_projects_task_id_fkey" FOREIGN KEY ("task_id") REFERENCES "tasks"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS "editor_assets" (
    "id" VARCHAR(36) NOT NULL DEFAULT uuid_generate_v4()::text,
    "task_id" VARCHAR(36) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "kind" VARCHAR(16) NOT NULL,
    "mime_type" VARCHAR(160) NOT NULL,
    "size_bytes" BIGINT NOT NULL,
    "file_path" TEXT NOT NULL,
    "duration" DOUBLE PRECISION,
    "width" INTEGER,
    "height" INTEGER,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "editor_assets_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "editor_assets_kind_check" CHECK ("kind" IN ('video', 'image', 'audio')),
    CONSTRAINT "editor_assets_size_check" CHECK ("size_bytes" > 0),
    CONSTRAINT "editor_assets_task_id_fkey" FOREIGN KEY ("task_id") REFERENCES "tasks"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS "editor_assets_task_id_idx" ON "editor_assets"("task_id");
