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
        self.assembly_ai_api_key = self._get_runtime_setting("ASSEMBLY_AI_API_KEY")
        self.assembly_ai_http_timeout_seconds = int(
            os.getenv("ASSEMBLY_AI_HTTP_TIMEOUT_SECONDS", "900")
        )
        self.pexels_api_key = self._get_runtime_setting("PEXELS_API_KEY")
        self.oxylabs_username = self._get_optional_env("OXYLABS_USERNAME")
        self.oxylabs_password = self._get_optional_env("OXYLABS_PASSWORD")
        self.oxylabs_video_quality = os.getenv("OXYLABS_VIDEO_QUALITY", "720")
        if self.oxylabs_video_quality not in {"144", "360", "480", "720", "1080", "1440", "2160", "4320"}:
            self.oxylabs_video_quality = "720"
        self.oxylabs_download_timeout_seconds = int(os.getenv("OXYLABS_DOWNLOAD_TIMEOUT_SECONDS", "3600"))
        self.oxylabs_transfer_timeout_seconds = int(os.getenv("OXYLABS_TRANSFER_TIMEOUT_SECONDS", "900"))
        self.oxylabs_storage_bucket = self._get_optional_env("OXYLABS_STORAGE_BUCKET")
        self.oxylabs_storage_prefix = os.getenv("OXYLABS_STORAGE_PREFIX", "youtube")
        self.oxylabs_storage_endpoint = self._get_optional_env("OXYLABS_STORAGE_ENDPOINT")
        self.oxylabs_storage_region = os.getenv("OXYLABS_STORAGE_REGION", "us-east-1")
        self.oxylabs_storage_read_key = self._get_optional_env("OXYLABS_STORAGE_READ_KEY")
        self.oxylabs_storage_read_secret = self._get_optional_env("OXYLABS_STORAGE_READ_SECRET")
        self.oxylabs_storage_write_key = self._get_optional_env("OXYLABS_STORAGE_WRITE_KEY")
        self.oxylabs_storage_write_secret = self._get_optional_env("OXYLABS_STORAGE_WRITE_SECRET")
        self.videoscale_username = self._get_optional_env("VIDEOSCALE_USERNAME")
        self.videoscale_password = self._get_optional_env("VIDEOSCALE_PASSWORD")
        videoscale_quality = os.getenv("VIDEOSCALE_VIDEO_QUALITY", "720").strip()
        self.videoscale_video_quality = int(videoscale_quality) if videoscale_quality in {"720", "1080"} else 720
        self.videoscale_download_timeout_seconds = int(os.getenv("VIDEOSCALE_DOWNLOAD_TIMEOUT_SECONDS", "3600"))
        self.videoscale_transfer_timeout_seconds = int(os.getenv("VIDEOSCALE_TRANSFER_TIMEOUT_SECONDS", "900"))
        self.apify_api_token = self._get_runtime_setting("APIFY_API_TOKEN")
        self.youtube_download_provider = self._normalize_youtube_download_provider(
            os.getenv("YOUTUBE_DOWNLOAD_PROVIDER", "yt_dlp")
        )
        self.youtube_metadata_provider = self._normalize_youtube_metadata_provider(
            os.getenv("YOUTUBE_METADATA_PROVIDER", "yt_dlp")
        )
        self.apify_youtube_default_quality = self._normalize_apify_quality(
            os.getenv("APIFY_YOUTUBE_DEFAULT_QUALITY", "1080")
        )
        self.apify_youtube_downloader_actor = (
            os.getenv(
                "APIFY_YOUTUBE_DOWNLOADER_ACTOR",
                "epctex/youtube-video-downloader",
            )
            .strip()
            or "epctex/youtube-video-downloader"
        )
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
        self.free_plan_task_limit = int(os.getenv("FREE_PLAN_TASK_LIMIT", "10"))
        self.pro_plan_task_limit = int(os.getenv("PRO_PLAN_TASK_LIMIT", "50"))
        self.scale_plan_task_limit = int(os.getenv("SCALE_PLAN_TASK_LIMIT", "300"))
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
        self.default_processing_mode = os.getenv("DEFAULT_PROCESSING_MODE", "fast")
        self.fast_mode_max_clips = int(os.getenv("FAST_MODE_MAX_CLIPS", "4"))
        self.fast_mode_transcript_model = os.getenv(
            "FAST_MODE_TRANSCRIPT_MODEL", "universal"
        )

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
        if normalized in {"apify", "oxylabs", "videoscale"}:
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
