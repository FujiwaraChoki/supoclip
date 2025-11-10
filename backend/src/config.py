from dotenv import load_dotenv
import os

load_dotenv()

class Config:
    def __init__(self):
        self.whisper_model = os.getenv("WHISPER_MODEL", "base")
        self.llm = os.getenv("LLM_MODEL", "google-gla:gemini-2.5-flash-lite")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        self.assembly_ai_api_key = os.getenv("ASSEMBLY_AI_API_KEY")

        # OpenRouter for multi-LLM council
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.openrouter_referer = os.getenv("OPENROUTER_REFERER", "http://localhost:3000")

        self.max_video_duration = int(os.getenv("MAX_VIDEO_DURATION", "3600"))
        self.output_dir = os.getenv("OUTPUT_DIR", "outputs")

        self.max_clips = int(os.getenv("MAX_CLIPS", "10"))
        self.clip_duration = int(os.getenv("CLIP_DURATION", "30"))  # seconds

        self.temp_dir = os.getenv("TEMP_DIR", "temp")

        # Redis configuration
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
