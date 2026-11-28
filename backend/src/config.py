from dotenv import load_dotenv
import math
import os

from .runtime_settings import get_cached_setting, setting_prefers_admin

load_dotenv()

_config_override = None
LOCAL_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DOCKER_OLLAMA_BASE_URL = "http://host.docker.internal:11434/v1"


class Config:
    def __init__(self):
        self.openai_api_key = self._get_runtime_setting("OPENAI_API_KEY")
        self.openrouter_api_key = self._get_runtime_setting("OPENROUTER_API_KEY")
        self.openrouter_fallback_model = self._get_optional_env("OPENROUTER_FALLBACK_MODEL") or "openai/gpt-6-luna"
        self.anthropic_api_key = self._get_runtime_setting("ANTHROPIC_API_KEY")
        self.google_api_key = self._get_runtime_setting("GOOGLE_API_KEY")
        self.youtube_data_api_key = self._get_runtime_setting("YOUTUBE_DATA_API_KEY")
        self.ollama_base_url = self._get_runtime_setting("OLLAMA_BASE_URL")
        self.ollama_api_key = self._get_runtime_setting("OLLAMA_API_KEY")

        self.whisper_model = os.getenv("WHISPER_MODEL") or os.getenv("WHISPER_MODEL_SIZE", "base")
        self.transcription_provider = self._normalize_transcription_provider(
            os.getenv("TRANSCRIPTION_PROVIDER", "assemblyai")
        )
        # Empty means auto-detect the spoken language; otherwise an AssemblyAI
        # language code such as "es" or "en_us" forces it.
        self.transcription_language = self._normalize_transcription_language(
            os.getenv("TRANSCRIPTION_LANGUAGE")
        )
        self.llm = self._get_runtime_setting("LLM") or self._infer_default_llm()
        # Optional separate model for the cross-window candidate rerank pass.
        self.rerank_llm = self._get_optional_env("RERANK_LLM") or self.llm
        self.assembly_ai_api_key = self._get_runtime_setting("ASSEMBLY_AI_API_KEY")
        # An explicit deployment choice overrides the model for every processing mode.
        self.assembly_ai_speech_model = os.getenv("ASSEMBLY_AI_SPEECH_MODEL", "").strip()
        self.assembly_ai_http_timeout_seconds = int(
            os.getenv("ASSEMBLY_AI_HTTP_TIMEOUT_SECONDS", "900")
        )
        self.assembly_ai_request_timeout_seconds = max(5, min(300, int(
            os.getenv("ASSEMBLY_AI_REQUEST_TIMEOUT_SECONDS", "60")
        )))
        self.soniox_api_key = self._get_runtime_setting("SONIOX_API_KEY")
        self.soniox_model = os.getenv("SONIOX_MODEL", "stt-async-v6").strip() or "stt-async-v6"
        self.soniox_base_url = (
            os.getenv("SONIOX_BASE_URL", "https://api.soniox.com").strip()
            or "https://api.soniox.com"
        ).rstrip("/")
        self.soniox_http_timeout_seconds = max(30, min(7200, int(
            os.getenv("SONIOX_HTTP_TIMEOUT_SECONDS", "900")
        )))
        self.soniox_request_timeout_seconds = max(5, min(300, int(
            os.getenv("SONIOX_REQUEST_TIMEOUT_SECONDS", "60")
        )))
        self.pexels_api_key = self._get_runtime_setting("PEXELS_API_KEY")
        self.apify_api_token = self._get_runtime_setting("APIFY_API_TOKEN")
        self.rapidapi_youtube_api_key = self._get_optional_env("RAPIDAPI_YOUTUBE_API_KEY")
        self.rapidapi_youtube_timeout_seconds = int(
            os.getenv("RAPIDAPI_YOUTUBE_TIMEOUT_SECONDS", "300")
        )
        self.youtube_download_provider = self._normalize_youtube_download_provider(
            os.getenv("YOUTUBE_DOWNLOAD_PROVIDER", "yt_dlp")
        )
        self.youtube_metadata_provider = self._normalize_youtube_metadata_provider(
            os.getenv("YOUTUBE_METADATA_PROVIDER", "yt_dlp")
        )
        # Task creation waits this long for YouTube length metadata. 30s matches
        # the Twitch/Kick preflight deadline and covers yt-dlp's deno step when
        # the backend runs under amd64 emulation.
        self.youtube_preflight_timeout_seconds = self._get_positive_float_env(
            "YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS",
            30.0,
            maximum=180.0,
        )
        # Empty disables the residential-proxy yt-dlp path. Nothing in the
        # download chain changes until this template is set.
        self.youtube_proxy_url = os.getenv("YOUTUBE_PROXY_URL", "").strip()
        self.youtube_proxy_timeout_seconds = self._get_int_env("YOUTUBE_PROXY_TIMEOUT_SECONDS", 300)
        # Twitch/Kick: YOUTUBE_PROXY_TIMEOUT_SECONDS is how long a download may go
        # without receiving data. This is the shortest overall cap for one call.
        # A long VOD is allowed longer, up to an hour, while data is still arriving.
        # Do not use the idle timeout as that cap.
        self.stream_download_max_seconds = self._get_int_env("STREAM_DOWNLOAD_MAX_SECONDS", 2400)
        self.youtube_proxy_country = os.getenv("YOUTUBE_PROXY_COUNTRY", "").strip()
        self.youtube_pot_provider_url = os.getenv("YOUTUBE_POT_PROVIDER_URL", "").strip()
        self.youtube_download_max_bytes = self._get_int_env("YOUTUBE_DOWNLOAD_MAX_BYTES", 2_000_000_000)
        self.youtube_section_padding_seconds = self._get_float_env("YOUTUBE_SECTION_PADDING_SECONDS", 10.0)
        self.youtube_max_height = self._get_int_env("YOUTUBE_MAX_HEIGHT", 1080)
        self.youtube_source_cache = self._get_bool_env("YOUTUBE_SOURCE_CACHE", True)
        self.youtube_source_cache_ttl_seconds = self._get_int_env(
            "YOUTUBE_SOURCE_CACHE_TTL_SECONDS", 604800
        )
        self.twitch_download_providers = os.getenv("TWITCH_DOWNLOAD_PROVIDERS", "proxy,direct,apify_twitch").strip()
        self.kick_download_providers = os.getenv("KICK_DOWNLOAD_PROVIDERS", "proxy,direct_retry,apify_kick_hls,apify_kick_mp4").strip()
        # Live clipping (live_source.py, docs/live-clipping.md). A live part of a
        # Twitch/Kick stream reads its in-progress VOD; YouTube reads the live DVR.
        self.live_clipping_platforms = {
            item.strip().lower()
            for item in os.getenv("LIVE_CLIPPING_PLATFORMS", "youtube,twitch,kick").split(",")
            if item.strip()
        }
        # Media of an in-progress Twitch/Kick VOD tries the server's own IP before the
        # residential proxy: their CDNs serve datacenter IPs, and proxy bytes cost money.
        self.live_prefer_direct = self._get_bool_env("LIVE_PREFER_DIRECT", True)
        # Continuous clipping: one part of the stream every LIVE_FOLLOW_INTERVAL_SECONDS,
        # overlapping the previous part by LIVE_FOLLOW_OVERLAP_SECONDS. A session stops
        # after LIVE_FOLLOW_MAX_HOURS of stream, or when the account runs out of minutes.
        self.live_follow_interval_seconds = max(
            600, self._get_int_env("LIVE_FOLLOW_INTERVAL_SECONDS", 1800)
        )
        self.live_follow_overlap_seconds = max(
            0, min(300, self._get_int_env("LIVE_FOLLOW_OVERLAP_SECONDS", 90))
        )
        self.live_follow_max_hours = max(1, self._get_int_env("LIVE_FOLLOW_MAX_HOURS", 12))
        self.apify_twitch_actor = os.getenv("APIFY_TWITCH_ACTOR", "abotapi/twitch-scraper").strip()
        self.apify_kick_hls_actor = os.getenv("APIFY_KICK_HLS_ACTOR", "hipersoft/kick-downloader").strip()
        self.apify_kick_mp4_actor = os.getenv("APIFY_KICK_MP4_ACTOR", "parsebird/kick-video-downloader").strip()
        self.apify_stream_enabled = self._get_bool_env("APIFY_STREAM_PROVIDERS", True)
        self.apify_stream_timeout_seconds = self._get_int_env("APIFY_STREAM_TIMEOUT_SECONDS", 300)
        self.apify_kick_max_video_size_mb = self._get_int_env("APIFY_KICK_MAX_VIDEO_SIZE_MB", 2000)
        self.apify_youtube_default_quality = self._normalize_apify_quality(
            os.getenv("APIFY_YOUTUBE_DEFAULT_QUALITY", "1080")
        )
        self.apify_youtube_downloader_actor = os.getenv(
            "APIFY_YOUTUBE_DOWNLOADER_ACTOR", ""
        ).strip()
        self.apify_run_timeout_seconds = int(
            os.getenv("APIFY_RUN_TIMEOUT_SECONDS", "3600")
        )
        # Long videos at >1080p don't finish within the Apify run timeout.
        self.apify_long_video_seconds = int(
            os.getenv("APIFY_LONG_VIDEO_SECONDS", "1800")
        )

        # Video duration limits apply only in hosted mode (SELF_HOST=false).
        # Self-hosted instances are not limited by video length.
        self.max_video_duration = int(os.getenv("MAX_VIDEO_DURATION", "5400"))
        # Per-video length cap for paid tiers, for every source (YouTube,
        # Twitch, Kick and uploads). The names predate uploads following it.
        self.pro_youtube_max_video_duration = int(
            os.getenv("PRO_YOUTUBE_MAX_VIDEO_DURATION", "7200")
        )
        self.scale_youtube_max_video_duration = int(
            os.getenv("SCALE_YOUTUBE_MAX_VIDEO_DURATION", "10800")
        )
        self.team_youtube_max_video_duration = int(
            os.getenv("TEAM_YOUTUBE_MAX_VIDEO_DURATION", "10800")
        )
        # Upload size cap without a paid plan, for self-hosted installs, and
        # for the API-spooled `/upload` route (it writes to the API's own disk).
        self.max_video_upload_bytes = int(
            os.getenv("MAX_VIDEO_UPLOAD_BYTES", "4000000000")
        )
        # Paid plans upload straight to the bucket. Each cap fits a plan's
        # longest video at about 66 Mbps, above a 4K60 phone or OBS recording,
        # so the length limit is the one people meet. Sources above 1080p are
        # scaled down before processing.
        self.pro_max_video_upload_bytes = int(
            os.getenv("PRO_MAX_VIDEO_UPLOAD_BYTES", "60000000000")
        )
        self.scale_max_video_upload_bytes = int(
            os.getenv("SCALE_MAX_VIDEO_UPLOAD_BYTES", "90000000000")
        )
        self.team_max_video_upload_bytes = int(
            os.getenv("TEAM_MAX_VIDEO_UPLOAD_BYTES", "90000000000")
        )
        self.output_dir = os.getenv("OUTPUT_DIR", "outputs")

        self.max_clips = int(os.getenv("MAX_CLIPS", "10"))
        self.clip_duration = int(os.getenv("CLIP_DURATION", "30"))  # seconds

        self.temp_dir = os.getenv("TEMP_DIR", "temp")
        self.media_storage = os.getenv("MEDIA_STORAGE", "local").strip().lower()
        if self.media_storage not in {"local", "s3"}:
            raise ValueError("MEDIA_STORAGE must be local or s3")
        self.media_s3_bucket = self._get_optional_env("MEDIA_S3_BUCKET")
        self.media_s3_prefix = os.getenv("MEDIA_S3_PREFIX", "media").strip().strip("/") or "media"
        self.media_s3_region = os.getenv("MEDIA_S3_REGION") or os.getenv("AWS_REGION", "eu-north-1")
        self.media_s3_endpoint = self._get_optional_env("MEDIA_S3_ENDPOINT")
        self.media_s3_access_key = self._get_optional_env("MEDIA_S3_ACCESS_KEY_ID")
        self.media_s3_secret_key = self._get_optional_env("MEDIA_S3_SECRET_ACCESS_KEY")
        self.media_s3_session_token = self._get_optional_env("MEDIA_S3_SESSION_TOKEN")
        self.media_fallback_bucket = self._get_optional_env("MEDIA_FALLBACK_BUCKET")
        self.media_fallback_endpoint = self._get_optional_env("MEDIA_FALLBACK_ENDPOINT")
        self.media_fallback_access_key = self._get_optional_env("MEDIA_FALLBACK_ACCESS_KEY_ID")
        self.media_fallback_secret_key = self._get_optional_env("MEDIA_FALLBACK_SECRET_ACCESS_KEY")
        self.media_fallback_region = os.getenv("MEDIA_FALLBACK_REGION", "auto")
        fallback_values = (self.media_fallback_bucket, self.media_fallback_endpoint,
                           self.media_fallback_access_key, self.media_fallback_secret_key)
        if any(fallback_values) and not all(fallback_values):
            raise ValueError("All MEDIA_FALLBACK bucket, endpoint and credentials are required")
        self.media_fallback_enabled = all(fallback_values)
        self.media_failover_enabled = self._get_bool_env("MEDIA_FAILOVER_ENABLED", False)
        if self.media_failover_enabled and not self.media_fallback_enabled:
            raise ValueError("MEDIA_FAILOVER_ENABLED requires MEDIA_FALLBACK configuration")
        self.media_preferred_store = os.getenv("MEDIA_PREFERRED_STORE", "primary").strip().lower()
        if self.media_preferred_store not in {"primary", "fallback"}:
            raise ValueError("MEDIA_PREFERRED_STORE must be primary or fallback")
        if self.media_preferred_store == "fallback" and not self.media_failover_enabled:
            raise ValueError("MEDIA_PREFERRED_STORE=fallback requires MEDIA_FAILOVER_ENABLED")
        self.media_url_ttl_seconds = min(3600, max(60, int(os.getenv("MEDIA_URL_TTL_SECONDS", "900"))))
        self.media_cache_ttl_seconds = max(14400, int(os.getenv("MEDIA_CACHE_TTL_SECONDS", "86400")))
        self.media_orphan_grace_seconds = max(86400, int(os.getenv("MEDIA_ORPHAN_GRACE_SECONDS", "86400")))
        self.media_upload_concurrency = max(1, int(os.getenv("MEDIA_UPLOAD_CONCURRENCY", "3")))
        self.media_upload_daily_limit = max(1, int(os.getenv("MEDIA_UPLOAD_DAILY_LIMIT", "20")))
        # A multipart upload with no activity for this long was abandoned (a
        # closed tab never sends its abort). It stops holding a concurrency
        # slot, and the cleanup worker aborts it. Finished parts count as
        # activity, so a slow upload that keeps going is never cut off.
        self.media_upload_stale_seconds = max(600, int(os.getenv("MEDIA_UPLOAD_STALE_SECONDS", "10800")))
        if self.media_storage == "s3" and not self.media_s3_bucket:
            raise ValueError("MEDIA_S3_BUCKET is required with MEDIA_STORAGE=s3")

        # Redis configuration
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.redis_password = self._get_optional_env("REDIS_PASSWORD")
        # arq's own defaults (1s, no retry while running) crash a worker when a
        # loaded host is slow to open a connection; see workers/redis_settings.py.
        self.redis_connect_timeout_seconds = max(
            1, self._get_int_env("REDIS_CONNECT_TIMEOUT_SECONDS", 10)
        )
        self.redis_connect_retries = max(
            0, self._get_int_env("REDIS_CONNECT_RETRIES", 5)
        )
        # A reply slower than this means the connection is dead (e.g. Redis
        # restarted behind a proxy); arq connections then reconnect and retry.
        self.redis_socket_timeout_seconds = max(
            1, self._get_int_env("REDIS_SOCKET_TIMEOUT_SECONDS", 30)
        )

        # Fail-safe: queued tasks should not stay queued forever
        self.queued_task_timeout_seconds = int(
            os.getenv("QUEUED_TASK_TIMEOUT_SECONDS", "180")
        )
        # Fail-safe: processing tasks whose worker died mid-run should not stay
        # "processing" forever. Default comfortably exceeds arq's job_timeout so
        # a legitimately long job is never falsely swept into "error".
        self.processing_task_timeout_seconds = int(
            os.getenv("PROCESSING_TASK_TIMEOUT_SECONDS", "14400")
        )
        # One account can hold at most this many video renders at once. The
        # worker has a handful of CPU slots, and one render can run for hours,
        # so the default is 1: a second video from the same account waits
        # instead of taking another slot. Raise it (for example to 2) to let
        # an account render two videos at the same time.
        self.video_job_user_concurrency = max(
            1, self._get_int_env("VIDEO_JOB_USER_CONCURRENCY", 1)
        )
        self.video_job_team_concurrency = max(
            1, self._get_int_env("VIDEO_JOB_TEAM_CONCURRENCY", 3)
        )
        # How many clips of one task render concurrently on a worker. Keeping this
        # modest (default 2) cuts per-task wait time without oversubscribing multi-job workers.
        self.clip_render_concurrency = min(
            8, max(1, self._get_int_env("CLIP_RENDER_CONCURRENCY", 2))
        )
        self.video_job_defer_seconds = min(
            300, max(5, self._get_int_env("VIDEO_JOB_DEFER_SECONDS", 20))
        )
        # How long a worker on another machine waits for an upload to be
        # copied from the API's disk to object storage before failing the task.
        self.upload_copy_wait_seconds = max(
            60, self._get_int_env("UPLOAD_COPY_WAIT_SECONDS", 1800)
        )
        # Pickup delay (enqueue until a worker starts the job) that raises a
        # PostHog exception. Rate-limited so a backed-up queue pages once.
        self.queue_delay_alert_seconds = max(
            1, self._get_int_env("QUEUE_DELAY_ALERT_SECONDS", 600)
        )
        self.queue_delay_alert_interval_seconds = max(
            1, self._get_int_env("QUEUE_DELAY_ALERT_INTERVAL_SECONDS", 900)
        )

        self.self_host = self._get_bool_env("SELF_HOST", True)
        self.monetization_enabled = not self.self_host
        self.backend_auth_secret = self._get_optional_env("BACKEND_AUTH_SECRET")
        # Shared with the MCP server; enables its OAuth endpoints (/internal/mcp-oauth).
        self.mcp_oauth_secret = self._get_optional_env("MCP_OAUTH_SECRET")
        self.allow_unsigned_backend_auth = self._get_bool_env(
            "ALLOW_UNSIGNED_BACKEND_AUTH", False
        )
        self.auth_signature_ttl_seconds = int(
            os.getenv("AUTH_SIGNATURE_TTL_SECONDS", "300")
        )
        # Monthly allowance in minutes of source video; 0 or less means unlimited.
        self.pro_plan_minute_limit = int(os.getenv("PRO_PLAN_MINUTE_LIMIT", "600"))
        self.scale_plan_minute_limit = int(os.getenv("SCALE_PLAN_MINUTE_LIMIT", "1500"))
        self.team_included_seats = max(1, self._get_int_env("TEAM_PLAN_INCLUDED_SEATS", 3))
        self.team_plan_minute_limit = int(os.getenv("TEAM_PLAN_MINUTE_LIMIT", "3600"))
        self.team_extra_seat_minute_limit = max(
            0, self._get_int_env("TEAM_EXTRA_SEAT_MINUTE_LIMIT", 900)
        )
        self.team_plan_ai_edit_limit = max(
            0, self._get_int_env("TEAM_PLAN_AI_EDIT_LIMIT", 300)
        )
        self.cors_origins = self._get_csv_env(
            "CORS_ORIGINS",
            [
                "http://localhost:3107",
                "http://sp.localhost:3107",
            ],
        )
        self.aws_region = self._get_optional_env("AWS_REGION")
        self.aws_access_key_id = self._get_optional_env("AWS_ACCESS_KEY_ID")
        self.aws_secret_access_key = self._get_optional_env("AWS_SECRET_ACCESS_KEY")
        self.ses_from_email = os.getenv(
            "SES_FROM_EMAIL", "SupoClip <onboarding@example.com>"
        )
        self.app_base_url = (
            self._get_optional_env("NEXT_PUBLIC_APP_URL") or "http://localhost:3107"
        ).rstrip("/")
        self.discord_feedback_webhook_url = self._get_optional_env("DISCORD_FEEDBACK_WEBHOOK_URL")
        self.discord_sales_webhook_url = self._get_optional_env("DISCORD_SALES_WEBHOOK_URL")
        # Hermes assistant (operator chat) route for every new submission, signed with Hermes' V2
        # scheme. Server-configured: both set adds a read-only "hermes" webhook (docs/feedback.md).
        self.hermes_feedback_webhook_url = self._get_optional_env("HERMES_FEEDBACK_WEBHOOK_URL")
        self.hermes_feedback_webhook_secret = self._get_optional_env("HERMES_FEEDBACK_WEBHOOK_SECRET")

        # Cancellation reviews (docs/cancellation-reviews.md). Stripe signs each account's
        # endpoint with its own secret: "acct_A:whsec_...,acct_B:whsec_...". The legacy account
        # id resolves users whose stripe_account_id is empty.
        self.stripe_cancellation_webhook_secrets = self._get_optional_env("STRIPE_CANCELLATION_WEBHOOK_SECRETS")
        self.stripe_source_account_id = self._get_optional_env("STRIPE_SOURCE_ACCOUNT_ID")
        self.cancellation_reviews_enabled = self._get_bool_env("CANCELLATION_REVIEWS_ENABLED", False)
        self.cancellation_review_settle_seconds = max(
            60, self._get_int_env("CANCELLATION_REVIEW_SETTLE_SECONDS", 600)
        )
        # Signed chat webhook that receives the finished reports (HMAC V2, see delivery.py).
        self.cancellation_report_webhook_url = self._get_optional_env("CANCELLATION_REPORT_WEBHOOK_URL")
        self.cancellation_report_webhook_secret = self._get_optional_env("CANCELLATION_REPORT_WEBHOOK_SECRET")

        # Social publishing. Each provider needs an OAuth app registered by the
        # operator; users then connect their own accounts through that app.
        self.youtube_oauth_client_id = self._get_optional_env(
            "YOUTUBE_OAUTH_CLIENT_ID"
        ) or self._get_optional_env("GOOGLE_OAUTH_CLIENT_ID")
        self.youtube_oauth_client_secret = self._get_optional_env(
            "YOUTUBE_OAUTH_CLIENT_SECRET"
        ) or self._get_optional_env("GOOGLE_OAUTH_CLIENT_SECRET")
        self.tiktok_client_key = self._get_optional_env("TIKTOK_CLIENT_KEY")
        self.tiktok_client_secret = self._get_optional_env("TIKTOK_CLIENT_SECRET")
        # Until TikTok audits the app, Direct Post only accepts SELF_ONLY posts
        # from accounts set to private. Set true only after the audit passes.
        self.tiktok_app_audited = self._get_bool_env("TIKTOK_APP_AUDITED", False)
        self.instagram_app_id = self._get_optional_env(
            "INSTAGRAM_APP_ID"
        ) or self._get_optional_env("META_APP_ID")
        self.instagram_app_secret = self._get_optional_env(
            "INSTAGRAM_APP_SECRET"
        ) or self._get_optional_env("META_APP_SECRET")
        # OAuth callbacks and public media fetches (Instagram pulls the video
        # from a URL) both go through the frontend, so they default to the
        # public app URL.
        self.social_oauth_redirect_base_url = (
            self._get_optional_env("SOCIAL_OAUTH_REDIRECT_BASE_URL")
            or self.app_base_url
        ).rstrip("/")
        self.social_public_media_base_url = (
            self._get_optional_env("SOCIAL_PUBLIC_MEDIA_BASE_URL")
            or self.app_base_url
        ).rstrip("/")
        self.social_metrics_refresh_hours = float(
            os.getenv("SOCIAL_METRICS_REFRESH_HOURS", "6")
        )
        self.social_metrics_max_post_age_days = int(
            os.getenv("SOCIAL_METRICS_MAX_POST_AGE_DAYS", "60")
        )
        self.social_performance_min_posts = int(
            os.getenv("SOCIAL_PERFORMANCE_MIN_POSTS", "3")
        )
        self.social_publish_max_attempts = int(
            os.getenv("SOCIAL_PUBLISH_MAX_ATTEMPTS", "3")
        )
        # Who may use social publishing: "off" (default; nav and pages stay
        # hidden), "admins" (admins plus the allowlist, for a gated rollout)
        # or "all".
        self.social_publishing_access = (
            os.getenv("SOCIAL_PUBLISHING_ACCESS", "off").strip().lower() or "off"
        )
        if self.social_publishing_access not in {"all", "admins", "off"}:
            raise ValueError("SOCIAL_PUBLISHING_ACCESS must be all, admins or off")
        # Platform rollout is separate from credentials and user access. An
        # explicitly empty list disables every platform; existing self-hosted
        # installations retain all platforms unless they set this variable.
        self.social_publishing_providers = frozenset(
            provider.strip().lower()
            for provider in os.getenv(
                "SOCIAL_PUBLISHING_PROVIDERS", "youtube,tiktok,instagram"
            ).split(",")
            if provider.strip()
        )
        if self.social_publishing_providers - {"youtube", "tiktok", "instagram"}:
            raise ValueError(
                "SOCIAL_PUBLISHING_PROVIDERS may only include youtube, tiktok, instagram"
            )
        self.social_publishing_allowed_emails = frozenset(
            email.strip().lower()
            for email in os.getenv("SOCIAL_PUBLISHING_ALLOWED_EMAILS", "").split(",")
            if email.strip()
        )
        # Who may use workflows (series, topic, recap, trailer) and creator
        # instructions: "admins" (default; admins plus the allowlist, also
        # covers deploys where an old worker could take a new task), "all" or
        # "off". Self-hosters set "all".
        self.workflows_access = (os.getenv("WORKFLOWS_ACCESS") or "admins").strip().lower()
        if self.workflows_access not in {"all", "admins", "off"}:
            self.workflows_access = "admins"
        self.workflows_allowed_emails = frozenset(
            email.strip().lower()
            for email in os.getenv("WORKFLOWS_ALLOWED_EMAILS", "").split(",")
            if email.strip()
        )
        # With access "admins", these workflows are open to every user too, so
        # they can launch one at a time. Ignored by "off" and "all".
        self.workflows_public = frozenset(
            name.strip().lower()
            for name in os.getenv("WORKFLOWS_PUBLIC", "").split(",")
            if name.strip()
        )
        self.workflow_topic_max_clips = max(1, self._get_int_env("WORKFLOW_TOPIC_MAX_CLIPS", 15))
        self.workflow_plan_timeout_seconds = self._get_positive_float_env(
            "WORKFLOW_PLAN_TIMEOUT_SECONDS", 1800.0, maximum=6 * 60 * 60
        )
        # Word budget for sending the whole transcript in one outline call.
        # None uses the provider default.
        self.workflow_full_context_max_words = (
            max(0, self._get_int_env("WORKFLOW_FULL_CONTEXT_MAX_WORDS", 0)) or None
        )
        self.default_processing_mode = os.getenv("DEFAULT_PROCESSING_MODE", "fast")
        self.fast_mode_max_clips = int(os.getenv("FAST_MODE_MAX_CLIPS", "10"))
        self.fast_mode_transcript_model = os.getenv(
            "FAST_MODE_TRANSCRIPT_MODEL", "universal-3-5-pro"
        )
        # Replay a later punchline before the clip. Off: the same line was
        # playing twice (teaser, then again inside the clip) and the opening
        # has to stand on its own. Set HOOK_FIRST_TEASER=true to opt back in.
        self.hook_first_teaser = self._get_bool_env("HOOK_FIRST_TEASER", False)
        # After the moments are picked, one small model call checks where each clip opens and closes
        # (an answer without its question, "he" with nobody named, a setup without its payoff) and may move
        # an edge by a few sentences. Code validates every move. Off keeps the rule-based edges only.
        self.clip_edge_review = self._get_bool_env("CLIP_EDGE_REVIEW", True)
        # The model for that check. A few hundred tokens per clip, so the strongest model is cheap here and
        # it is the one that decides whether a clip stands on its own: Claude Sonnet when an Anthropic key is
        # set, else the rerank model, else LLM.
        self.clip_edge_llm = self._get_optional_env("CLIP_EDGE_LLM") or (
            "anthropic:claude-sonnet-5-5" if self.anthropic_api_key else None
        )
        # The local speech recogniser listens to the few seconds around each cut and corrects word times and
        # sentence ends the transcript has wrong there (media/edge_listen.py). auto: only when the model
        # file is already on the machine (the image bakes it in); on: load it, downloading if needed; off.
        self.clip_edge_listen = (os.getenv("CLIP_EDGE_LISTEN") or "auto").strip().lower()
        self.clip_edge_listen_model = (os.getenv("CLIP_EDGE_LISTEN_MODEL") or "small").strip()
        # Cut between speakers on multi-person wide shots using diarization.
        self.speaker_framing = self._get_bool_env("SPEAKER_FRAMING", True)

    def max_youtube_video_duration_for_plan(
        self,
        plan: str | None,
        subscription_status: str | None,
        subscription_provider: str | None = None,
    ) -> int:
        """Return the per-video duration cap (any source) granted by an active entitlement."""
        return self._plan_limit(
            plan, subscription_status, subscription_provider,
            default=self.max_video_duration,
            pro=self.pro_youtube_max_video_duration,
            scale=self.scale_youtube_max_video_duration,
            team=self.team_youtube_max_video_duration,
        )

    def max_upload_bytes_for_plan(
        self,
        plan: str | None,
        subscription_status: str | None,
        subscription_provider: str | None = None,
    ) -> int:
        """Return the direct-upload size cap granted by an active entitlement."""
        return self._plan_limit(
            plan, subscription_status, subscription_provider,
            default=self.max_video_upload_bytes,
            pro=self.pro_max_video_upload_bytes,
            scale=self.scale_max_video_upload_bytes,
            team=self.team_max_video_upload_bytes,
        )

    @property
    def upload_bytes_ceiling(self) -> int:
        """Largest upload any plan allows; storage refuses anything bigger."""
        return max(
            self.max_video_upload_bytes,
            self.pro_max_video_upload_bytes,
            self.scale_max_video_upload_bytes,
            self.team_max_video_upload_bytes,
        )

    @staticmethod
    def _plan_limit(
        plan: str | None,
        subscription_status: str | None,
        subscription_provider: str | None,
        *,
        default: int,
        pro: int,
        scale: int,
        team: int,
    ) -> int:
        status = (subscription_status or "").lower()
        provider = (subscription_provider or "").lower()
        # past_due keeps the cap only while Stripe is still retrying the invoice.
        entitled = status in {"active", "trialing"} or (
            status == "past_due" and provider == "stripe"
        )
        if not entitled:
            return default
        return {"team": team, "scale": scale, "pro": pro}.get((plan or "").lower(), default)

    def team_minute_limit_for_seats(self, seat_limit: int | None) -> int:
        """Pooled monthly minute allowance for a Team plan with `seat_limit` seats."""
        if self.team_plan_minute_limit <= 0:
            return 0
        seats = max(int(seat_limit or self.team_included_seats), self.team_included_seats)
        extra_seats = max(seats - self.team_included_seats, 0)
        return self.team_plan_minute_limit + extra_seats * self.team_extra_seat_minute_limit

    @staticmethod
    def _get_optional_env(name: str):
        value = os.getenv(name)
        if value is None:
            return None

        normalized = value.strip()
        return normalized or None

    @classmethod
    def _get_runtime_setting(cls, name: str):
        env_value = cls._get_optional_env(name)
        admin_value = get_cached_setting(name)
        if admin_value and setting_prefers_admin(name):
            return admin_value
        return env_value or admin_value

    def as_runtime_settings(self) -> dict[str, str | None]:
        return {
            "ASSEMBLY_AI_API_KEY": self.assembly_ai_api_key,
            "SONIOX_API_KEY": self.soniox_api_key,
            "LLM": self.llm,
            "OPENAI_API_KEY": self.openai_api_key,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "GOOGLE_API_KEY": self.google_api_key,
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "OLLAMA_BASE_URL": self.ollama_base_url,
            "OLLAMA_API_KEY": self.ollama_api_key,
            "YOUTUBE_DATA_API_KEY": self.youtube_data_api_key,
            "APIFY_API_TOKEN": self.apify_api_token,
            "PEXELS_API_KEY": self.pexels_api_key,
        }

    @staticmethod
    def _get_int_env(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _get_float_env(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _get_positive_float_env(cls, name: str, default: float, *, maximum: float) -> float:
        """Parse a positive float. Blank or invalid values use ``default``.

        Values above ``maximum`` are capped so a typo cannot hold a request open.
        """
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(value) or value <= 0:
            return default
        return min(value, maximum)

    @staticmethod
    def _get_bool_env(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _get_csv_env(name: str, default: list[str]) -> list[str]:
        value = os.getenv(name)
        if not value:
            return default
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _normalize_apify_quality(value: str | None) -> str:
        normalized = (value or "").strip()
        if normalized in {"360", "480", "720", "1080", "1440", "2160"}:
            return normalized
        return "1080"

    @staticmethod
    def _normalize_transcription_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        if normalized in ("soniox", "whisper", "youtube_captions"):
            return normalized
        return "assemblyai"

    @staticmethod
    def _normalize_transcription_language(value: str | None) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        return "" if normalized in ("", "auto") else normalized

    @staticmethod
    def _normalize_youtube_metadata_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        if normalized == "youtube_data_api":
            return "youtube_data_api"
        return "yt_dlp"

    @staticmethod
    def _normalize_youtube_download_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        if normalized in {"apify", "rapidapi"}:
            return normalized
        return "yt_dlp"

    def resolve_youtube_data_api_key(self) -> str | None:
        return self.youtube_data_api_key or self.google_api_key

    def resolve_ollama_base_url(self) -> str:
        return self.ollama_base_url or self._default_ollama_base_url()

    @staticmethod
    def _default_ollama_base_url() -> str:
        if os.path.exists("/.dockerenv"):
            return DOCKER_OLLAMA_BASE_URL
        return LOCAL_OLLAMA_BASE_URL

    def _infer_default_llm(self) -> str:
        """
        Infer a usable default model based on whichever API key is present.
        Falls back to Google for backward compatibility.
        """
        if self.google_api_key:
            return "google-gla:gemini-3-flash-preview"
        if self.openai_api_key:
            return "openai:gpt-5.2"
        if self.anthropic_api_key:
            return "anthropic:claude-4-sonnet"
        if self.openrouter_api_key:
            return "openrouter:anthropic/claude-sonnet-5.5"
        return "google-gla:gemini-3-flash-preview"


def get_config() -> Config:
    override = _config_override
    if override is not None:
        return override
    return Config()


def set_config_override(config: Config | None) -> None:
    global _config_override
    _config_override = config
