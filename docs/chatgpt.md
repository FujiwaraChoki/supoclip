# Use your ChatGPT plan for local clip analysis

Local self-hosters can connect an eligible ChatGPT account instead of providing
an LLM API key. This connects an AI provider to your existing SupoClip account;
it does not replace SupoClip's email/password login.

## Setup

1. Run the normal local Docker stack, or the API, frontend, PostgreSQL and worker.
2. Keep `SELF_HOST=true`. Set a strong `BACKEND_AUTH_SECRET` shared by the frontend,
   API and worker. Set `APP_SETTINGS_ENCRYPTION_KEY` on the API and worker to a
   stable, private value of at least 16 characters (the existing settings
   encryption falls back to `BACKEND_AUTH_SECRET`). Back up that key securely.
3. Set `NEXT_PUBLIC_APP_URL` to your local frontend origin, for example
   `http://localhost:3107`, and open that exact address in your browser.
4. Open **Settings → Use your ChatGPT plan → Continue with ChatGPT**.
5. Complete OpenAI's sign-in and consent screens. Allow ChatGPT plan usage.
6. Choose a model from your account's live catalog and select
   **Use ChatGPT for clip analysis**. New video jobs use that model for highlight
   selection and comparative ranking, including long-video analysis windows.

You still need transcription: use AssemblyAI with its API key, or configure
`TRANSCRIPTION_PROVIDER=whisper` for local transcription. No `OPENAI_API_KEY` is
needed for ChatGPT plan analysis. Existing provider settings remain available
for accounts that have not enabled ChatGPT.

## Local callback

OpenAI's open-source registration flow requires an HTTP callback on
`127.0.0.1`. The default is:

```env
CHATGPT_REDIRECT_URI=http://127.0.0.1:8000/chatgpt/callback
```

Use the backend's **host** port. Docker Compose forwards this setting to the API;
local Python users set it in the root/backend environment. Keep the host and path
unchanged for returning sign-ins; only the port may vary. No additional Docker
port is needed with the standard backend port mapping.

The backend callback redirects the authorization code to the configured local
frontend. The frontend checks its HttpOnly OAuth state cookie and current
SupoClip session, then the backend consumes the matching one-time attempt before
exchanging the code. OpenAI ID-token signature, issuer, audience, expiration,
nonce and returning account identity are verified before credentials are saved.

The browser and backend must run on the same computer. This first implementation
does not support remote VMs, LAN browser clients, public deployments, or hosted
mode. Do not expose it through a public reverse proxy. A separately registered
website login integration is not part of this feature.

## Accounts, usage and errors

- Each SupoClip user owns separate encrypted ChatGPT registrations. You can add
  multiple accounts/workspaces, reconnect a saved registration, and choose which
  account/model to use. Registration labels distinguish accounts with the same
  email. No credentials are returned to browser storage or placed in job payloads.
- A stable host ID and account/client mappings persist in PostgreSQL. Access,
  refresh and ID tokens use the existing encrypted-settings format. Concurrent
  refreshes are serialized with PostgreSQL row locks across API/worker processes.
- ChatGPT-derived analysis bypasses the shared analysis cache. Transcript caching
  remains available. One user's credentials or plan are never used for another
  user's task. Analysis context is reset on completion, cancellation and failure.
- ChatGPT plan usage and SupoClip billing are separate. Usage counts against your
  eligible ChatGPT plan or available credits; it is not unlimited. Manage usage
  and access at [ChatGPT usage settings](https://chatgpt.com/settings/usage).
- Usage-limit, revoked-access and interrupted-stream errors fail analysis with
  guidance to manage usage or reconnect. They never switch to another paid
  provider. Structured output is validated against the existing clip models.
- **Sign out of ChatGPT** attempts remote session revocation and removes local
  tokens while preserving the registration for a later sign-in. If revocation
  cannot be confirmed, the UI directs you to disconnect SupoClip in ChatGPT.
  Future jobs return to your configured provider after you explicitly sign out.
  An already-running inference request may finish before sign-out takes effect.

## Validation

Run `make check` and `make test`, plus the ChatGPT browser checks:

```bash
cd frontend
pnpm exec playwright test --config playwright.polish.config.ts e2e/chatgpt.spec.ts
```

Automated tests mock OpenAI; they do not spend ChatGPT allowance or require real
credentials. Before considering the integration verified for a deployment,
perform real browser consent, load the account catalog, select a model, and clip a
short video. Check a subsequent refresh and sign-out as well. Eligibility and
OpenAI's preview behavior can change; this implementation cannot guarantee
access for every account.

### First Docker startup

`docker compose ps` lists containers, not image builds. A completed frontend
build does not mean the backend and worker have finished. Check all build records
with `docker buildx history ls`, and review the startup command's final result.

If a build reports `Read-only file system`, check host free space with `df -h .`.
A full host disk can leave Docker's VM storage read-only. Reclaim disposable
build/dependency caches, restart Docker Desktop, and retry the failed build.
Do not remove database volumes to fix a build-cache problem.

Compose uses structured bind mounts with `create_host_path: false`, which also
supports local project paths containing a colon without changing the folder name.

## Protocol references

- [Open-source registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and renewable sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and streamed inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
