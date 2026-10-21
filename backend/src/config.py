from dotenv import load_dotenv
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
        self.assembly_ai_http_timeout_seconds = int(
            os.getenv("ASSEMBLY_AI_HTTP_TIMEOUT_SECONDS", "900")
        )
        self.assembly_ai_request_timeout_seconds = max(5, min(300, int(
            os.getenv("ASSEMBLY_AI_REQUEST_TIMEOUT_SECONDS", "60")
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
        # Empty disables the residential-proxy yt-dlp path. Nothing in the
        # download chain changes until this template is set.
        self.youtube_proxy_url = os.getenv("YOUTUBE_PROXY_URL", "").strip()
        self.youtube_proxy_timeout_seconds = self._get_int_env("YOUTUBE_PROXY_TIMEOUT_SECONDS", 300)
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

        self.max_video_duration = int(os.getenv("MAX_VIDEO_DURATION", "5400"))
        # YouTube sources can be longer on paid tiers. Uploads continue to use
        # max_video_duration regardless of plan.
        self.pro_youtube_max_video_duration = int(
            os.getenv("PRO_YOUTUBE_MAX_VIDEO_DURATION", str(self.max_video_duration))
        )
        self.scale_youtube_max_video_duration = int(
            os.getenv("SCALE_YOUTUBE_MAX_VIDEO_DURATION", "10800")
        )
        self.max_video_upload_bytes = int(
            os.getenv("MAX_VIDEO_UPLOAD_BYTES", "12000000000")
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
        self.media_url_ttl_seconds = min(3600, max(60, int(os.getenv("MEDIA_URL_TTL_SECONDS", "900"))))
        self.media_cache_ttl_seconds = max(14400, int(os.getenv("MEDIA_CACHE_TTL_SECONDS", "86400")))
        self.media_orphan_grace_seconds = max(86400, int(os.getenv("MEDIA_ORPHAN_GRACE_SECONDS", "86400")))
        self.media_upload_concurrency = max(1, int(os.getenv("MEDIA_UPLOAD_CONCURRENCY", "3")))
        self.media_upload_daily_limit = max(1, int(os.getenv("MEDIA_UPLOAD_DAILY_LIMIT", "20")))
        if self.media_storage == "s3" and not self.media_s3_bucket:
            raise ValueError("MEDIA_S3_BUCKET is required with MEDIA_STORAGE=s3")

        # Redis configuration
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.redis_password = self._get_optional_env("REDIS_PASSWORD")

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

        self.self_host = self._get_bool_env("SELF_HOST", True)
        self.monetization_enabled = not self.self_host
        self.backend_auth_secret = self._get_optional_env("BACKEND_AUTH_SECRET")
        self.allow_unsigned_backend_auth = self._get_bool_env(
            "ALLOW_UNSIGNED_BACKEND_AUTH", False
        )
        self.auth_signature_ttl_seconds = int(
            os.getenv("AUTH_SIGNATURE_TTL_SECONDS", "300")
        )
        # Monthly allowance in minutes of source video; 0 or less means unlimited.
        self.pro_plan_minute_limit = int(os.getenv("PRO_PLAN_MINUTE_LIMIT", "600"))
        self.scale_plan_minute_limit = int(os.getenv("SCALE_PLAN_MINUTE_LIMIT", "1500"))
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
        self.social_publishing_allowed_emails = frozenset(
            email.strip().lower()
            for email in os.getenv("SOCIAL_PUBLISHING_ALLOWED_EMAILS", "").split(",")
            if email.strip()
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
        # Cut between speakers on multi-person wide shots using diarization.
        self.speaker_framing = self._get_bool_env("SPEAKER_FRAMING", True)

    def max_youtube_video_duration_for_plan(
        self, plan: str | None, subscription_status: str | None
    ) -> int:
        """Return the YouTube duration cap granted by an active entitlement."""
        if (subscription_status or "").lower() not in {"active", "trialing"}:
            return self.max_video_duration

        if (plan or "").lower() == "scale":
            return self.scale_youtube_max_video_duration
        if (plan or "").lower() == "pro":
            return self.pro_youtube_max_video_duration
        return self.max_video_duration

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
        if normalized in ("whisper", "youtube_captions"):
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
