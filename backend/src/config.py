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
        self.anthropic_api_key = self._get_runtime_setting("ANTHROPIC_API_KEY")
        self.google_api_key = self._get_runtime_setting("GOOGLE_API_KEY")
        self.youtube_data_api_key = self._get_runtime_setting("YOUTUBE_DATA_API_KEY")
        self.ollama_base_url = self._get_runtime_setting("OLLAMA_BASE_URL")
        self.ollama_api_key = self._get_runtime_setting("OLLAMA_API_KEY")

        self.whisper_model = os.getenv("WHISPER_MODEL", "base")
        self.llm = self._get_runtime_setting("LLM") or self._infer_default_llm()
        self.assembly_ai_api_key = self._get_runtime_setting("ASSEMBLY_AI_API_KEY")
        self.pexels_api_key = self._get_runtime_setting("PEXELS_API_KEY")
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

        self.max_video_duration = int(os.getenv("MAX_VIDEO_DURATION", "5400"))
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
                "http://localhost:3000",
                "http://sp.localhost:3000",
            ],
        )
        self.resend_api_key = self._get_optional_env("RESEND_API_KEY")
        self.resend_from_email = os.getenv(
            "RESEND_FROM_EMAIL", "SupoClip <onboarding@resend.dev>"
        )
        self.app_base_url = (
            self._get_optional_env("NEXT_PUBLIC_APP_URL") or "http://localhost:3000"
        ).rstrip("/")
        self.discord_feedback_webhook_url = self._get_optional_env("DISCORD_FEEDBACK_WEBHOOK_URL")
        self.discord_sales_webhook_url = self._get_optional_env("DISCORD_SALES_WEBHOOK_URL")
        self.default_processing_mode = os.getenv("DEFAULT_PROCESSING_MODE", "fast")
        self.fast_mode_max_clips = int(os.getenv("FAST_MODE_MAX_CLIPS", "4"))
        self.fast_mode_transcript_model = os.getenv(
            "FAST_MODE_TRANSCRIPT_MODEL", "nano"
        )

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
        if normalized in {"360", "480", "720", "1080"}:
            return normalized
        return "1080"

    @staticmethod
    def _normalize_youtube_metadata_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        if normalized == "youtube_data_api":
            return "youtube_data_api"
        return "yt_dlp"

    @staticmethod
    def _normalize_youtube_download_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        if normalized == "apify":
            return "apify"
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
        return "google-gla:gemini-3-flash-preview"


def get_config() -> Config:
    override = _config_override
    if override is not None:
        return override
    return Config()


def set_config_override(config: Config | None) -> None:
    global _config_override
    _config_override = config
