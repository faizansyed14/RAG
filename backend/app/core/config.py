"""
Central settings. Every env var the app needs is declared here; no secrets
hardcoded. Every field name here is an env var, matched case-insensitively
(RAG_INDEX_MODEL -> rag_index_model). The five model_* fields have no
Python-side default -- .env.dev/.env.prod is the only source of truth for
which model runs each role, and the app refuses to start rather than
silently using a model you didn't choose. See docs/MODELS.md.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "dev"
    auth_secret: str = "dev-only-not-for-prod"

    # Bootstrap admin: seeded into the users table on first boot only (see
    # core/bootstrap.py). After that, accounts are managed from the Users page.
    admin_username: str = "admin"
    admin_password: str = "admin"

    # --- Web / security ---
    # Comma-separated browser origins allowed to call the API (CORS).
    cors_origins: str = "http://localhost:3000"
    # How many reverse proxies sit in front of the API. 0 = none: X-Forwarded-For is
    # ignored (it's client-controlled). With N > 0 the Nth entry from the right is used.
    trusted_proxy_hops: int = 0
    access_token_ttl_minutes: int = 60 * 24
    max_upload_mb: int = 50
    presigned_url_ttl_seconds: int = 600

    # --- Rate limits (see core/ratelimit.py). limit / window in seconds. ---
    rl_login_per_ip: int = 10            # login attempts per IP per minute
    rl_login_max_failures: int = 5       # failed logins per username before a lockout
    rl_login_ip_max_failures: int = 15   # failed logins per IP before a lockout
    rl_login_lockout_seconds: int = 900
    rl_chat_per_minute: int = 6          # chat requests per user per minute (on top of credits)
    rl_upload_per_hour: int = 20
    rl_admin_write_per_minute: int = 60
    rl_me_per_minute: int = 60
    rl_global_per_minute: int = 300      # coarse per-IP backstop, in-process
    chat_lease_seconds: int = 300        # max time a single chat stream may hold the user's lease

    # --- Per-user chat quota (see core/quota.py) ---
    chat_credit_cost: int = 10          # credits charged per chat message
    chat_block_seconds: int = 3600      # block length once a user's allowance is spent
    default_credit_limit: int = 100     # allowance for new users (100 / 10 = 10 messages)

    # --- Postgres (metadata: documents, diagram pages) ---
    postgres_dsn: str = "postgresql://rag:change_me@localhost:5432/rag_engine"

    # --- Qdrant (diagram embeddings only) ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None

    # --- Object storage (original files, page renders, diagram crops) ---
    s3_endpoint_url: str | None = None  # unset -> real AWS S3; the backend's own MinIO endpoint in dev
    # Only needed in dev: presigned URLs are handed to the *browser*, which
    # can't resolve the Docker-internal "minio" hostname s3_endpoint_url
    # uses -- see core/object_store.py's module docstring. Unset in prod
    # (real S3 is publicly reachable, so s3_endpoint_url's None already works).
    s3_public_endpoint_url: str | None = None
    s3_bucket: str = "rag-engine-dev"
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # --- Model gateway: OpenRouter, OpenAI-compatible ---
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Tree engine local storage ---
    # Where the tree engine (app/rag_core/, driven by app/rag_service.py)
    # persists its per-document tree/page data on disk -- the actual source
    # of truth for every rag_doc_id this app issues. Must be a persistent
    # volume in Docker.
    rag_storage_path: str = "./.rag-data"

    # --- Pinned models ---
    # No defaults here on purpose: .env.dev (or .env.prod) is the single
    # source of truth for which model runs each role. The app refuses to
    # start rather than silently falling back to a model choice you can't
    # see -- set all five in .env.dev before running. See docs/MODELS.md
    # for exactly which call site each one drives and how many calls to
    # expect per document/query.
    rag_index_model: str          # tree build + node summaries (app/rag_service.py)
    rag_chat_model: str           # tree-search reasoning agent (app/rag_service.py)
    model_vision: str             # diagram OCR/caption/description + citation verification
    model_embedding: str          # diagram vector embeddings (Qdrant)
    model_embedding_dimension: int  # must match model_embedding's real output size


    @property
    def is_prod(self) -> bool:
        return self.environment.lower() in ("prod", "production")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def production_problems(self) -> list[str]:
        """Things that must not be true when ENVIRONMENT=prod."""
        problems: list[str] = []
        if self.auth_secret == "dev-only-not-for-prod" or len(self.auth_secret) < 32:
            problems.append("AUTH_SECRET must be a random string of at least 32 characters")
        if self.admin_password in ("admin", "change-me", "change_me", "password") or len(self.admin_password) < 12:
            problems.append("ADMIN_PASSWORD must be at least 12 characters and not a default")
        if "change_me" in self.postgres_dsn:
            problems.append("POSTGRES_DSN still uses the change_me password")
        if not self.s3_access_key or self.s3_secret_key in ("", "change_me"):
            problems.append("S3_ACCESS_KEY/S3_SECRET_KEY must be set to real credentials")
        if not self.cors_origin_list or any("localhost" in o for o in self.cors_origin_list):
            problems.append("CORS_ORIGINS must list your real https origin(s), not localhost")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
