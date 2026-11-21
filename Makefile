BACKEND_TEST_ENV = DATABASE_URL=$${TEST_DATABASE_URL:-$${DATABASE_URL:-postgresql+asyncpg://supoclip:supoclip_password@127.0.0.1:5432/supoclip}} REDIS_HOST=$${REDIS_HOST:-127.0.0.1} REDIS_PORT=$${REDIS_PORT:-6379}
FRONTEND_TEST_ENV = DATABASE_URL=$${TEST_DATABASE_URL:-$${DATABASE_URL:-postgresql://supoclip:supoclip_password@127.0.0.1:5432/supoclip}} BACKEND_AUTH_SECRET=$${BACKEND_AUTH_SECRET:-supoclip_test_secret} BETTER_AUTH_SECRET=$${BETTER_AUTH_SECRET:-supoclip_better_auth_test_secret} NEXT_PUBLIC_SELF_HOST=true

.PHONY: test check test-backend test-frontend test-e2e test-ci fx-assets

test: test-backend test-frontend

test-backend:
	cd backend && uv sync --frozen --all-groups
	cd backend && $(BACKEND_TEST_ENV) .venv/bin/pytest

test-frontend:
	cd frontend && pnpm install --frozen-lockfile
	cd frontend && $(FRONTEND_TEST_ENV) pnpm run test:coverage

test-e2e:
	cd frontend && pnpm install --frozen-lockfile
	cd frontend && $(FRONTEND_TEST_ENV) pnpm exec playwright install --with-deps
	cd frontend && $(FRONTEND_TEST_ENV) pnpm run test:e2e

check:
	cd frontend && pnpm run lint && pnpm run typecheck

test-ci: check test-backend test-frontend test-e2e

# Regenerate the committed dynamic-edit assets in backend/src/fx_assets (emoji atlas, manifest). The SFX and music
# beds are committed CC0 recordings (SOURCES-CC0.txt, music/SOURCES-CC0.txt); write_manifest.py only hashes and
# measures them. `make fx-assets MUSIC_LIBRARY=/path/to/music-library` also re-imports the beds (import_music.py).
# Needs ffmpeg, deno and the Noto Color Emoji font. CI never runs this; it only checks the manifest hashes.
FX_PYTHON ?= .venv/bin/python
DENO ?= deno
MUSIC_LIBRARY ?=

fx-assets:
	$(if $(MUSIC_LIBRARY),cd backend && $(FX_PYTHON) scripts/fx/import_music.py $(abspath $(MUSIC_LIBRARY)))
	cd backend && $(FX_PYTHON) scripts/fx/emoji_list.py | $(DENO) run --allow-read --allow-write --allow-env \
		--allow-ffi --allow-sys=homedir --allow-net=registry.npmjs.org scripts/fx/build_emoji_atlas.ts \
		--list - --out src/fx_assets/emoji
	cd backend && $(FX_PYTHON) scripts/fx/write_manifest.py
