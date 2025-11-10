"""
Council data models for multi-LLM deliberation.
"""
from typing import List, Optional
from pydantic import BaseModel, Field
from enum import Enum


class CouncilMember(str, Enum):
    """5-model council members."""
    SONNET = "anthropic/claude-sonnet-4-20250514"
    OPUS = "anthropic/claude-opus-4-20250514"
    GPT4 = "openai/gpt-4-turbo-preview"
    GEMINI = "google/gemini-pro-1.5"
    DEEPSEEK = "deepseek/deepseek-chat"


COUNCIL_DISPLAY_NAMES = {
    CouncilMember.SONNET: "Claude Sonnet 4.5",
    CouncilMember.OPUS: "Claude Opus 4.1",
    CouncilMember.GPT4: "GPT-4 Turbo",
    CouncilMember.GEMINI: "Gemini 2.5 Pro",
    CouncilMember.DEEPSEEK: "DeepSeek Chat"
}


class ClipCandidate(BaseModel):
    """A potential clip identified by the council."""
    start_time: str = Field(description="Start timestamp MM:SS")
    end_time: str = Field(description="End timestamp MM:SS")
    duration: float = Field(description="Duration in seconds")
    title: str = Field(description="Suggested clip title")
    reasoning: str = Field(description="Why this moment is interesting")
    engagement_score: float = Field(description="Predicted engagement score 0-10", ge=0, le=10)
    category: str = Field(description="Content category (action, story, emotional, etc.)")


class ModelAnalysis(BaseModel):
    """Analysis from a single council member."""
    model_name: str
    candidates: List[ClipCandidate]
    overall_assessment: str = Field(description="High-level thoughts on the content")
    recommended_total_clips: int = Field(description="How many clips this model thinks should be generated")


class CouncilVote(BaseModel):
    """Vote on a specific clip candidate."""
    model_name: str
    clip_index: int
    vote: str = Field(description="YES or NO")
    confidence: float = Field(description="Confidence in vote 0-1", ge=0, le=1)
    reasoning: str = Field(description="Why this vote")


class CouncilDeliberation(BaseModel):
    """Results from council deliberation."""
    video_duration: float
    target_clips: int
    model_analyses: List[ModelAnalysis]
    final_candidates: List[ClipCandidate]
    consensus_level: float = Field(description="Agreement level 0-1")
