# SupoClip - Technical Notes

## Video Upload Flow

### Where Upload Starts
**Endpoint:** `POST /upload`  
**Location:** `backend/src/api/routes/media.py` (lines 281-320)

The upload route:
1. Authenticates the user via `resolve_authenticated_user_id()`
2. Receives form data with a "video" file field
3. Creates an `uploads` directory in `{TEMP_DIR}/uploads`
4. Generates a unique filename: `{uuid}{original_extension}`
5. Writes file to disk using `_write_upload_to_disk()` (streaming chunks, max 1GB)
6. Returns response with `video_path: "upload://{unique_filename}"`

### Files Participating in Upload

| File | Purpose |
|------|---------|
| `backend/src/api/routes/media.py` | Upload route, file validation, disk write |
| `backend/src/services/video_service.py` | Detects source type, resolves local paths |
| `backend/src/api/routes/tasks.py` | Task creation that consumes uploaded video |
| `backend/src/config.py` | Provides `TEMP_DIR` configuration |

### After Upload - Video Processing Chain

1. **Upload Response**
   - Client receives `{"video_path": "upload://abc123.mp4"}`

2. **Task Creation** (`POST /tasks/`)
   - Frontend sends task creation with `source.url: "upload://abc123.mp4"`
   - `TaskService.create_task_with_source()` creates database record

3. **Source Type Detection** (`VideoService.determine_source_type()`)
   - URL starting with `upload://` → classified as `source_type: "video_url"`
   - Other URLs → classified as `source_type: "youtube"`

4. **Path Resolution** (`VideoService.resolve_local_video_path()`)
   - When worker needs to process the video:
   - `upload://abc123.mp4` → extracted filename → resolves to `{TEMP_DIR}/uploads/abc123.mp4`
   - Only `upload://` prefix allowed for local sources (security: prevents `/etc/passwd` attacks)

5. **Video Processing** (Worker via ARQ)
   - Worker receives task with `url: "upload://abc123.mp4"` and `source_type: "video_url"`
   - `VideoService.download_video()` checks source type
   - For `video_url`: skips YouTube download, uses local path directly
   - Transcription, AI analysis, and clip generation proceed normally

### Key Security & Design Details

- **Path Safety**: `upload://` prefix is parsed and reconstructed to prevent directory traversal (line 98: `.name` extracts filename only)
- **File Limits**: Max 1GB per upload (`MAX_VIDEO_UPLOAD_BYTES = 1_000_000_000`)
- **Storage Location**: `{TEMP_DIR}/uploads/` (configurable via environment)
- **Unique Naming**: UUID + original extension prevents overwrites/collisions
- **Temporary Storage**: Files in `uploads/` are temporary; clip output goes to `{TEMP_DIR}/clips/`

### Configuration
- `TEMP_DIR` environment variable controls where uploads are stored (default: `/tmp`)
- Redis metadata stored for 7 days (task source settings cached: `task_source:{task_id}`)

---

## Video Processing Pipeline (Queue → Worker → FFmpeg)

### 1. Who Puts Video in the Queue

**Location:** `backend/src/api/routes/tasks.py` (lines 279-294)

After task is created in database, the API enqueues a job:
```python
job_id = await queue_adapter.enqueue_processing_job(
    "process_video_task",       # function name
    processing_mode,
    task_id,
    raw_source["url"],          # upload://abc123.mp4
    source_type,                # "video_url" or "youtube"
    user_id,
    font_family, font_size, font_color,
    caption_template, processing_mode, output_format,
    add_subtitles, cleanup_settings,
)
```

**Queue Implementation:** `backend/src/workers/job_queue.py`
- Uses **ARQ** (async Redis queue) library
- Queue name: `supoclip_tasks` (DEFAULT_QUEUE_NAME)
- Job placed in Redis at `pool.enqueue_job()` (line 62)
- Redis host/port from config (default: localhost:6379)

### 2. Who Processes the Queue (Worker)

**Worker Function:** `backend/src/workers/tasks.py` (lines 16-119)  
**Function:** `async def process_video_task(ctx, task_id, url, source_type, ...)`

**Worker Configuration:** `backend/src/workers/tasks.py` (lines 122-145)
```python
class WorkerSettings:
    functions = [process_video_task]      # Only function this worker runs
    queue_name = "supoclip_tasks"         # Listens on this queue
    max_jobs = 4                          # Process up to 4 jobs simultaneously
    job_timeout = 10800                   # 3-hour timeout per job
    max_tries = 3                         # Retry failed jobs up to 3 times
```

**Worker Startup Command:**
```bash
arq backend.src.workers.tasks.WorkerSettings
```

**Processing Flow in Worker:**
1. Receives dequeued job from Redis (ARQ pops from queue)
2. Creates `ProgressTracker(ctx["redis"], task_id)` for real-time progress via SSE
3. Calls `task_service.process_task()` with all parameters (line 80)
4. Passes callbacks:
   - `update_progress()` → updates Redis progress, frontend sees via SSE
   - `should_cancel()` → checks if user cancelled (Redis key `task_cancel:{task_id}`)
   - `clip_ready_callback()` → notifies frontend of completed clip
5. On failure: stores dead-letter in Redis (key `dead_letter:{task_id}`) for retry tracking

### 3. Who Calls FFmpeg

**Main Orchestrator:** `backend/src/services/video_service.py`
- `process_video_complete()` → coordinates transcription, AI analysis, clip generation

**Clip Rendering Chain:**
1. `create_single_clip()` (video_service.py:204) → wraps rendering in `run_in_thread()`
2. `create_clips_with_transitions()` (video_utils.py:3502) → delegates to:
3. `create_clips_from_segments()` (video_utils.py:3278) → loops segments, calls:
4. `create_optimized_clip()` (video_utils.py:3147) → **CALLS FFMPEG** ↓

**FFmpeg Calls in create_optimized_clip():**

| What | Function | Details |
|------|----------|---------|
| **Fast Path** | subprocess.run() directly (line 3189) | Stream copy, no re-encode: `ffmpeg -c copy` |
| **Source Extraction** | `render_source_ranges_ffmpeg()` (line 3216) | Extracts keep_ranges from video, creates temp source clip |
| **Reframing** | `render_reframed_clip_ffmpeg()` (line 3259) | Resizes to 1080x1920 (vertical), burns subtitles, encodes to H.264 |

**Central FFmpeg Executor:** `backend/src/video_utils.py` (line 807)
```python
def run_ffmpeg_command(command: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe and log stderr on failure."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.error("Command failed: %s\n%s", " ".join(command), result.stderr[-4000:])
    return result
```

**All FFmpeg Operations in video_utils.py:**
- Line 128: `_prepare_audio_for_transcription()` → extracts audio to MP3
- Line 821: `ffprobe_has_audio()` → checks for audio stream
- Line 840: `ffprobe_video_size()` → queries video dimensions
- Line 862: `ffprobe_duration()` → queries video duration
- Line 1000: `generate_frame_in_thread()` → extracts single frame for face detection
- Line 1095: `render_source_ranges_ffmpeg()` → extracts keep_ranges, creates intermediate clip
- Line 1192: `render_reframed_clip_ffmpeg()` → **MAIN RENDER** — applies resize, crop, subtitle burn, H.264 encode
- Line 1691: `count_scene_cuts()` → detects scene changes for smart cropping
- Line 1933: `detect_speaker_reframe_plan()` → analyzes motion for smart framing
- Line 2700: `burn_ass_subtitles_ffmpeg()` → burns .ass subtitles into video
- Line 2771: `detect_audio_peak_times()` → finds audio energy peaks
- Line 3484: `apply_transition_effect()` → xfade between clips

### Processing Timeline

```
1. upload_video()           → stores file in {TEMP_DIR}/uploads/
2. create_task()            → stores record in DB
3. enqueue_processing_job() → puts job in Redis queue
                 ↓
   [WAITING FOR WORKER]
                 ↓
4. worker.process_video_task()
   ├─ transcription (AssemblyAI API)
   ├─ AI analysis (Pydantic AI)
   └─ create_single_clip() per segment
      ├─ run_in_thread() calls:
      │  ├─ render_source_ranges_ffmpeg() [FFmpeg: extracts keep_ranges]
      │  └─ render_reframed_clip_ffmpeg() [FFmpeg: reframe + burn subtitles]
      └─ saves to {TEMP_DIR}/clips/clip_1_uuid.mp4
5. task_service.update_task_status() → "completed"
6. ProgressTracker pushes via SSE → frontend shows "Complete!"
```

### Key Implementation Details

- **Run in thread pool:** All blocking video operations wrapped in `run_in_thread()` to avoid blocking async event loop
- **Subtitle burn:** Two-pass rendering avoided: ASS captions built before encode, burned in single `render_reframed_clip_ffmpeg()` pass
- **Progress tracking:** Redis pub/sub pushes progress % and message; frontend connects via SSE to `/tasks/{id}/progress`
- **Error handling:** Failed clips logged but don't stop pipeline; task completes with remaining clips
- **Dead-letter queue:** Exhausted retries stored in Redis `dead_letter:{task_id}` for manual investigation
- **Timeout:** FFmpeg operations timeout at 900s (15 min), job overall timeout 10800s (3 hours)

---

## Clip Selection Pipeline (How Cuts Are Chosen)

### Flow Overview

```
1. Transcription → transcript (AssemblyAI)
   ↓
2. Clip Signals (optional) → audio/visual analysis hints
   ↓
3. AI Analysis → get_most_relevant_parts_by_transcript()
   ├─ LLM selects segments
   ├─ Validate & repair bounds
   ├─ Calculate virality scores
   └─ Sort by virality (primary) + relevance (secondary)
   ↓
4. Final segments → rendered as video clips
```

### Step 1: Transcription (AssemblyAI)

**Location:** `backend/src/services/video_service.py:149` — `generate_transcript()`
- Calls AssemblyAI API with word-level timestamps
- Converts to `[MM:SS - MM:SS] spoken text` format
- Result: Multi-line transcript with precise word timing

**Processing Mode Affects Model:**
- `fast` mode: Uses `fast_mode_transcript_model` (default: "nova")
- `balanced`/`quality`: Uses "best" model for accuracy

### Step 2: Clip Signals (Optional)

**Location:** `backend/src/services/video_service.py:452-463`

Before AI analysis, optional deterministic signals extracted:
```python
clip_signals = await run_in_thread(
    build_clip_signal_summary,  # Analyzes audio peaks, scene cuts, etc.
    video_path,
    transcript,
)
```

These signals are **hints only** — passed to LLM to influence ranking but don't directly select segments.

### Step 3: AI Analysis — The Core Selection Logic

**Location:** `backend/src/ai.py:662` — `get_most_relevant_parts_by_transcript()`

#### 3A. LLM Segment Selection

**Agent:** `get_transcript_agent()` (ai.py:388)
- **Model:** Configurable via `LLM` env var (e.g., `google-gla:gemini-3-flash-preview`)
- **System Prompt:** `transcript_analysis_system_prompt` (ai.py:181-246)
- **Output Type:** `TranscriptAnalysis` (Pydantic model)

**System Prompt Key Instructions:**
- "Extract and rank, not creative rewrite" — stay grounded in transcript
- Segments must be **15-60 seconds** (prefer 25-50s)
- "Complete thoughts, insights, or entertaining moments"
- Priority: "hooks, emotional moments, valuable information"
- Return valid JSON only with fields: `start_time`, `end_time`, `text`, `relevance_score`, `reasoning`, `virality`, `hook_title`

**User Prompt Built by:** `build_transcript_analysis_prompt()` (ai.py:421)
```
- Passes transcript with [MM:SS - MM:SS] timestamped spans
- Includes clip_signals if available (as hints for ranking)
- Sets selection target (e.g., "3-7 segments")
```

**LLM Output:** Returns `TranscriptAnalysis` with:
- `most_relevant_segments[]` — selected segments with timestamps and scores
- `summary` — video summary
- `key_topics[]` — main topics
- `broll_opportunities[]` — (optional) B-roll insertion points

#### 3B. Validation & Repair

**Location:** `backend/src/ai.py:686-773`

Each segment validated & repaired:

| Check | Logic | Action |
|-------|-------|--------|
| **Text content** | < 3 words? | Skip segment |
| **Timestamp equality** | start == end? | Skip segment |
| **Duration** | < 15s or > 60s? | Attempt `_repair_segment_bounds()` to snap to transcript spans |
| **Virality scores** | total ≠ sum of subscores? | Correct total_score |
| **Hook title** | Sanitize | Apply `sanitize_hook_title()` (remove emojis, hashtags) |

**Repair Logic** (`_repair_segment_bounds()`, ai.py:627):
- If LLM timestamps fall outside valid boundaries, find nearest valid transcript span
- Penalize durations outside 25-50s ideal range
- Prefer exact boundaries when available
- Fallback to nearest sentence/word boundary if needed

#### 3C. Virality Scoring

**Data Model:** `ViralityAnalysis` (ai.py:35)

Each segment scored on 4 dimensions (0-25 points each):

| Score | Meaning | Examples |
|-------|---------|----------|
| **hook_score** | Strength of opening hook (0-25) | Question, stat, surprising statement |
| **engagement_score** | Entertainment/hold attention (0-25) | Story, humor, emotional beats |
| **value_score** | Educational/informational (0-25) | Teaches, explains, reveals insight |
| **shareability_score** | Likelihood to be shared (0-25) | Relatable, provocative, useful |
| **total_score** | Sum of above (0-100) | `hook + engagement + value + shareability` |

Also includes:
- `hook_type` — "question", "statement", "statistic", "story", "contrast", or "none"
- `virality_reasoning` — LLM explanation of score

**Example:**
```
hook_score: 22        # Strong opening question
engagement_score: 20  # Story-driven narrative
value_score: 18       # Learning opportunity
shareability_score: 19 # Relatable advice
total_score: 79       # (calculated sum)
```

#### 3D. Sorting & Final Selection

**Location:** `backend/src/ai.py:775-782`

```python
# Sort by virality (primary), then relevance (secondary)
validated_segments.sort(
    key=lambda x: (
        x.virality.total_score if x.virality else 0,
        x.relevance_score,
    ),
    reverse=True,  # Highest scores first
)
```

**Clip Limit by Mode:**
- `fast` mode: Capped at `fast_mode_max_clips` (typically 3-5 clips)
- `balanced`/`quality`: No built-in cap, LLM usually returns 3-7 segments

### Step 4: Fallback (No Segments Selected)

**Location:** `backend/src/services/video_service.py:517-527`

If AI selects 0 segments, generate **fallback clip**:
```python
_build_fallback_segment(
    file_duration,      # e.g., 600 seconds
    transcript,         # Full transcript
    clip_duration,      # e.g., 45 seconds
)
```
Creates single middle-of-video clip (start at duration/4, ends at start + 45s) to ensure task produces output.

### Summary Table

| Component | File | Selection Criteria |
|-----------|------|-------------------|
| **Transcription** | video_service.py | AssemblyAI word-level timing |
| **Signals** | video_service.py | Audio peaks, scene cuts (hints only) |
| **LLM Selection** | ai.py | System prompt, content hooks, engagement |
| **Validation** | ai.py | Duration 15-60s, text length, timestamp format |
| **Repair** | ai.py | Snap out-of-bounds to transcript spans |
| **Virality Scoring** | ai.py | 4-dimensional score (hook/engagement/value/shareability) |
| **Sorting** | ai.py | Virality DESC, then relevance DESC |
| **Limit** | video_service.py | `fast_mode_max_clips` or LLM default (3-7) |

### Key Parameters in ai.py

```python
IDEAL_CLIP_MIN_SECONDS = 25        # Preferred minimum
IDEAL_CLIP_MAX_SECONDS = 50        # Preferred maximum
MIN_ACCEPTED_CLIP_SECONDS = 15     # Hard minimum (repair allowed below this)
MAX_ACCEPTED_CLIP_SECONDS = 60     # Hard maximum
HOOK_TITLE_MAX_CHARS = 64          # On-screen title limit
HOOK_TITLE_MAX_WORDS = 10          # On-screen title word limit
```

### Notes

- **Caching:** Analysis results cached by (url, source_type, processing_mode) key in DB
- **B-roll:** If enabled, `BRollOpportunity[]` objects also returned (but not used in cut selection, only for compositing)
- **Provider flexibility:** Works with any LLM via Pydantic AI (Gemini, OpenAI, Anthropic, Ollama, etc.)
- **Deterministic backup:** If LLM fails, fallback segment guaranteed

---

## Caption Generation Pipeline (How Subtitles Are Created)

### Flow Overview

```
1. get_video_transcript()
   ├─ Extract audio → MP3 (ffmpeg)
   ├─ Send to AssemblyAI API (word-level timestamps)
   └─ Cache words + utterances to .transcript_cache.json
   ↓
2. build_assemblyai_ass_subtitles()
   ├─ Load word data from cache
   ├─ Extract words in clip range
   ├─ Build .ass file (Advanced SubStation format)
   ├─ Apply template (colors, fonts, animations)
   └─ Write events (per-word karaoke or chunk-based)
   ↓
3. render_reframed_clip_ffmpeg()
   └─ FFmpeg burns .ass subtitles into video
```

### Step 1: Transcription with Word Timing

**Location:** `backend/src/video_utils.py:212` — `get_video_transcript()`

**Process:**
1. Extract audio from video to MP3 (via `_prepare_audio_for_transcription()`)
2. Submit to AssemblyAI Transcriber with config:
   - `speech_models`: model variant (fast="nova", quality="pro", default="universal-2")
   - `speaker_labels=True`: detect speaker changes
   - `punctuate=True`: add punctuation
   - `format_text=True`: clean text formatting
3. Wait for AssemblyAI to return transcript with **word-level timestamps**
4. Format to `[MM:SS - MM:SS] spoken text` (line-per-span format for LLM analysis)
5. Cache raw transcript to `{video_path}.transcript_cache.json`

**AssemblyAI Response Data:**
Each word object contains:
- `text` — the word spoken
- `start` — start time in seconds (float, e.g., 12.34)
- `end` — end time in seconds (float, e.g., 12.67)
- `confidence` — speech confidence (0-1)
- `speaker` — speaker ID if speaker_labels enabled

**Cached at:** `{video_path}.transcript_cache.json`
```json
{
  "version": "...",
  "words": [
    {"text": "hello", "start": 0.12, "end": 0.45, "confidence": 0.99, "speaker": "A"},
    ...
  ],
  "utterances": [
    {"text": "hello world", "start": 0.12, "end": 1.23, "speaker": "A", "words": [...]}
  ],
  "text": "hello world..."
}
```

### Step 2: Build ASS Subtitle File

**Location:** `backend/src/video_utils.py:1423` — `build_assemblyai_ass_subtitles()`

**Input Parameters:**
- `video_path` — source video (to load transcript cache)
- `clip_start` / `clip_end` — timestamp range for this clip (seconds)
- `video_width` / `video_height` — output dimensions
- `font_family`, `font_size`, `font_color` — user customizations
- `caption_template` — "default", "minimal", "bold", etc. (defines styles & animations)
- `hook_title` — AI-written headline (optional, burns into top safe area first 4s)
- `include_captions` — whether to add word-synced captions (vs. hook-title-only)

**Processing:**

**2A. Load Template & Colors**
```
template = get_template(caption_template)
  ├─ font_family (e.g., "TikTokSans")
  ├─ font_size (e.g., 48)
  ├─ font_color (primary, default "#FFFFFF")
  ├─ highlight_color (active word, default "#FFE000")
  ├─ emphasis_color (power words, default highlight)
  ├─ stroke_color (outline, e.g., "#000000")
  ├─ background_color (pill/box behind text)
  ├─ animation ("karaoke", "fade", "pop", "bounce", "none")
  ├─ word_pop (scale-up entrance on first word)
  └─ position_y (0.75 = 75% down the frame)
```

**2B. Extract Words in Clip Range**
```python
# Load cache
transcript_data = load_cached_transcript_data(video_path)

# Get words within [clip_start, clip_end] or [keep_ranges]
relevant_words = get_words_in_range(transcript_data, clip_start, clip_end)
# Result: List[{"text": "...", "start": 0.12, "end": 0.45, ...}]
```

**2C. Build ASS Header**

ASS (Advanced SubStation) format header:
```
[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, ...
Style: Default,{font_name},{font_px},{primary_color},...
Style: Hook,...  (for hook_title, if provided)

[Events]
Format: Layer, Start, End, Style, Name, ..., Text
Dialogue: 0,00:00:12.34,00:00:12.67,Default,,0,0,0,,{text_with_formatting}
...
```

**Color Format in ASS:** BGR hex with alpha (e.g., `&H80FFFF00` = BGRA)

**2D. Generate Caption Events**

Two animation modes:

**Karaoke Mode** (default, per-word highlight):
- Loop through words in chunks (e.g., 4 words per line)
- For each word:
  - Create **2 events**: one for each frame (active/inactive)
  - Active span: highlight color, optional box around word
  - Inactive span: primary color (or emphasis color for power words)
  - Line entrance: scale animation on first word only (pop effect)
  
Example event:
```
Dialogue: 0,00:00:12.34,00:00:12.47,Default,,0,0,0,,{\pos(540,1440)\fscx92\fscy92\t(0,140,\fscx100\fscy100)}{\fn"TikTokSans"\c&H00FFFF00}hello {\fn"TikTokSans"\c&H0000FF00}world
```
Breakdown:
- `\pos(540,1440)` — center horizontally (540), 75% down (1440 out of 1920)
- `\fscx92\fscy92\t(...)` — scale 92% → 100% over 140ms (pop entrance)
- `\c&H00FFFF00` — color (yellow highlight)
- `hello world` — text

**Chunk Mode** (fade/pop/bounce, whole line animates):
- Group words into lines (4 words per line)
- Single event per line covering all words
- Whole line fades/pops, not per-word

**2E. Hook Title (Optional)**

If `hook_title` provided (AI-written 3-9 word headline):
- Burn into top safe area (first ~4 seconds)
- Separate "Hook" style with larger font
- Fade entrance/exit: `\fad(160,240)` (fade in 160ms, out 240ms)
- Optional word-pop animation on entrance

**2F. Write ASS File**

```python
output_ass_path.write_text(header + "\n".join(all_events), encoding="utf-8")
```

Result: `.ass` file ready to burn into video

### Step 3: Burn Subtitles into Video

**Location:** `backend/src/video_utils.py:1259` — `render_reframed_clip_ffmpeg()`

FFmpeg command that resizes, crops, AND burns subtitles in one pass:

```bash
ffmpeg -i source.mp4 \
  -vf "[0]scale=1080:1920:...,subtitles=captions.ass:fonts_dir=fonts/" \
  -c:v libx264 -preset medium -crf 23 \
  -c:a aac -b:a 192k \
  -movflags +faststart \
  output.mp4
```

Key parameters:
- `subtitles=captions.ass` — load ASS file
- `fonts_dir={fonts_dir}` — where to find custom fonts (.ttf files)
- Scale/crop filter applied before subtitle burn
- Encoding: H.264 video, AAC audio

**Font Resolution:**
- System fonts: Windows/Mac/Linux default paths
- User fonts: `backend/fonts/` (bundled)
- Custom fonts: `{TEMP_DIR}/user_fonts/{user_id}/`

### Key Features

| Feature | Implementation |
|---------|-----------------|
| **Word sync** | Per-word timing from AssemblyAI |
| **Karaoke highlight** | Active word color changes per event |
| **Power word emphasis** | Detect numbers/keywords, highlight in accent color |
| **Emojis** | Contextual emojis added per-word (if template enables) |
| **Animations** | Karaoke (per-word), Fade, Pop, Bounce (whole line) |
| **Hook title** | AI-written headline, top-safe-area, fades in/out |
| **Custom fonts** | TTF/OTF support via `fonts_dir` parameter |
| **Position** | Configurable Y position (default 75% down frame) |
| **Outline + Shadow** | Scaled stroke width + drop shadow for readability |
| **Pill box** | Optional colored box around active word |
| **Glow effect** | Optional `\blur4` blur on whole subtitle line |
| **Emphasis colors** | Separate color for power words vs. regular text |

### Template System

Templates in `backend/src/caption_templates.py` define:
- Font family (TikTokSans, Inter, Montserrat, etc.)
- Font size (24-72px, scaled to video resolution)
- Colors (primary, highlight, emphasis, stroke, background)
- Animation style (karaoke, fade, pop, bounce)
- Position Y (0.0-1.0, default 0.75)
- Word pop effect (scale animation on entrance)
- Uppercase conversion
- Emoji injection
- Word box styling
- Glow/blur effects
- Stroke/outline width
- Drop shadow

Example:
```python
"default": {
    "font_family": "TikTokSans-Regular",
    "font_size": 48,
    "font_color": "#FFFFFF",
    "highlight_color": "#FFE000",
    "animation": "karaoke",
    "word_pop": True,
    "position_y": 0.75,
}
```

### Caching & Reuse

- **Transcript cache:** Stored at `{video_path}.transcript_cache.json` (reused across clips from same source)
- **ASS generation:** Unique per clip (different timestamp ranges)
- **Font loading:** Cached in memory during render process
- **Color conversion:** Hex to ASS BGR format cached

### Key Parameters (video_utils.py)

```python
TRANSCRIPT_CACHE_SCHEMA_VERSION = "..." # Cache version tag
EMOJI_FONT_NAME = "Noto Color Emoji"   # Fallback emoji font
POWER_WORDS = {...}                     # Keywords highlighted in accent color
HOOK_TITLE_SECONDS = 4.0                # How long hook title shows
HOOK_TITLE_MIN_SECONDS = 0.5            # Minimum hook duration
```

### Notes

- **No double-encoding:** ASS burn happens in same FFmpeg pass as resize/crop (efficiency)
- **Word syncing:** Relies on AssemblyAI word-level accuracy (typically >95% correct boundaries)
- **Emoji support:** Requires modern OS/renderer (falls back gracefully if unavailable)
- **Font availability:** Missing fonts fall back to system default (not a hard error)
- **Position safety:** Automatically adjusts Y position if text would go off-screen
- **Chunk-based rendering:** Each line rendered as unit to prevent per-word jitter (vibration fix)

---

## LLM Integration (Where AI Is Used)

### Where AI Is Called

**Location:** `backend/src/services/video_service.py:149` → `analyze_transcript()`

```
process_video_complete()
  ├─ (30%) Generate transcript (AssemblyAI)
  ├─ (50%) analyze_transcript() ← AI HERE
  │  └─ get_most_relevant_parts_by_transcript() (ai.py:662)
  │     └─ get_transcript_agent().run()  [Pydantic AI Agent]
  ├─ (70%) create_video_clips()
  └─ Complete
```

**Called via:** `backend/src/ai.py:673`
```python
result = await agent.run(
    build_transcript_analysis_prompt(
        transcript=transcript,
        include_broll=include_broll,
        clip_signals=clip_signals,
    )
)
```

**What AI Does:**
1. Analyzes transcript to find 2-5 viral clip candidates
2. Scores each segment on 4 dimensions (hook, engagement, value, shareability)
3. Writes hook titles (on-screen headlines)
4. Optionally identifies B-roll opportunities
5. Returns structured JSON with all annotations

---

### Which Model Is Used

**Configuration:** `backend/src/config.py:23` — `LLM` environment variable

**Format:** `provider:model-name`

**Supported Providers & Default Models:**

| Provider | Config Key | Default Model | Requires |
|----------|-----------|---|---|
| **Google** | `GOOGLE_API_KEY` | `google-gla:gemini-3-flash-preview` | API key |
| **OpenAI** | `OPENAI_API_KEY` | `openai:gpt-5.2` | API key |
| **Anthropic** | `ANTHROPIC_API_KEY` | `anthropic:claude-4-sonnet` | API key |
| **Ollama** (local) | `OLLAMA_BASE_URL` | Any local model | None (optional key) |

**Model Selection Logic** (`_infer_default_llm()`, config.py:188):
```python
if GOOGLE_API_KEY is set:
    use google-gla:gemini-3-flash-preview
elif OPENAI_API_KEY is set:
    use openai:gpt-5.2
elif ANTHROPIC_API_KEY is set:
    use anthropic:claude-4-sonnet
else:
    fallback google-gla:gemini-3-flash-preview
```

**Custom Model Override:**
Set `LLM` env var to override:
```bash
export LLM=anthropic:claude-3-opus-20250205
# or
export LLM=ollama:llama2
```

**Model Instantiation** (`ai.py:369` — `_build_transcript_model()`):
- **Non-Ollama:** Returns model name as string (Pydantic AI handles auth via env vars)
- **Ollama:** Returns `OllamaModel` instance with custom provider & base URL

---

### System Prompt (The AI's Instructions)

**Location:** `backend/src/ai.py:181` — `transcript_analysis_system_prompt`

**Prompt Outline** (310 lines of detailed instructions):

#### 1. Role & Contract (lines 181-192)
- "Expert transcript analyst for short-form video editing"
- **Output must be:** Valid JSON only (no markdown, prose, code fences)
- **Required fields:** `most_relevant_segments[]`, `summary`, `key_topics`
- **Per-segment fields:** `start_time`, `end_time`, `text`, `relevance_score`, `reasoning`, `virality` (with subscores), `hook_title`
- **Duration constraint:** Every segment 15-60 seconds (prefer 25-50)

#### 2. Core Objectives (lines 194-199)
1. Identify segments compelling on social media
2. Focus on complete thoughts/insights/entertaining moments
3. Prioritize hooks, emotional moments, valuable info
4. Ensure each segment engaging & worth watching
5. Score viral potential with detailed breakdown

#### 3. Grounding Rules (lines 201-208)
- Use only provided transcript & timestamps
- Never invent facts/tone/context
- Treat as span selection, not summarization
- Each segment = one contiguous range
- `text` field must match chosen span exactly
- No stitching distant moments together
- Use speaker labels only if part of spoken content

#### 4. Content Neutrality (lines 210-215)
- Don't judge based on topic sensitivity (controversial, political, adult, medical, etc.)
- Evaluate on **clip quality only:** clarity, self-contained value, hook, emotional impact, specificity, shareability
- Don't refuse analysis for risky/offensive subject matter
- Only downgrade if transcript itself is weak/confusing/unusable

#### 5. Selection Criteria (lines 217-231)
**What makes a good clip:**
- STRONG HOOKS: attention-grabbing opening
- VALUABLE: tips, insights, interesting facts, stories
- EMOTIONAL: excitement, surprise, humor, inspiration
- COMPLETE: self-contained ideas making sense alone
- ENTERTAINING: shareable content
- HIGH SIGNAL: specific, concrete language
- LOW FILLER: avoid greetings, intros, housekeeping

**What a good clip feels like:**
- Viewer understands without original context
- Complete mini-story: setup → tension/claim → detail → payoff
- Include nearby lines for setup/stakes/payoff
- Contrarian claims, lessons, examples, before/after, frameworks, surprises
- NOT: intros, sponsor reads, vague setup, fragments, definitions, meandering

#### 6. Virality Scoring (lines 233-258)
**Four 0-25 dimensions (sum to 0-100):**

**HOOK STRENGTH (0-25):**
- 20-25: Immediately grabs attention (fact, bold claim, question)
- 15-19: Good opener, creates curiosity
- 10-14: Decent but weak
- 0-9: Weak or no hook

**ENGAGEMENT (0-25):**
- 20-25: Highly entertaining, emotional, dramatic
- 15-19: Interesting, holds attention
- 10-14: Moderately engaging
- 0-9: Flat, boring

**VALUE (0-25):**
- 20-25: Actionable insights, unique knowledge, transformative
- 15-19: Useful info most don't know
- 10-14: Somewhat informative
- 0-9: Common knowledge, filler

**SHAREABILITY (0-25):**
- 20-25: "Must send to someone" content
- 15-19: Worth bookmarking
- 10-14: Nice but not shareable
- 0-9: Generic

#### 7. Hook Titles (lines 260-266)
**On-screen headline rules (3-9 words, burned to top):**
- Make scrolling viewer stop
- Bold claim, curiosity gap, number, or stakes
- Grounded in segment only—never invent
- Don't repeat first words verbatim; reframe as headline
- Plain text only: NO hashtags, emojis, quotes
- Examples: "The $40k mistake I keep seeing", "Why nobody tells you this about VC"

**Hook Types to identify (lines 268-274):**
- `question`: Curiosity-opening question
- `statement`: Bold/surprising claim
- `statistic`: Compelling numbers/data
- `story`: Narrative/anecdote start
- `contrast`: Before/after or problem/solution
- `none`: No clear hook pattern

#### 8. B-Roll Opportunities (lines 276-281)
- Identify 2-4 moments per segment where stock footage enhances
- When objects/places/concepts mentioned
- During explanations benefiting from visuals
- At emotional peaks
- Use simple, searchable keywords

#### 9. Timing Guidelines (lines 283-292)
- Target 25-50 seconds
- 15-24 only for exceptionally dense/complete moments
- CRITICAL: `start_time` ≠ `end_time` (minimum 15s apart)
- Focus on natural boundaries, not arbitrary time limits
- Include context for understanding
- Expand single good line to include setup + payoff
- Stop when topic drifts, speaker repeats, or momentum lost

#### 10. Timestamp Requirements (lines 294-302)
- Use EXACT timestamps from transcript
- Never modify format (MM:SS)
- `start_time < end_time` (always)
- Minimum 15 seconds, ideal 25-50 seconds
- Look at ranges like `[02:25 - 02:35]`, use different times
- NEVER same timestamp for both start/end
- Example: start "02:25", end "02:35" ✓ (NOT "02:25", "02:25" ✗)

#### 11. Output Rules (lines 304-310)
- `relevance_score`: How well works as standalone clip, not general importance
- Penalize: only-quotable, generic, missing setup/payoff, padded
- `virality_reasoning` & `reasoning`: Cite what's in chosen span
- `summary` & `key_topics`: Grounded in transcript, no outside interpretation
- **Target:** 2-5 segments. Quality over quantity.

---

### User Prompt (What's Sent Each Request)

**Built by:** `build_transcript_analysis_prompt()` (ai.py:421)

**Template:**
```
Analyze this video transcript and identify the most engaging segments for short-form content.

The transcript is formatted as one line per timestamped span:
[00:12 - 00:21] Spoken text here
[00:21 - 00:35] More spoken text here

Follow this workflow:
1. Read as sequence of timestamped spans
2. Select only contiguous ranges already in transcript
3. Prefer moments with strong hook, clear payoff, emotional charge, or value
4. For each segment, use earliest timestamp as start_time, latest as end_time
{broll_instruction}

Selection target: 2-5 compelling segments

{signal_section}

[FULL TRANSCRIPT HERE]
```

**Dynamic Sections:**

**B-roll instruction** (if `include_broll=True`):
```
5. Also identify B-roll opportunities for each chosen segment where stock footage could enhance the visual appeal.
```

**Clip signals section** (if provided):
```
Additional deterministic signals from transcript/audio analysis:
{clip_signals}

Use these as hints only. They should influence ranking, but every final segment 
must still be a coherent contiguous transcript range.
```

**Transcript:** Full formatted transcript with word-level timing from AssemblyAI

---

### Agent Configuration

**Location:** `ai.py:408` — `Agent[None, TranscriptAnalysis]` (Pydantic AI Agent)

```python
_transcript_agent = Agent[None, TranscriptAnalysis](
    model=_build_transcript_model(runtime_config),          # Model name or OllamaModel
    output_type=TranscriptAnalysis,                          # Pydantic model for structured JSON
    system_prompt=transcript_analysis_system_prompt,        # 310-line instruction prompt
    output_retries=2,                                        # Retry if JSON invalid (2x for Ollama, 2x for others)
)
```

**Output Type:** `TranscriptAnalysis` (Pydantic model, ai.py:169)
```python
class TranscriptAnalysis(BaseModel):
    most_relevant_segments: List[TranscriptSegment]  # Selected clips
    summary: str                                      # Video summary
    key_topics: List[str]                            # Main topics
    broll_opportunities: Optional[List[BRollOpportunity]]  # (optional)
```

Each `TranscriptSegment` includes:
- `start_time`, `end_time` (MM:SS format)
- `text` (actual transcript words)
- `relevance_score` (0-1)
- `reasoning` (why this segment works)
- `virality` (`ViralityAnalysis` with 4 subscores + total)
- `hook_title` (AI-written on-screen headline)

---

### Error Handling & Fallbacks

**If LLM fails:**
- Exception caught in `get_most_relevant_parts_by_transcript()` (ai.py:800)
- Logs error: "Error in transcript analysis: {e}"
- Raises `RuntimeError` → task marked as "error"

**If 0 segments returned:**
- Fallback in `process_video_complete()` (video_service.py:517)
- Creates 1 segment: ~middle of video, ~45 seconds duration
- Task still completes with ≥1 clip

**Missing API key:**
- `_get_missing_llm_key_error()` (ai.py:305) checks at agent creation
- Returns error message if key not set
- `process_video_complete()` raises `RuntimeError` before clip creation begins

---

### Performance & Cost

- **Typical latency:** 5-15 seconds (depends on model & transcript length)
- **Token usage:** ~1000-3000 tokens per video (transcript + system prompt)
- **Cost examples** (approximate):
  - Gemini 3 Flash: ~$0.01-0.05 per video
  - OpenAI GPT-5.2: ~$0.05-0.20 per video
  - Anthropic Claude: ~$0.05-0.15 per video
  - Ollama (local): Free

**Optimization:** Analysis results cached by (url, source_type, processing_mode) — retrying same video reuses cached AI output
