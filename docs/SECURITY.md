# Security, guardrails and rate limits

## Who can do what
- **admin**: documents, folders, uploads, the Users page, unmetered chat.
- **user**: chat only (read-only source picker), limited by credits (see `core/quota.py`).
- Every request re-loads the user from the database: disabling a user or changing their password/role takes effect immediately (`token_version`). Tokens last `ACCESS_TOKEN_TTL_MINUTES` (24 h by default).
- The account named by `ADMIN_USERNAME` is **owned by `.env`**: created on start, password re-synced to `ADMIN_PASSWORD` on every start (existing sessions are signed out when it changes), and the Users page/API refuse to change its username, password, role or status, or delete it. Change the password by editing `.env` and restarting the backend. Other accounts (including other admins) are managed from the Users page.
- Not implemented: per-user / per-folder access control. Any signed-in user can query any indexed document through the API; folder scoping in the UI is a convenience.

## Topic guardrails (`core/guardrails.py`, `retrieval/chat_service.py`)
The assistant answers only from the knowledge base. Layers, cheapest first, none of them costs a model call:
1. **Query screen** -- jailbreak / system-prompt extraction / role-marker patterns and bare greetings get a canned reply before any credit or LLM use.
2. **History sanitising** -- the browser sends prior turns back, so they are size-capped and injection-screened.
3. **Prompt** -- `SCOPE_POLICY` in `rag_core/local_chat.py` (documented byte-exact in `docs/PROMPTS.md`).
4. **Evidence gate (the hard guarantee)** -- answer text is held back until the agent has read real document content (`get_page_content` / `get_document_structure`). No evidence = the answer is discarded and replaced by a refusal. Nothing the model produced without reading documents ever reaches the user.
5. **Output check** -- answers that echo tool names / instructions are replaced.
6. **Wire hygiene** -- `thinking` events dropped, raw `tool_result` text replaced by `{"ok": true}`, engine errors replaced by a generic message (details go to the server log with a request id).

Refusals (layers 1, 4, 5) never cost credits.

## Rate limiting (`core/ratelimit.py`, Postgres-backed, correct across workers)
| What | Default |
|---|---|
| Login | 10 requests/min/IP; **lockout** after 5 failed attempts per username or 15 per IP (15 min) |
| Chat | 6 requests/min/user + **one stream at a time per user** (crash-safe lease) + the credit allowance |
| Upload | 20/hour/user; size cap `MAX_UPLOAD_MB`; magic-byte / zip-bomb checks; sanitised filenames |
| Admin writes | 60/min |
| Everything else | 300/min/IP in-process backstop (health excluded) |

All knobs are `RL_*` settings in `core/config.py`. Behind a proxy set `TRUSTED_PROXY_HOPS=1`; otherwise `X-Forwarded-For` is ignored.

## Hardening checklist
- `ENVIRONMENT=prod` **refuses to start** with a default/short `AUTH_SECRET`, weak `ADMIN_PASSWORD`, `change_me` DB or S3 secrets, or localhost CORS origins; `/docs` and `/openapi.json` are disabled.
- Security headers on the API (nosniff, frame deny, no-referrer, HSTS in prod, `no-store`) and a CSP + the same headers on the frontend (`frontend/next.config.js`).
- Presigned file URLs live 10 minutes (`PRESIGNED_URL_TTL_SECONDS`).
- `/api/health` returns only `ok` / `unavailable`.
- Security events (`login_ok`, `login_failed`, `login_locked`, `rate_limited`, `chat_screened`, `chat_refused`, `user_*`, `document_uploaded`) are logged on the `security` logger with the request id. Passwords, tokens and chat text are never logged.
- Dependencies: `npm audit` is clean; backend versions are pinned in `backend/requirements.lock` (regenerate with `pip freeze` from a tested image).

## Deploying
`docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build` (copy `.env.prod.example` first). Only nginx publishes ports; Postgres and Qdrant are internal; use a private S3 bucket. Put real certificates in `TLS_CERT_DIR`.

## Known limits
- The JWT is kept in `localStorage` (readable by any script that runs on the page); the CSP and short token life reduce, not remove, that risk.
- The CSP allows inline scripts because Next.js hydration needs them; a nonce-based policy needs a middleware and dynamic rendering.
- A per-username login lockout can be used to lock a real user out for 15 minutes by someone who knows their username.
