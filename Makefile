BACKEND_TEST_ENV = DATABASE_URL=$${TEST_DATABASE_URL:-$${DATABASE_URL:-postgresql+asyncpg://supoclip:supoclip_password@127.0.0.1:5432/supoclip}} REDIS_HOST=$${REDIS_HOST:-127.0.0.1} REDIS_PORT=$${REDIS_PORT:-6379}
FRONTEND_TEST_ENV = DATABASE_URL=$${TEST_DATABASE_URL:-$${DATABASE_URL:-postgresql://supoclip:supoclip_password@127.0.0.1:5432/supoclip}} BACKEND_AUTH_SECRET=$${BACKEND_AUTH_SECRET:-supoclip_test_secret} BETTER_AUTH_SECRET=$${BETTER_AUTH_SECRET:-supoclip_better_auth_test_secret} NEXT_PUBLIC_SELF_HOST=true

.PHONY: test test-backend test-frontend test-e2e test-ci

test: test-backend test-frontend

test-backend:
	cd backend && uv sync --all-groups
	cd backend && $(BACKEND_TEST_ENV) .venv/bin/pytest

test-frontend:
	cd frontend && npm install
	cd frontend && $(FRONTEND_TEST_ENV) npm run test:coverage

test-e2e:
	cd frontend && npm install
	cd frontend && $(FRONTEND_TEST_ENV) npx playwright install --with-deps
	cd frontend && $(FRONTEND_TEST_ENV) npm run test:e2e

test-ci: test-backend test-frontend test-e2e
