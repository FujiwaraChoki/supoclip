"""Shared media dependencies and rendering constants."""

from pathlib import Path

from typing import List, Dict, Any, Tuple, Optional

import logging

import numpy as np

from concurrent.futures import ThreadPoolExecutor

import json

import re

import uuid

import shutil

import subprocess

import tempfile

import time

import cv2

import assemblyai as aai

import httpx

import srt

from datetime import timedelta

try:
    import whisper as _whisper

    _WHISPER_AVAILABLE = True
except ImportError:  # pragma: no cover - optional transcription backend
    _whisper = None
    _WHISPER_AVAILABLE = False

from ..config import get_config

from ..clip_cleanup import DEFAULT_FILTERED_WORDS, clip_cleanup_enabled

from ..clip_source_map import (
    normalize_source_ranges,
    save_clip_source_ranges,
)

from ..caption_templates import get_template, CAPTION_TEMPLATES

from ..emoji_captions import POWER_WORDS, annotate_caption_words, normalize_token

from ..font_registry import FONTS_DIR, find_font_path, get_font_family_name

logger = logging.getLogger(__name__)

TRANSCRIPT_CACHE_SCHEMA_VERSION = 2

VALID_OUTPUT_FORMATS = {"vertical", "vertical_pan", "vertical_split", "original"}

EMOJI_FONT_NAME = "Noto Color Emoji"

CLIP_END_SENTENCE_EXTENSION_SECONDS = 3.0

CLIP_END_PADDING_SECONDS = 0.35

SENTENCE_END_RE = re.compile(r"""[.!?]["')\]}]*$""")

HOOK_TITLE_SECONDS = 4.0

HOOK_TITLE_MIN_SECONDS = 1.5

HOOK_TITLE_TOP_MARGIN_FRAC = 0.07

ANALYSIS_SEGMENT_MIN_WORDS = 8

ANALYSIS_SEGMENT_MAX_WORDS = 8

ANALYSIS_SEGMENT_MAX_DURATION_MS = 12_000

ANALYSIS_LONG_UTTERANCE_MAX_WORDS = 24

ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS = 20_000

ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS = 45_000

ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS = 80

_WHISPER_MODEL_CACHE: Dict[str, Any] = {}

FINAL_VIDEO_CRF = 19

FINAL_VIDEO_PRESET = "medium"

INTERMEDIATE_CRF = 16

OUTPUT_FPS = 30

AUDIO_BITRATE = "192k"

LOUDNORM_FILTER = "loudnorm=I=-14:TP=-1.5:LRA=11"

_EMOJI_SUPPORT_CACHE: Optional[bool] = None

FACE_PRESENCE_MIN_AREA = 0.002

FACE_RATE_WINDOW = 2.0

FACE_PRESENCE_RATE = 0.25

MIN_LAYOUT_SECONDS = 1.5

LAYOUT_SNAP_WINDOW = 0.6

KENBURNS_ZOOM_DELTA = 0.05

KENBURNS_MIN_SECONDS = 3.0

KENBURNS_SUPERSAMPLE_W = 1620

KENBURNS_SUPERSAMPLE_H = 2880
