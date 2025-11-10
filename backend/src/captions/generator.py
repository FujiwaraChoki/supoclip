"""
Caption generator with word-level timing synchronization.

Specs:
- Font: Proxima Nova Sans
- Size: 135
- Alignment: Centered
- Max characters per line: 11
- Uses AssemblyAI word-level timestamps
"""
import logging
import asyncio
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json

logger = logging.getLogger(__name__)


@dataclass
class CaptionWord:
    """Single word with timing."""
    text: str
    start: float  # seconds
    end: float    # seconds
    confidence: float


@dataclass
class CaptionLine:
    """Caption line with multiple words."""
    text: str
    start: float
    end: float
    words: List[CaptionWord]


class CaptionGenerator:
    """
    Generate synchronized captions for video clips.

    Uses AssemblyAI word-level timestamps for precise synchronization.
    Formats captions according to SupoClip specs (centered, max 11 chars).
    """

    def __init__(
        self,
        font_path: str = "/app/fonts/ProximaNova-Regular.ttf",
        font_size: int = 135,
        max_chars_per_line: int = 11
    ):
        """
        Initialize caption generator.

        Args:
            font_path: Path to Proxima Nova Sans font file
            font_size: Font size (default 135)
            max_chars_per_line: Maximum characters per line (default 11)
        """
        self.font_path = font_path
        self.font_size = font_size
        self.max_chars_per_line = max_chars_per_line
        logger.info(f"Caption generator initialized: {font_size}pt, max {max_chars_per_line} chars/line")

    def format_captions(
        self,
        words: List[CaptionWord],
        clip_start: float,
        clip_end: float
    ) -> List[CaptionLine]:
        """
        Format word-level timestamps into caption lines.

        Breaks text into lines with max characters per line.
        Adjusts timing relative to clip start.

        Args:
            words: List of words with timestamps
            clip_start: Clip start time in seconds (from full video)
            clip_end: Clip end time in seconds

        Returns:
            List of formatted caption lines
        """
        # Filter words that fall within clip timeframe
        clip_words = [
            w for w in words
            if w.start >= clip_start and w.end <= clip_end
        ]

        if not clip_words:
            logger.warning(f"No words found in clip timeframe {clip_start:.2f}-{clip_end:.2f}")
            return []

        # Adjust timestamps to be relative to clip start
        adjusted_words = [
            CaptionWord(
                text=w.text,
                start=w.start - clip_start,
                end=w.end - clip_start,
                confidence=w.confidence
            )
            for w in clip_words
        ]

        # Group words into lines (max chars per line)
        lines = self._group_words_into_lines(adjusted_words)

        logger.info(f"Formatted {len(lines)} caption lines for clip")
        return lines

    def _group_words_into_lines(
        self,
        words: List[CaptionWord]
    ) -> List[CaptionLine]:
        """
        Group words into caption lines respecting max chars.

        Args:
            words: List of words

        Returns:
            List of caption lines
        """
        lines = []
        current_words = []
        current_text = ""

        for word in words:
            # Check if adding this word would exceed max chars
            test_text = current_text + (" " if current_text else "") + word.text

            if len(test_text) <= self.max_chars_per_line:
                # Add word to current line
                current_words.append(word)
                current_text = test_text
            else:
                # Start new line
                if current_words:
                    lines.append(self._create_caption_line(current_words, current_text))

                current_words = [word]
                current_text = word.text

        # Add final line
        if current_words:
            lines.append(self._create_caption_line(current_words, current_text))

        return lines

    def _create_caption_line(
        self,
        words: List[CaptionWord],
        text: str
    ) -> CaptionLine:
        """
        Create caption line from words.

        Args:
            words: Words in this line
            text: Combined text

        Returns:
            CaptionLine object
        """
        return CaptionLine(
            text=text,
            start=words[0].start,
            end=words[-1].end,
            words=words
        )

    async def add_captions_to_video(
        self,
        input_path: str,
        output_path: str,
        caption_lines: List[CaptionLine],
        position: str = "center"
    ) -> bool:
        """
        Add captions to video using FFmpeg drawtext filter.

        Args:
            input_path: Source video
            output_path: Output video with captions
            caption_lines: List of caption lines with timing
            position: Caption position ("center", "bottom", "top")

        Returns:
            True if successful
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Generate SRT subtitle file
        srt_path = input_path.replace('.mp4', '.srt')
        self._generate_srt(caption_lines, srt_path)

        # Position map
        position_map = {
            "center": "(h-text_h)/2",
            "bottom": "h-text_h-50",  # 50px from bottom
            "top": "50"  # 50px from top
        }
        y_pos = position_map.get(position, position_map["center"])

        # FFmpeg subtitles filter
        # Use ASS format for better styling control
        ass_path = srt_path.replace('.srt', '.ass')
        self._generate_ass(caption_lines, ass_path)

        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-vf', f"ass={ass_path}",
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-c:a', 'copy',
            '-movflags', '+faststart',
            '-y',
            output_path
        ]

        logger.debug(f"FFmpeg caption command: {' '.join(cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            # Cleanup temp files
            if os.path.exists(srt_path):
                os.remove(srt_path)
            if os.path.exists(ass_path):
                os.remove(ass_path)

            if process.returncode != 0:
                logger.error(f"FFmpeg caption failed with code {process.returncode}")
                logger.error(f"stderr: {stderr.decode()[:500]}")
                return False

            logger.info(f"✅ Captions added: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error adding captions: {e}", exc_info=True)
            return False

    def _generate_srt(
        self,
        caption_lines: List[CaptionLine],
        output_path: str
    ):
        """
        Generate SRT subtitle file.

        Args:
            caption_lines: Caption lines with timing
            output_path: Output SRT file path
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            for i, line in enumerate(caption_lines, start=1):
                # SRT format:
                # 1
                # 00:00:01,000 --> 00:00:03,500
                # Caption text
                #
                start_time = self._format_srt_timestamp(line.start)
                end_time = self._format_srt_timestamp(line.end)

                f.write(f"{i}\n")
                f.write(f"{start_time} --> {end_time}\n")
                f.write(f"{line.text}\n")
                f.write("\n")

        logger.debug(f"Generated SRT file: {output_path}")

    def _generate_ass(
        self,
        caption_lines: List[CaptionLine],
        output_path: str
    ):
        """
        Generate ASS subtitle file with custom styling.

        Args:
            caption_lines: Caption lines with timing
            output_path: Output ASS file path
        """
        # ASS header with styling
        header = f"""[Script Info]
Title: SupoClip Captions
ScriptType: v4.00+
Collisions: Normal
PlayDepth: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Proxima Nova,{self.font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,10,10,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(header)

            for line in caption_lines:
                start_time = self._format_ass_timestamp(line.start)
                end_time = self._format_ass_timestamp(line.end)

                # ASS dialogue line
                f.write(f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{line.text}\n")

        logger.debug(f"Generated ASS file: {output_path}")

    def _format_srt_timestamp(self, seconds: float) -> str:
        """
        Format timestamp for SRT format.

        Args:
            seconds: Time in seconds

        Returns:
            Timestamp string (HH:MM:SS,mmm)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)

        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _format_ass_timestamp(self, seconds: float) -> str:
        """
        Format timestamp for ASS format.

        Args:
            seconds: Time in seconds

        Returns:
            Timestamp string (H:MM:SS.cc)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60

        return f"{hours}:{minutes:02d}:{secs:05.2f}"

    @staticmethod
    def words_from_assemblyai(transcript_data: Dict[str, Any]) -> List[CaptionWord]:
        """
        Extract words from AssemblyAI transcript data.

        Args:
            transcript_data: AssemblyAI transcript response

        Returns:
            List of CaptionWord objects
        """
        words = []

        for word_data in transcript_data.get('words', []):
            words.append(CaptionWord(
                text=word_data['text'],
                start=word_data['start'] / 1000.0,  # Convert ms to seconds
                end=word_data['end'] / 1000.0,
                confidence=word_data.get('confidence', 1.0)
            ))

        logger.info(f"Extracted {len(words)} words from AssemblyAI transcript")
        return words

    @staticmethod
    def words_from_mlx(transcript_data: Dict[str, Any]) -> List[CaptionWord]:
        """
        Extract words from MLX Whisper transcript data.

        Args:
            transcript_data: MLX Whisper transcript response

        Returns:
            List of CaptionWord objects
        """
        words = []

        # MLX Whisper format has segments with words
        for segment in transcript_data.get('segments', []):
            for word_data in segment.get('words', []):
                words.append(CaptionWord(
                    text=word_data['word'].strip(),
                    start=word_data['start'],
                    end=word_data['end'],
                    confidence=1.0  # MLX doesn't provide confidence
                ))

        logger.info(f"Extracted {len(words)} words from MLX transcript")
        return words
