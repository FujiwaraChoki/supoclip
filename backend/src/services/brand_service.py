"""Apply reusable brand-kit media to rendered clips."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from uuid import uuid4


logger = logging.getLogger(__name__)


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


def _overlay_logo(source: Path, logo: Path, output: Path, position: str) -> None:
    positions = {
        "top_left": "40:40", "top_right": "W-w-40:40",
        "bottom_left": "40:H-h-40", "bottom_right": "W-w-40:H-h-40",
    }
    overlay = positions.get(position, positions["top_right"])
    _run([
        "ffmpeg", "-y", "-i", str(source), "-loop", "1", "-i", str(logo),
        "-filter_complex", f"[1:v]scale='min(220,iw)':-1[wm];[0:v][wm]overlay={overlay}:format=auto[outv]",
        "-map", "[outv]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(output),
    ])


def _mix_music(source: Path, music: Path, output: Path, volume: float) -> None:
    volume = max(0.02, min(0.5, volume))
    _run([
        "ffmpeg", "-y", "-i", str(source), "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", f"[1:a]volume={volume:.3f}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]",
        "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
        "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(output),
    ])


def apply_brand_kit_to_clip(clip_path: Path, assets: list[dict], settings: dict) -> bool:
    """Apply supported kit assets in stable passes, preserving the original on failure."""
    current = clip_path
    temporary: list[Path] = []
    try:
        logo = next((Path(item["file_path"]) for item in assets if item["asset_type"] == "logo" and Path(item["file_path"]).is_file()), None)
        music = next((Path(item["file_path"]) for item in assets if item["asset_type"] == "music" and Path(item["file_path"]).is_file()), None)
        if logo:
            branded = clip_path.with_name(f".{clip_path.stem}.logo-{uuid4().hex[:8]}.mp4")
            _overlay_logo(current, logo, branded, str(settings.get("logo_position", "top_right")))
            temporary.append(branded)
            current = branded
        if music:
            mixed = clip_path.with_name(f".{clip_path.stem}.music-{uuid4().hex[:8]}.mp4")
            _mix_music(current, music, mixed, float(settings.get("music_volume", 0.12)))
            temporary.append(mixed)
            current = mixed
        if current != clip_path:
            os.replace(current, clip_path)
        for path in temporary:
            if path != current:
                path.unlink(missing_ok=True)
        return True
    except Exception as exc:
        logger.warning("Brand-kit media pass failed for %s: %s", clip_path, exc)
        for path in temporary:
            path.unlink(missing_ok=True)
        return False
