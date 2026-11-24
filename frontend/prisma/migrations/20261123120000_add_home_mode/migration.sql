-- What the home screen opens on: 'agent' (Creator plans), 'manual', or NULL until chosen.
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "home_mode" VARCHAR(10);
