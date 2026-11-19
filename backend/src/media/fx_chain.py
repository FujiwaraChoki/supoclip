"""The dynamic-edits server chain (FINAL §6.2-§6.4): P1 ffmpeg → P2 deno (fx-core) → P3 ffmpeg, in one encode.

- **P1** decodes ``input_path`` through the caller's picture graph (the framing head; the studio's full graph through
  B-roll), adds B-roll and the reaction PiP, converts to RGBA with the source's explicit colour matrix (AR-B2) at
  30 fps and, when the scene draws behind the person, carries the person matte in the alpha channel (AR-B1). Raw
  frames go to stdout. Everything is on the E clock.
- **P2** (``src/fx_runtime/main.ts render job.json``) draws every visual effect with fx-core and writes exactly
  ``outFrames`` frames on the F clock (freezes repeat frames, speed pieces skip them; §4.4).
- **P3** burns the ASS (hook title, point labels, legacy-template captions) on top, converts to BT.709 limited range
  with explicit tags, mixes the voice with ``fxbus.wav`` (bleep mutes, warp pieces, static gain, limiter; §9) and
  encodes with the caller's final encode args.

``mode="safe"`` pipes P1 straight into P3 (no deno, identity warp, no picture FX); the audio bus is kept. Nothing in
this module decides the fallback ladder (§6.6): it returns an ``FxResult`` and never raises for a failed render. A
failed result's ``error`` starts with the stage (``analysis``, ``bus``, ``p1``, ``p2``, ``p3``, ``q`` (file mode
encoder), ``output``, ``timeout``, ``cancelled``, ``setup``) so callers can tell a P1 failure (retry without B-roll)
from the rest.

Every file this module writes lives in a private directory under the caller's per-render ``scratch_dir`` and is
removed before returning (no shared disk, §0.4.2). Labels the chain adds are prefixed ``fxp_`` (P1) and
``fxv_``/``fxa_`` (P3) so they never collide with the caller's ``[v] [r] [b] [a] [face] [top]`` (AR-m1).
"""

from __future__ import annotations

import collections
import fcntl
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Sequence

from ..edit_fx import env as fx_env
from ..edit_fx.censor import censored_words
from ..edit_fx.compat import known_fields, log_dropped
from ..edit_fx.constants import reaction_rect
from ..edit_fx.contracts import (
    FPS,
    FaceTrack,
    FxCaptionStyle,
    FxHookTitle,
    FxPlan,
    FxScene,
    FxWord,
    MatteWindow,
)
from ..edit_fx.warp import Warp, build_warp, identity_warp
from .broll_overlay import broll_overlay_graph
from .ffmpeg import NO_CHAPTERS, build_final_video_encode_args, select_audio_stream_index

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- constants

FX_RUNTIME_DIR = Path(__file__).resolve().parent.parent / "fx_runtime"
FX_MAIN_TS = FX_RUNTIME_DIR / "main.ts"
FX_DENO_CONFIG = FX_RUNTIME_DIR / "deno.json"

# Bundled font stems fx-core's drawers use (§11 "Fonts are bundled stems"); registered as "FX-<stem>" in P2.
DRAWER_FONT_STEMS: tuple[str, ...] = ("TikTokSans-Bold", "TikTokSans-Regular", "Poppins-ExtraBold", "THEBOLDFONT",
                                      "ArchivoBlack-Regular")

PIPE_SIZE = 1 << 20                 # F_SETPIPE_SZ target; one 1080x1920 RGBA frame is ~8 MB, so this only smooths
POLL_SECONDS = 0.1
CANCEL_POLL_SECONDS = 0.5
EXIT_GRACE_SECONDS = 10.0           # after P3 succeeds, how long P1/P2 get to notice the closed pipe and exit
STDERR_TAIL_CHARS = 4000
P2_STDERR_LINES = 200

P2_EXIT_STDOUT_CLOSED = 4           # §4.4: "stdout closed early"; success when P3 already exited 0 (AR-m5)
LOOP_CROSSFADE_SECONDS = 0.4
STATIC_GAIN_LIMIT_DB = 20.0
TARGET_LUFS = -14.0
AUDIO_BITRATE = "192k"
AUDIO_RATE = "48000"
FX_PROGRESS_COLOR = "#FF7A1A"

# ffprobe color_space → scale's in_color_matrix (names valid on ffmpeg 6.1 and 7.1). Untagged ⇒ bt709 (AR-B2).
_COLOR_MATRIX = {
    "bt709": "bt709",
    "smpte170m": "smpte170m",
    "bt470bg": "bt470",
    "fcc": "fcc",
    "smpte240m": "smpte240m",
    "bt2020nc": "bt2020",
    "bt2020c": "bt2020",
}
_FULL_RANGES = {"pc", "jpeg", "full"}


# ----------------------------------------------------------------------------- contracts (§6.2, VERBATIM)

@dataclass(frozen=True)
class FxRenderInput:                       # what generation/re-render hands to create_optimized_clip
    plan: FxPlan
    words: list[dict]                      # S==E words {id,start,end,text,highlight}; uncensored
    reaction_files: dict[str, Path] = field(default_factory=dict)   # cue id → local mp4
    face_track: dict | None = None         # from the sidecar on re-render / caption PATCH (skips analysis, AR-M10)


@dataclass(frozen=True)
class FxRequest:                           # what renderers hand to run_fx_chain
    plan: FxPlan                           # cues already in E (generation: identity; studio: mapped_fx)
    words: list[dict]                      # E words; censored here using bleep spans when timing == "word"
    caption: FxCaptionStyle | None         # set iff FX template and captions enabled
    picture_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)   # base picture in output (normalised)
    face_track: dict | None = None         # space "picture"; None + plan.needs_face ⇒ computed (fx_analysis)
    matte_input: Path | None = None        # studio: prebuilt matte.mp4 (base-picture space, S time)
    matte_filter: str | None = None        # studio: filter taking matte to output space AND to E time: the same
                                           # per-segment trim/setpts/concat as [0:v], then geometry (AR-B4)
    reaction_files: dict[str, Path] = field(default_factory=dict)
    broll_windows: tuple[tuple[float, float], ...] = ()
    hook_window: tuple[float, float] | None = None
    hook: FxHookTitle | None = None        # the title fx-core draws at level full (the ASS then carries none)
    progress_color: str = FX_PROGRESS_COLOR   # "#RRGGBB"; studio: DocFx.progressColor
    voice_gain: float = 1.0                # studio volume (0 when muted)
    loudness: Literal["static", "dynamic", "none"] = "static"   # generation: env FX_LOUDNORM; studio: "none" (AR-M3)


@dataclass
class FxResult:
    ok: bool
    width: int
    height: int
    level: Literal["full", "safe"]
    out_duration: float
    face_track: dict | None
    applied_warp: list[dict]               # WarpPieceJson[] actually rendered ([] for identity / safe) (AI-H4)
    timings: dict[str, float]              # p1/p2/p3/analysis/bus ms
    library_fingerprint: str = ""          # assets.library_fingerprint() of the machine that mixed
    error: str | None = None


# ----------------------------------------------------------------------------- scene (pure)

def _round(value: float, places: int = 4) -> float:
    return round(float(value), places)


def _progress_color(value: object) -> str:
    """The bar colour as ``#RRGGBB``; anything else falls back to the default orange."""
    text = str(value or "")
    if len(text) == 7 and text[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in text[1:]):
        return text
    return FX_PROGRESS_COLOR


def _bleep_spans(plan: FxPlan) -> list[tuple[float, float]]:
    return [(float(c.start), float(c.end)) for c in plan.cues if c.kind == "bleep" and c.end > c.start]


def _behind_spans(plan: FxPlan) -> list[tuple[float, float]]:
    return [(float(c.start), float(c.end)) for c in plan.cues
            if c.kind == "accent" and c.variant == "behind" and c.end > c.start]


def _scene_words(request: FxRequest) -> tuple[FxWord, ...]:
    words = list(request.words or [])
    spans = _bleep_spans(request.plan) if request.plan.timing == "word" else []
    if spans:
        words = censored_words(words, spans)
    out: list[FxWord] = []
    for index, word in enumerate(words):
        try:
            start = float(word.get("start", 0.0))
            end = float(word.get("end", start))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        text = str(word.get("text", "") or "")[:120]
        if not text.strip():
            continue
        out.append(FxWord(
            id=str(word.get("id", f"w{index}"))[:40],
            start=start,
            end=max(start, end),
            text=text,
            highlight=bool(word.get("highlight", False)),
            run=str(word.get("run") or "clip")[:40],
        ))
    return tuple(out)


def _face_track_to_output(face_track: dict | None, picture_rect: tuple[float, float, float, float]) -> FaceTrack | None:
    """A ``FaceTrack`` in space "output": picture-space rows are placed with ``picture_rect`` (x, y, w, h normalised
    in the output frame). Landmark pairs are moved the same way; the score is kept. Lenient: unreadable → None."""
    if not face_track:
        return None
    clean, dropped = known_fields(FaceTrack, face_track)
    log_dropped("fx_chain.face_track", dropped)
    try:
        track = FaceTrack.model_validate(clean)
    except Exception as exc:  # noqa: BLE001 - a bad track only costs face-anchored drawing
        logger.warning("fx_face_track_unreadable (%s); drawing without faces", exc)
        return None
    if track.space == "output":
        return track
    rx, ry, rw, rh = (float(v) for v in picture_rect)
    if (rx, ry, rw, rh) == (0.0, 0.0, 1.0, 1.0):
        return track.model_copy(update={"space": "output"})
    samples = []
    for t, rows in track.samples:
        placed = []
        for row in rows:
            if len(row) < 4:
                continue
            values = list(row)
            values[0] = rx + values[0] * rw
            values[1] = ry + values[1] * rh
            values[2] = values[2] * rw
            values[3] = values[3] * rh
            for i in range(5, min(len(values), 15)):
                values[i] = rx + values[i] * rw if (i - 5) % 2 == 0 else ry + values[i] * rh
            placed.append(tuple(_round(v) for v in values))
        samples.append((t, tuple(placed)))
    return FaceTrack(version=1, space="output", fps=track.fps, samples=tuple(samples))


def _scene_cues(request: FxRequest, warp: Warp) -> tuple:
    """The cue kinds this render draws, sorted by (start, id).

    Speed and freeze cues only exist in the scene when the warp they produced is applied (FX_WARP=off, safe level and
    the identity retry render without them, so no freeze label shows over a hold that is not there). A reaction cue
    without a local file is dropped: P1 has no PiP for it, so its frame would outline nothing.
    """
    warped = warp.nontrivial
    cues = []
    for cue in request.plan.cues:
        if cue.kind in ("speed", "freeze") and not warped:
            continue
        if cue.kind == "reaction" and not _reaction_file(request, cue.id):
            continue
        cues.append(cue)
    cues.sort(key=lambda c: (c.start, c.id))
    return tuple(cues)


def _reaction_file(request: FxRequest, cue_id: str) -> Path | None:
    path = request.reaction_files.get(cue_id) if request.reaction_files else None
    if path is None:
        return None
    path = Path(path)
    return path if path.is_file() else None


def build_scene(request: FxRequest, *, out_w: int, out_h: int, warp: Warp, face_track: dict | None,
                matte_windows: list[MatteWindow]) -> FxScene:
    """Everything P2 needs to draw this render (§3.1 ``FxScene``). Pure.

    ``face_track`` is in space "picture" (relative to ``request.picture_rect``) or already "output", on the E clock;
    it is placed into the output frame here. ``matte_windows`` are E windows (person or not) for behind accents.
    """
    plan = request.plan
    return FxScene(
        version=1,
        width=int(out_w),
        height=int(out_h),
        edit_duration=float(warp.edit_duration),
        out_duration=float(warp.out_duration),
        warp=tuple(warp.to_json()),
        template=plan.template,
        cues=_scene_cues(request, warp),
        words=_scene_words(request),
        caption=request.caption,
        grade=plan.grade,
        grain=plan.grain,
        progress_bar=plan.progress_bar,
        progress_color=_progress_color(request.progress_color),
        loop_ending=plan.loop_ending,
        hook_window=tuple(request.hook_window) if request.hook_window else None,
        hook_title_suppressed=plan.hook_title_suppressed,
        hook=None if plan.hook_title_suppressed else request.hook,
        face_track=_face_track_to_output(face_track, request.picture_rect),
        broll_windows=tuple((float(a), float(b)) for a, b in request.broll_windows),
        matte_windows=tuple(sorted(matte_windows, key=lambda m: m.start)),
        seed=plan.seed,
    )


# ----------------------------------------------------------------------------- probes and command pieces

def _run_probe(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def probe_color(input_path: Path) -> tuple[str, str]:
    """``(in_color_matrix, in_range)`` for P1's RGB conversion; untagged or unreadable ⇒ ``("bt709", "tv")``."""
    try:
        result = _run_probe([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=color_space,color_range", "-of", "json", str(input_path),
        ])
        stream = (json.loads(result.stdout or "{}").get("streams") or [{}])[0]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return "bt709", "tv"
    matrix = _COLOR_MATRIX.get(str(stream.get("color_space") or "").lower(), "bt709")
    color_range = "pc" if str(stream.get("color_range") or "").lower() in _FULL_RANGES else "tv"
    return matrix, color_range


def probe_output(path: Path) -> dict[str, Any] | None:
    """Video frame count (packets) and size of a rendered file, or None when ffprobe cannot read it."""
    try:
        result = _run_probe([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
            "-show_entries", "stream=nb_read_packets,width,height", "-of", "json", str(path),
        ], timeout=120)
        if result.returncode != 0:
            return None
        stream = (json.loads(result.stdout or "{}").get("streams") or [None])[0]
        if not stream:
            return None
        return {
            "frames": int(stream.get("nb_read_packets") or 0),
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
        }
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None


def _count_inputs(args: Sequence[str]) -> int:
    return sum(1 for arg in args if arg == "-i")


def _even(value: float) -> int:
    rounded = int(round(value))
    return max(2, rounded - (rounded % 2))


def reaction_pip(request: FxRequest, cues: Sequence, *, base_label: str, first_input: int,
                 out_w: int, out_h: int) -> tuple[list[str], str, str]:
    """Input args, graph fragment and output label for the reaction PiPs (§6.3): each GIF loops for its window,
    scaled to cover ``reaction_rect(W, H)`` and overlaid between its start and end (E). Drawn after B-roll."""
    x, y, w, h = reaction_rect(out_w, out_h)
    rw, rh = _even(w), _even(h)
    px, py = int(round(x)), int(round(y))
    args: list[str] = []
    parts: list[str] = []
    current = base_label
    index = first_input
    for i, cue in enumerate(c for c in cues if c.kind == "reaction"):
        path = _reaction_file(request, cue.id)
        if path is None or cue.end <= cue.start:
            continue
        duration = cue.end - cue.start
        args += ["-stream_loop", "-1", "-t", f"{duration + 0.5:.3f}", "-i", str(path)]
        clip, out = f"[fxp_r{i}]", f"[fxp_rx{i}]"
        parts.append(
            f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS+{cue.start:.3f}/TB,"
            f"scale={rw}:{rh}:force_original_aspect_ratio=increase:flags=bicubic,crop={rw}:{rh},setsar=1{clip}"
        )
        parts.append(f"{current}{clip}overlay={px}:{py}:eof_action=pass:"
                     f"enable='between(t,{cue.start:.3f},{cue.end:.3f})'{out}")
        current = out
        index += 1
    return args, ";".join(parts), current


def build_p1_command(*, input_path: Path, head: str, head_label: str, out_w: int, out_h: int,
                     broll_overlays: list[dict], broll_inputs: list[str], tail: str,
                     color: tuple[str, str], reaction_args: list[str], reaction_graph: str, reaction_label: str | None,
                     matte_input: Path | None, matte_filter: str | None, in_frames: int) -> list[str]:
    """``ffmpeg … -map [fxp_v] -an -f rawvideo -pix_fmt rgba pipe:1`` (§6.3 P1)."""
    graph = [head]
    label = head_label
    if broll_overlays:
        broll_graph, label = broll_overlay_graph(head_label, broll_overlays, out_w, out_h, first_input=1)
        if broll_graph:
            graph.append(broll_graph)
    if reaction_graph:
        graph.append(reaction_graph.replace("{base}", label))
        label = reaction_label or label
    matrix, color_range = color
    pre = f"{tail}," if tail else ""
    rgb_label = "[fxp_rgb]" if matte_input else "[fxp_v]"
    graph.append(f"{label}{pre}scale=in_color_matrix={matrix}:in_range={color_range},fps={FPS},format=rgba{rgb_label}")
    inputs = ["-i", str(input_path), *broll_inputs, *reaction_args]
    if matte_input:
        matte_index = _count_inputs(inputs)
        inputs += ["-i", str(matte_input)]
        if matte_filter:
            chain = f"[{matte_index}:v]{matte_filter.strip().rstrip(',')},"
        else:
            chain = f"[{matte_index}:v]"
        graph.append(f"{chain}scale={out_w}:{out_h}:flags=bilinear,fps={FPS},format=gray[fxp_mk]")
        graph.append("[fxp_rgb][fxp_mk]alphamerge[fxp_v]")
    return [
        "ffmpeg", "-y", "-nostdin", "-v", "error",
        *inputs,
        "-filter_complex", ";".join(part for part in graph if part),
        "-map", "[fxp_v]", "-an",
        "-frames:v", str(in_frames),
        "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1",
    ]


def _deno_dir() -> str:
    explicit = (os.environ.get("DENO_DIR") or "").strip()
    if explicit:
        return explicit
    cache = (os.environ.get("XDG_CACHE_HOME") or "").strip() or str(Path.home() / ".cache")
    return str(Path(cache) / "deno")


def build_p2_command(job_path: Path, read_dirs: Sequence[Path]) -> list[str]:
    """The P2 argv: §4.4 as amended by WP6 / D1 (``/usr/bin/ldd``, the napi loader env names, ``--allow-sys=homedir``).
    ``--config`` pins the import map wherever the process is started from."""
    allow_read = []
    for entry in [*read_dirs, Path("/usr/share/fonts"), Path("/usr/bin/ldd"), Path(_deno_dir())]:
        text = str(entry)
        if text and text not in allow_read:
            allow_read.append(text)
    return [
        fx_env.fx_deno_bin(), "run", "--cached-only", "--frozen", "--no-prompt",
        "--config", str(FX_DENO_CONFIG),
        "--allow-ffi",
        f"--allow-read={','.join(allow_read)}",
        "--allow-env=DENO_DIR,FX_DEBUG,NAPI_RS_NATIVE_LIBRARY_PATH,NAPI_RS_FORCE_WASI,DISABLE_SYSTEM_FONTS_LOAD",
        "--allow-sys=homedir",
        str(FX_MAIN_TS), "render", str(job_path),
    ]


def build_p3_command(*, video_input: list[str], input_path: Path, bus_path: Path | None, subs: str | None,
                     audio_graph: str | None, encode_args: list[str] | None, out_frames: int,
                     output_path: Path) -> list[str]:
    """§6.3 P3: raw RGBA (or the file-mode .mov) → ASS → BT.709 tv yuv420p → x264, tagged; voice ⊕ bus → AAC."""
    inputs = [*video_input, "-i", str(input_path)]
    if bus_path is not None:
        inputs += ["-i", str(bus_path)]
    # setparams tags the frames too: newer encoders take colour properties from the frames before the -color_* options.
    video = (f"[0:v]{subs + ',' if subs else ''}setsar=1,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
             f"setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709[fxv_out]")
    graph = video if not audio_graph else f"{video};{audio_graph}"
    encode = _at_chain_rate(list(encode_args) if encode_args is not None else build_final_video_encode_args())
    command = [
        "ffmpeg", "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1",
        *inputs,
        "-filter_complex", graph,
        "-map", "[fxv_out]",
    ]
    if audio_graph:
        command += ["-map", "[fxa_out]"]
    command += [
        *encode,
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
    ]
    if audio_graph:
        command += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", AUDIO_RATE]
    else:
        command += ["-an"]
    command += ["-frames:v", str(out_frames), "-movflags", "+faststart"]
    if "-map_chapters" not in encode:
        command += list(NO_CHAPTERS)
    command.append(str(output_path))
    return command


def _at_chain_rate(encode: list[str]) -> list[str]:
    """The caller's encode args with every output rate set to the chain's 30 fps.

    The chain is 30 fps end to end (P1 ``fps=30``, P2's frame protocol, P3's raw input). A branch that encodes at
    the source rate (``original`` output of a 60 fps upload passes ``-r 60``) would make P3 duplicate every frame,
    and ``-frames:v outFrames`` would then cut the clip at half its length while the audio runs to the end.
    """
    out = list(encode)
    for index, arg in enumerate(out[:-1]):
        if arg in ("-r", "-r:v") and out[index + 1] != str(FPS):
            logger.info("fx_chain_encode_rate_forced from=%s to=%s", out[index + 1], FPS)
            out[index + 1] = str(FPS)
    return out


def _font_paths(caption: FxCaptionStyle | None, fonts_dir: Path | None, hook: FxHookTitle | None = None) -> dict[str, str]:
    """Font stem → file for P2: the drawers' bundled stems plus the caption and hook title fonts. Missing fonts are
    skipped (P2 warns and the text falls back to system Noto)."""
    from ..font_registry import FONTS_DIR, SUPPORTED_FONT_EXTENSIONS, find_font_path

    stems = list(DRAWER_FONT_STEMS)
    if caption is not None and caption.font and caption.font not in stems:
        stems.append(caption.font)
    if hook is not None and hook.font and hook.font not in stems:
        stems.append(hook.font)
    found: dict[str, str] = {}
    for stem in stems:
        path: Path | None = None
        for directory in (fonts_dir, FONTS_DIR):
            if directory is None:
                continue
            for ext in SUPPORTED_FONT_EXTENSIONS:
                candidate = Path(directory) / f"{stem}{ext}"
                if candidate.is_file():
                    path = candidate
                    break
            if path:
                break
        if path is None:
            try:
                path = find_font_path(stem, allow_all_user_fonts=True)
            except Exception:  # noqa: BLE001 - a missing font is a P2 warning, never a failure
                path = None
        if path is not None and Path(path).is_file():
            found[stem] = str(Path(path).resolve())
    return found


def _atlas() -> dict[str, str]:
    try:
        from ..edit_fx.assets import load_index

        index = load_index()
        if index.atlas_png.is_file() and index.atlas_json.is_file():
            return {"png": str(index.atlas_png), "json": str(index.atlas_json)}
    except Exception as exc:  # noqa: BLE001 - emoji are decorative
        logger.warning("fx_atlas_unavailable (%s)", exc)
    return {"png": "", "json": ""}


def _fx_assets_dir() -> Path:
    from ..edit_fx.assets import builtin_dir

    return builtin_dir()


def _library_fingerprint() -> str:
    try:
        from ..edit_fx.assets import library_fingerprint

        return library_fingerprint()
    except Exception:  # noqa: BLE001 - logging only
        return ""


def _set_pipe_size(stream) -> None:
    setter = getattr(fcntl, "F_SETPIPE_SZ", 1031)
    try:
        fcntl.fcntl(stream.fileno(), setter, PIPE_SIZE)
    except (OSError, ValueError, AttributeError):
        pass   # EPERM above /proc/sys/fs/pipe-max-size, or not Linux: the default buffer still works


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - STDERR_TAIL_CHARS))
            return fh.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


# ----------------------------------------------------------------------------- the audio and analysis stages

def _audio_module():
    from . import fx_audio   # WP12b

    return fx_audio


def _analysis_module():
    from . import fx_analysis   # WP12c

    return fx_analysis


def _voice_fragment(input_path: Path, voice_fragment: str | None, has_audio: bool) -> str | None:
    """The caller's fragment, else ``[1:a:<dialogue track>]anull[fxa_voice0]``; None for a silent source."""
    if voice_fragment:
        return voice_fragment if has_audio else None
    if not has_audio:
        return None
    index = select_audio_stream_index(input_path)
    if index is None:
        return None
    return f"[1:a:{index}]anull[fxa_voice0]"


def _silent_voice(edit_duration: float) -> str:
    return f"anullsrc=r=48000:cl=stereo,atrim=0:{max(edit_duration, 1.0 / FPS):.6f}[fxa_voice0]"


# ----------------------------------------------------------------------------- process supervision

class _Stage:
    def __init__(self, name: str, proc: subprocess.Popen, stderr_path: Path | None, started: float) -> None:
        self.name = name
        self.proc = proc
        self.stderr_path = stderr_path
        self.started = started
        self.ended: float | None = None
        self.killed = False

    def poll(self) -> int | None:
        code = self.proc.poll()
        if code is not None and self.ended is None:
            self.ended = time.monotonic()
        return code

    def elapsed_ms(self) -> float:
        end = self.ended if self.ended is not None else time.monotonic()
        return round((end - self.started) * 1000.0, 1)


def _kill_all(stages: Sequence[_Stage]) -> None:
    for stage in stages:
        if stage.proc.poll() is None:
            stage.killed = True
            try:
                stage.proc.kill()
            except OSError:
                pass
    for stage in stages:
        try:
            stage.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:   # pragma: no cover - SIGKILL does not block
            logger.error("fx_chain_kill_wait_timeout stage=%s pid=%s", stage.name, stage.proc.pid)
        stage.poll()


class _P2Stderr(threading.Thread):
    """Drains P2's JSON-line stderr (never let it block deno), keeps a bounded tail, the last error and the stats."""

    def __init__(self, stream) -> None:
        super().__init__(name="fx-p2-stderr", daemon=True)
        self.stream = stream
        self.lines: collections.deque[str] = collections.deque(maxlen=P2_STDERR_LINES)
        self.error: str | None = None
        self.stats: dict | None = None
        self.warnings: list[str] = []

    def run(self) -> None:
        try:
            for raw in iter(self.stream.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self.lines.append(line)
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("ev")
                if kind == "error":
                    self.error = str(event.get("message", ""))[:2000]
                elif kind == "stats":
                    self.stats = event
                elif kind == "warn" and len(self.warnings) < 20:
                    self.warnings.append(str(event.get("message", ""))[:300])
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.stream.close()
            except OSError:
                pass

    def tail(self) -> str:
        return (self.error or "\n".join(list(self.lines)[-10:]))[-STDERR_TAIL_CHARS:]


class _Progress(threading.Thread):
    """Reads ffmpeg ``-progress pipe:1`` key=value lines from P3 and reports ``frame / out_frames``."""

    def __init__(self, stream, out_frames: int, on_progress: Callable[[float], None] | None) -> None:
        super().__init__(name="fx-p3-progress", daemon=True)
        self.stream = stream
        self.out_frames = max(1, out_frames)
        self.on_progress = on_progress
        self.frame = 0

    def run(self) -> None:
        last = -1.0
        try:
            for raw in iter(self.stream.readline, b""):
                line = raw.decode("ascii", "replace").strip()
                if not line.startswith("frame="):
                    continue
                try:
                    self.frame = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                fraction = min(1.0, self.frame / self.out_frames)
                if self.on_progress is not None and fraction - last >= 0.01:
                    last = fraction
                    try:
                        self.on_progress(fraction)
                    except Exception:  # noqa: BLE001 - a progress callback never stops a render
                        logger.debug("fx_chain on_progress raised", exc_info=True)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.stream.close()
            except OSError:
                pass


def _benign_exit(stage: _Stage, code: int | None) -> bool:
    """An upstream exit caused by the downstream reader going away: P2's exit 4 (§4.4) or P1's broken pipe (SIGPIPE,
    or ffmpeg's EPIPE error code, which differs between versions). Harmless once P3 has exited 0 (AR-m5)."""
    if code in (None, 0):
        return True
    if stage.name == "p2":
        return code == P2_EXIT_STDOUT_CLOSED
    if code == -13:
        return True
    return stage.stderr_path is not None and "Broken pipe" in _tail(stage.stderr_path)


def _blame(stages: Sequence[_Stage], final: _Stage) -> str:
    """The stage that failed first in pipeline order: a process that exited non-zero on its own (not killed by us)
    for a reason other than its reader going away. Falls back to the final stage."""
    for stage in stages:
        if stage is final or stage.killed:
            continue
        code = stage.proc.returncode
        if code not in (None, 0) and not _benign_exit(stage, code):
            return stage.name
    return final.name


def _describe(stage_name: str, stages: Sequence[_Stage]) -> str:
    stage = next((s for s in stages if s.name == stage_name), None)
    if stage is None:
        return stage_name
    code = stage.proc.returncode
    return f"{stage_name}: exit {code}" if code is not None else stage_name


def _supervise(stages: list[_Stage], *, final: _Stage, timeout: float,
               should_cancel: Callable[[], bool] | None) -> str | None:
    """Wait for ``final`` (P3, or the file-mode encoder) to exit 0. Returns None on success, else an error string
    ("<stage>: exit <code>", "timeout", "cancelled"). Any failure kills every process; all of them are always reaped
    (``kill()`` then ``wait()``: no zombies). An upstream process that exits benignly (its reader went away: P2's
    exit 4, P1's broken pipe) is ignored; one that fails on its own fails the run, whatever ``final`` does."""
    deadline = time.monotonic() + timeout
    next_cancel_check = 0.0
    try:
        while True:
            code = final.poll()
            if code is not None:
                if code != 0:
                    _kill_all(stages)
                    return _describe(_blame(stages, final), stages)
                break
            # An upstream process that failed on its own means the final stage got (or will get) a short stream:
            # even if it then exits 0, the render is incomplete.
            if any(s is not final and not _benign_exit(s, s.poll()) for s in stages):
                _kill_all(stages)
                return _describe(_blame(stages, final), stages)
            now = time.monotonic()
            if now > deadline:
                _kill_all(stages)
                return "timeout"
            if should_cancel is not None and now >= next_cancel_check:
                next_cancel_check = now + CANCEL_POLL_SECONDS
                try:
                    cancelled = bool(should_cancel())
                except Exception:  # noqa: BLE001 - a broken cancel probe never stops a render
                    cancelled = False
                if cancelled:
                    _kill_all(stages)
                    return "cancelled"
            time.sleep(POLL_SECONDS)
        # Final stage succeeded: upstream stops on its own (P2 exits 4, P1 hits a broken pipe) or finishes normally.
        grace = time.monotonic() + EXIT_GRACE_SECONDS
        while any(stage.poll() is None for stage in stages) and time.monotonic() < grace:
            time.sleep(POLL_SECONDS)
        _kill_all(stages)
        if any(s is not final and not s.killed and not _benign_exit(s, s.proc.returncode) for s in stages):
            return _describe(_blame(stages, final), stages)
        return None
    except BaseException:
        _kill_all(stages)
        raise


# ----------------------------------------------------------------------------- run_fx_chain

def _analysis(request: FxRequest, *, input_path: Path, head: str, head_label: str, out_w: int, out_h: int,
              work: Path, timings: dict[str, float]) -> tuple[dict | None, list[MatteWindow], Path | None, bool]:
    """Face track and matte (§6.4). Returns (face_track, matte_windows, matte_file, computed_face)."""
    plan = request.plan
    behind = _behind_spans(plan)
    # The hook title moves beneath a head it would cover, so it needs the track too.
    need_face = (plan.needs_face or request.hook is not None) and request.face_track is None
    need_matte = bool(behind) and request.matte_input is None
    face_track = request.face_track
    if request.matte_input is not None and behind:
        windows = [MatteWindow(start=a, end=b, person=True) for a, b in behind]
    else:
        windows = [MatteWindow(start=a, end=b, person=False) for a, b in behind]
    matte_file: Path | None = request.matte_input if behind and request.matte_input is not None else None
    if not (need_face or need_matte):
        return face_track, windows, matte_file, False
    started = time.monotonic()
    dest = work / "matte.mkv" if need_matte else None
    computed = False
    try:
        track, analysed = _analysis_module().analyse(
            input_path, head, head_label, out_w=out_w, out_h=out_h, face=need_face,
            matte_windows=behind if need_matte else [], matte_dest=dest,
        )
        if need_face:
            face_track = track
            computed = track is not None
        if need_matte:
            windows = [w if isinstance(w, MatteWindow) else MatteWindow.model_validate(w) for w in analysed] or windows
            if dest is not None and dest.is_file() and any(w.person for w in windows):
                matte_file = dest
            else:
                windows = [w.model_copy(update={"person": False}) for w in windows]
    except Exception as exc:  # noqa: BLE001 - analysis is never fatal: faces fall back, behind draws in front
        logger.warning("fx_analysis_failed (%s); face cues fall back and behind text draws in front", exc)
        windows = [MatteWindow(start=a, end=b, person=False) for a, b in behind]
    timings["analysis"] = round((time.monotonic() - started) * 1000.0, 1)
    return face_track, windows, matte_file, computed


def run_fx_chain(*, input_path: Path, output_path: Path, head: str, head_label: str, out_w: int, out_h: int,
                 broll_overlays: list[dict], broll_inputs: list[str], subtitle_ass_path: Path | None,
                 fonts_dir: Path | None, request: FxRequest, mode: Literal["full", "safe"] = "full",
                 voice_fragment: str | None = None, scratch_dir: Path,
                 tail: str = "", encode_args: list[str] | None = None, has_audio: bool = True,
                 should_cancel: Callable[[], bool] | None = None,
                 on_progress: Callable[[float], None] | None = None) -> FxResult:
    """Render ``input_path`` through P1 → P2 → P3 (or P1 → P3 for ``mode="safe"``) into ``output_path``.

    head: the complete P1 head ending in [head_label]. Generation: the framing head (no subs). Studio: its full graph
    up to after B-roll with broll_overlays=[] (its B-roll inputs still go in ``broll_inputs``).
    tail: composited()'s tail (e.g. "setsar=1" for `original`), applied in P1 before fps/format.
    encode_args: P3 video encode args; None ⇒ build_final_video_encode_args().
    voice_fragment: a filter_complex fragment producing [fxa_voice0] from input index 1 (== input_path in P3).
    Blocking; call it through ``run_in_thread`` like every render. Never raises for a failed render.
    """
    started = time.monotonic()
    plan = request.plan
    out_w, out_h = int(out_w), int(out_h)
    edit_duration = max(1.0 / FPS, float(plan.edit_duration))
    full = mode == "full"
    warp = build_warp(plan.cues, edit_duration) if full and fx_env.fx_warp_enabled() else identity_warp(edit_duration)
    out_duration = warp.out_duration
    in_frames = max(1, int(round(edit_duration * FPS)))
    out_frames = max(1, int(round(out_duration * FPS))) if full else in_frames
    # The audio is padded/trimmed to exactly the video's length (outFrames at 30 fps), not to the unrounded F length,
    # so A/V agree to the sample whatever the plan's edit duration.
    av_duration = out_frames / FPS
    timings: dict[str, float] = {}
    applied_warp = warp.to_json() if full else []

    def result(ok: bool, error: str | None = None, face: dict | None = None, fingerprint: str = "") -> FxResult:
        timings["total"] = round((time.monotonic() - started) * 1000.0, 1)
        return FxResult(
            ok=ok, width=out_w, height=out_h, level="full" if full else "safe",
            out_duration=out_frames / FPS, face_track=face, applied_warp=applied_warp if ok else [],
            timings=timings, library_fingerprint=fingerprint, error=error,
        )

    Path(scratch_dir).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="fxchain-", dir=str(scratch_dir)))
    stages: list[_Stage] = []
    threads: list[threading.Thread] = []
    face_track = request.face_track
    try:
        # Analysis (full only; safe draws no picture FX).
        matte_windows: list[MatteWindow] = []
        matte_file: Path | None = None
        computed_face = False
        if full:
            face_track, matte_windows, matte_file, computed_face = _analysis(
                request, input_path=input_path, head=head, head_label=head_label, out_w=out_w, out_h=out_h,
                work=work, timings=timings,
            )

        # Audio: the bus (SFX, bleep tone, ducked music; F time) and the P3 graph.
        fingerprint = ""
        bus_path: Path | None = None
        if plan.has_audio:
            bus_started = time.monotonic()
            candidate = work / "fxbus.wav"
            try:
                if _audio_module().build_bus(plan, words_e=list(request.words or []), warp=warp,
                                             out_duration=av_duration, path=candidate):
                    bus_path = candidate
                    fingerprint = _library_fingerprint()
            except Exception as exc:  # noqa: BLE001 - the bus is decorative; the voice still renders
                logger.warning("fx_bus_failed (%s); rendering without SFX/music", exc)
            timings["bus"] = round((time.monotonic() - bus_started) * 1000.0, 1)

        voice = _voice_fragment(input_path, voice_fragment, has_audio)
        real_voice = voice is not None
        audio_graph: str | None = None
        if voice is not None or bus_path is not None:
            voice = voice or _silent_voice(edit_duration)
            static_gain: float | None = None
            mix_gain: float | None = None
            if request.loudness == "static":
                static_gain = 0.0
                if real_voice:
                    lufs_started = time.monotonic()
                    try:
                        audio = _audio_module()
                        # The voice as P3 hears it before the warp: after the bleep mutes (§9 "static").
                        with_mutes = getattr(audio, "measurement_fragment", None)
                        measured = with_mutes(voice, _bleep_spans(plan)) if with_mutes else voice
                        lufs = audio.measure_voice_lufs(input_path, measured, duration=edit_duration)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("fx_voice_lufs_failed (%s); static gain 0 dB", exc)
                        lufs = None
                    if lufs is not None and math.isfinite(lufs):
                        static_gain = max(-STATIC_GAIN_LIMIT_DB, min(STATIC_GAIN_LIMIT_DB, TARGET_LUFS - lufs))
                    if bus_path is not None:
                        try:
                            audio = _audio_module()
                            measure_mix = getattr(audio, "measure_mix_lufs", None)
                            if measure_mix is not None:
                                mix_gain = audio.mix_gain_db(measure_mix(
                                    input_path, measured, bus_path, voice_gain_db=static_gain,
                                    duration=edit_duration))
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("fx_mix_lufs_failed (%s); mix gain 0 dB", exc)
                    timings["lufs"] = round((time.monotonic() - lufs_started) * 1000.0, 1)
            audio_graph = _audio_module().audio_graph(
                voice_fragment=voice, bleeps_e=_bleep_spans(plan), warp=warp, out_duration=av_duration,
                has_bus=bus_path is not None, voice_gain=float(request.voice_gain), loudness=request.loudness,
                static_gain_db=static_gain,
                **({"mix_gain_db": mix_gain} if mix_gain is not None else {}),
                # The voice crossfades into its own head only under P2's frame0 crossfade (full); at level safe the
                # picture does not loop, so the audio must not either.
                loop_crossfade=LOOP_CROSSFADE_SECONDS if full and plan.loop_ending == "crossfade" else 0.0,
            )

        # Analysis, the bus and the loudness pass run before any process is started: honour a cancel made meanwhile.
        if should_cancel is not None:
            try:
                cancelled = bool(should_cancel())
            except Exception:  # noqa: BLE001 - a broken cancel probe never stops a render
                cancelled = False
            if cancelled:
                logger.info("fx_chain_cancelled before start")
                return result(False, "cancelled", face=face_track)

        # P1.
        reaction_cues = _scene_cues(request, warp) if full else ()
        first_reaction_input = 1 + _count_inputs(broll_inputs)
        reaction_args, reaction_graph, reaction_label = reaction_pip(
            request, reaction_cues, base_label="{base}", first_input=first_reaction_input, out_w=out_w, out_h=out_h,
        )
        if not reaction_graph:
            reaction_label = None
        p1_cmd = build_p1_command(
            input_path=input_path, head=head, head_label=head_label, out_w=out_w, out_h=out_h,
            broll_overlays=broll_overlays, broll_inputs=list(broll_inputs), tail=tail, color=probe_color(input_path),
            reaction_args=reaction_args, reaction_graph=reaction_graph, reaction_label=reaction_label,
            matte_input=matte_file if full and any(w.person for w in matte_windows) else None,
            matte_filter=request.matte_filter if matte_file is not None and matte_file == request.matte_input else None,
            in_frames=in_frames,
        )

        subs = None
        if subtitle_ass_path is not None:
            from .ffmpeg import subtitles_filter_fragment

            subs = subtitles_filter_fragment(subtitle_ass_path, fonts_dir)

        chain_mode = fx_env.fx_chain_mode() if full else "pipe"
        raw_input = ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{out_w}x{out_h}", "-r", str(FPS), "-i", "pipe:0"]

        p2_cmd: list[str] | None = None
        if full:
            scene = build_scene(request, out_w=out_w, out_h=out_h, warp=warp,
                                face_track=_as_output_track(face_track) if computed_face else face_track,
                                matte_windows=matte_windows)
            fonts = _font_paths(request.caption, fonts_dir, scene.hook)
            atlas = _atlas()
            job = {
                "version": 1, "width": out_w, "height": out_h, "fps": FPS,
                "inFrames": in_frames, "outFrames": out_frames,
                "scene": scene.model_dump(mode="json"), "fonts": fonts, "atlas": atlas,
            }
            job_path = work / "job.json"
            job_path.write_text(json.dumps(job, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            read_dirs = [work, _fx_assets_dir()]
            read_dirs += sorted({Path(p).parent for p in fonts.values()})
            read_dirs += sorted({Path(p).parent for p in atlas.values() if p})
            p2_cmd = build_p2_command(job_path, read_dirs)

        log_path = {name: work / f"{name}.stderr" for name in ("p1", "p3", "q")}

        def spawn(name: str, cmd: list[str], *, stdin, stdout, stderr_to_file: bool = True,
                  cwd: Path | None = None, env: dict | None = None) -> _Stage:
            err = open(log_path[name], "wb") if stderr_to_file else subprocess.PIPE
            try:
                proc = subprocess.Popen(cmd, stdin=stdin, stdout=stdout, stderr=err,
                                        cwd=str(cwd) if cwd else None, env=env)
            finally:
                if stderr_to_file:
                    err.close()
            stage = _Stage(name, proc, log_path.get(name) if stderr_to_file else None, time.monotonic())
            stages.append(stage)
            return stage

        def setup_failure(name: str, exc: BaseException) -> FxResult:
            _kill_all(stages)
            logger.error("fx_chain_setup_failed stage=%s error=%s", name, exc)
            return result(False, f"{name}: could not start: {exc}", face=face_track)

        try:
            p1 = spawn("p1", p1_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        except OSError as exc:
            return setup_failure("p1", exc)
        _set_pipe_size(p1.proc.stdout)

        upstream = p1.proc.stdout
        p2_reader: _P2Stderr | None = None
        mov_path = work / "p2.mov"
        if full and p2_cmd is not None:
            env = dict(os.environ)
            env.setdefault("NO_COLOR", "1")
            try:
                p2 = spawn("p2", p2_cmd, stdin=upstream, stdout=subprocess.PIPE,
                           stderr_to_file=False, cwd=FX_RUNTIME_DIR, env=env)
            except OSError as exc:
                upstream.close()
                return setup_failure("p2", exc)
            upstream.close()   # our copy: EOF must reach P2 when P1 exits
            _set_pipe_size(p2.proc.stdout)
            p2_reader = _P2Stderr(p2.proc.stderr)
            p2_reader.start()
            threads.append(p2_reader)
            upstream = p2.proc.stdout
            if chain_mode == "file":
                # FX_CHAIN_MODE=file: P2's frames are stored as a qtrle .mov; P3 then reads the file (same frames).
                q_cmd = ["ffmpeg", "-y", "-nostdin", "-v", "error", *raw_input,
                         "-c:v", "qtrle", "-pix_fmt", "rgb24", str(mov_path)]
                try:
                    q = spawn("q", q_cmd, stdin=upstream, stdout=subprocess.DEVNULL)
                except OSError as exc:
                    upstream.close()
                    return setup_failure("q", exc)
                upstream.close()
                error = _supervise(stages, final=q, timeout=fx_env.fx_render_timeout(out_duration),
                                   should_cancel=should_cancel)
                timings.update({s.name: s.elapsed_ms() for s in stages})
                if error is not None:
                    return _failure(result, error, stages, p2_reader, face_track)
                upstream = None

        video_input = raw_input if upstream is not None else ["-i", str(mov_path)]
        p3_cmd = build_p3_command(
            video_input=video_input, input_path=input_path, bus_path=bus_path, subs=subs, audio_graph=audio_graph,
            encode_args=encode_args, out_frames=out_frames, output_path=output_path,
        )
        try:
            p3 = spawn("p3", p3_cmd, stdin=upstream if upstream is not None else subprocess.DEVNULL,
                       stdout=subprocess.PIPE)
        except OSError as exc:
            if upstream is not None:
                upstream.close()
            return setup_failure("p3", exc)
        if upstream is not None:
            upstream.close()
        progress = _Progress(p3.proc.stdout, out_frames, on_progress)
        progress.start()
        threads.append(progress)

        live = [s for s in stages if s.name != "q"]
        elapsed_before = time.monotonic() - started
        error = _supervise(
            live, final=p3, timeout=max(1.0, fx_env.fx_render_timeout(out_duration) - elapsed_before),
            should_cancel=should_cancel,
        )
        for thread in threads:
            thread.join(timeout=5)
        for stage in live:
            timings[stage.name] = stage.elapsed_ms()
        if p2_reader is not None and p2_reader.stats:
            timings["p2_draw"] = float(p2_reader.stats.get("ms_draw", 0) or 0)
            timings["p2_passthrough"] = float(p2_reader.stats.get("passthrough", 0) or 0)
        if error is not None:
            return _failure(result, error, stages, p2_reader, face_track)

        probe = probe_output(output_path)
        if probe is None:
            return result(False, "output: unreadable", face=face_track)
        frames = probe["frames"]
        # Full: P2 writes exactly outFrames. Safe: P1 feeds P3 directly, so a source a frame short of the plan
        # yields one frame less (the audio is still bounded at out_duration: A/V stays within a frame).
        if frames != out_frames and not (not full and out_frames - 1 <= frames <= out_frames):
            return result(False, f"output: {frames} frames, expected {out_frames}", face=face_track)
        if (probe["width"], probe["height"]) != (out_w, out_h):
            return result(False, f"output: {probe['width']}x{probe['height']}, expected {out_w}x{out_h}",
                          face=face_track)
        if on_progress is not None:
            try:
                on_progress(1.0)
            except Exception:  # noqa: BLE001
                pass
        if p2_reader is not None and p2_reader.warnings:
            logger.info("fx_chain_p2_warnings %s", "; ".join(p2_reader.warnings[:5]))
        res = result(True, face=face_track, fingerprint=fingerprint)
        logger.info(
            "fx_chain level=%s frames=%s warp=%s cues=%s timings=%s library=%s",
            res.level, out_frames, bool(applied_warp), len(plan.cues), json.dumps(timings), fingerprint[:12],
        )
        return res
    except Exception as exc:  # noqa: BLE001 - a render failure is a result, not an exception (§6.6 ladder)
        _kill_all(stages)
        logger.exception("fx_chain_failed")
        return result(False, f"setup: {exc}", face=face_track)
    finally:
        _kill_all(stages)
        for stage in stages:
            for stream in (stage.proc.stdout, stage.proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        shutil.rmtree(work, ignore_errors=True)


def _as_output_track(track: dict | None) -> dict | None:
    """A track computed by the chain's own analysis decoded the head's output: it is already in output space."""
    if not track:
        return track
    return {**track, "space": "output"}


def _failure(result: Callable[..., FxResult], error: str, stages: Sequence[_Stage], p2_reader: _P2Stderr | None,
             face_track: dict | None) -> FxResult:
    """Attach the most useful stderr to a failed run and log it once."""
    detail = error
    name = error.split(":", 1)[0]
    if name == "p2" and p2_reader is not None:
        p2_reader.join(timeout=2)
        detail = f"{error}: {p2_reader.tail()}"
    else:
        stage = next((s for s in stages if s.name == name), None)
        if stage is not None and stage.stderr_path is not None:
            detail = f"{error}: {_tail(stage.stderr_path)}"
    detail = detail[:STDERR_TAIL_CHARS]
    if error == "cancelled":
        logger.info("fx_chain_cancelled")
    else:
        logger.warning("fx_chain_failed %s", detail)
    return result(False, detail, face=face_track)
