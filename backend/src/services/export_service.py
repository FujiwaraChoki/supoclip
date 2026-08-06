"""Professional batch and NLE export builders."""

from __future__ import annotations

import csv
import html
import json
import zipfile
from pathlib import Path
from typing import Iterable


def _seconds(timestamp: str) -> float:
    parts = [float(part) for part in timestamp.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def _srt_time(seconds: float) -> str:
    millis = round(max(0, seconds) * 1000)
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(clips: Iterable[dict], output: Path) -> Path:
    lines = []
    cursor = 0.0
    for index, clip in enumerate(clips, start=1):
        duration = float(clip.get("duration") or 0)
        end = cursor + duration
        lines.append(
            f"{index}\n{_srt_time(cursor)} --> {_srt_time(end)}\n{clip.get('text') or ''}\n"
        )
        cursor = end
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def build_csv(clips: Iterable[dict], output: Path) -> Path:
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "filename", "start_time", "end_time", "duration", "text", "virality_score"],
        )
        writer.writeheader()
        for clip in clips:
            writer.writerow({key: clip.get(key) for key in writer.fieldnames})
    return output


def build_fcpxml(clips: list[dict], output: Path, fps: int = 30) -> Path:
    sequence_duration = sum(float(clip.get("duration") or 0) for clip in clips)
    assets = []
    spans = []
    cursor = 0.0
    for index, clip in enumerate(clips, start=1):
        duration = float(clip.get("duration") or 0)
        uri = Path(clip["file_path"]).resolve().as_uri()
        assets.append(
            f'<asset id="r{index}" name="{html.escape(clip["filename"])}" '
            f'src="{html.escape(uri)}" start="0s" duration="{duration:.3f}s" hasVideo="1" hasAudio="1"/>'
        )
        spans.append(
            f'<asset-clip name="{html.escape(clip["filename"])}" ref="r{index}" '
            f'offset="{cursor:.3f}s" start="0s" duration="{duration:.3f}s"/>'
        )
        cursor += duration
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<fcpxml version="1.10"><resources>'
        f'<format id="r0" name="SupoClipVertical" frameDuration="1/{fps}s" width="1080" height="1920"/>'
        + "".join(assets)
        + f'</resources><library><event name="SupoClip"><project name="SupoClip Export"><sequence '
        f'format="r0" duration="{sequence_duration:.3f}s"><spine>{"".join(spans)}</spine>'
        '</sequence></project></event></library></fcpxml>'
    )
    output.write_text(xml, encoding="utf-8")
    return output


def build_edl(clips: list[dict], output: Path, fps: int = 30) -> Path:
    def tc(seconds: float) -> str:
        frames = round(seconds * fps)
        hh, rem = divmod(frames, fps * 3600)
        mm, rem = divmod(rem, fps * 60)
        ss, ff = divmod(rem, fps)
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    lines = ["TITLE: SUPOCLIP EXPORT", "FCM: NON-DROP FRAME", ""]
    cursor = 0.0
    for index, clip in enumerate(clips, start=1):
        duration = float(clip.get("duration") or 0)
        lines.extend([
            f"{index:03d}  AX       V     C        {tc(0)} {tc(duration)} {tc(cursor)} {tc(cursor + duration)}",
            f"* FROM CLIP NAME: {clip['filename']}", "",
        ])
        cursor += duration
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def build_package(clips: list[dict], output: Path, include_metadata: bool = True) -> Path:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = []
        for clip in clips:
            source = Path(clip["file_path"])
            if source.is_file():
                archive.write(source, f"clips/{clip['filename']}")
            subtitle = output.parent / f"{clip['id']}.srt"
            build_srt([clip], subtitle)
            archive.write(subtitle, f"captions/{Path(clip['filename']).stem}.srt")
            subtitle.unlink(missing_ok=True)
            manifest.append({key: value for key, value in clip.items() if key != "file_path"})
        if include_metadata:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
    return output


def render_export(export_type: str, clips: list[dict], output_dir: Path, job_id: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if export_type == "zip":
        return build_package(clips, output_dir / f"{job_id}.zip")
    if export_type == "csv":
        return build_csv(clips, output_dir / f"{job_id}.csv")
    if export_type == "srt":
        return build_srt(clips, output_dir / f"{job_id}.srt")
    if export_type == "fcpxml":
        return build_fcpxml(clips, output_dir / f"{job_id}.fcpxml")
    if export_type == "edl":
        return build_edl(clips, output_dir / f"{job_id}.edl")
    raise ValueError("Unsupported export type")
