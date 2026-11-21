"""Dynamic-edits audio: the premixed FX bus and the P3 audio graph (§9).

Two pieces, both in the render's own scratch directory (no shared disk):

- ``build_bus`` premixes SFX, the bleep tone and the ducked music bed into one ``fxbus.wav`` with numpy, entirely on
  the **F** clock (final seconds, after the time warp). Drop-in SFX and beds are resolved here, at mix time
  (``assets.mix_sfx_file`` / ``assets.mix_bed_file``), never in the resolver.
- ``audio_graph`` is the pure P3 ``filter_complex`` that turns the source voice into the final mix: bleep mutes in **E**
  at 1 ms resolution, the studio volume, the warp pieces (``atrim``/``atempo`` per linear piece, silence per hold),
  ``apad`` to the probed length, the optional loop crossfade, the loudness stage and ``alimiter latency=1``.

P3's inputs are constant: ``0`` rawvideo (P2), ``1`` the source clip (the voice), ``2`` the bus when there is one.
Every label added here is prefixed ``fxa_``. Every filter used exists and behaves the same on ffmpeg 6.1 and 7.1:
``asetnsamples``, ``alimiter latency``, ``apad whole_dur``, ``adelay all``, ``afade``, ``amix normalize``, ``ebur128``.

Loudness (``FxRequest.loudness``):

- ``static`` (generation): ``g = clamp(-14 - I_voice, -20, +20)`` dB on the voice only before ``amix``, so SFX and
  music keep fixed levels relative to the normalised voice (D8). ``I_voice`` comes from one audio-only ``measure_voice_lufs`` pass (voice after bleeps, before the warp). Duck depths stay exact.
- ``dynamic`` (``FX_LOUDNORM=dynamic``): the legacy one-pass ``loudnorm`` after ``alimiter``.
- ``none`` (studio exports): no gain stage, ``alimiter`` only, like the browser export.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import wave
from pathlib import Path
from typing import Iterable, Literal, Sequence

import numpy as np

from ..edit_fx import assets
from ..edit_fx.contracts import BleepCue, FxPlan, SfxCue
from ..edit_fx.duck import duck_envelope, speech_spans
from ..edit_fx.resolver import sfx_bus_gain_db
from ..edit_fx.warp import Warp
from .common import LOUDNORM_FILTER

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
TARGET_LUFS = -14.0
STATIC_GAIN_LIMIT_DB = 20.0
SILENT_LUFS = -69.0                  # ebur128 reports -70.0 LUFS (the absolute gate) for silence

BLEEP_TONE_HZ = 1000.0
BLEEP_TONE_DBFS = -12.0
BLEEP_TONE_RAMP = 0.005
TICKER_FADE = 0.030
BED_SEAM_CROSSFADE = 0.050
LOOP_CROSSFADE_SECONDS = 0.4         # the bus and voice seam with loop_ending == "crossfade" (§6.5 item 8)
SOFT_CLIP_KNEE = 0.8

MUTE_FRAME_SAMPLES = 48              # 1 ms frames at 48 kHz: volume's `enable` is evaluated per frame (AR-M1)
MAX_ENABLE_TERMS = 40                # between() terms per volume filter; more spans chain more filters
ATEMPO_RANGE = (0.5, 2.0)
LIMITER = "alimiter=limit=0.82:attack=5:release=50:level=disabled:latency=1"   # -1.7 dBFS: true peak stays under
                                                                                  # -1 dBTP after AAC; latency=1: no 5 ms lag (AR-M2)
VOICE_LABEL = "fxa_voice0"
BUS_INPUT_INDEX = 2

AudioOutput = Literal["source", "silent", "none"]

_INTEGRATED = re.compile(r"Integrated loudness:\s*I:\s*(-?(?:\d+(?:\.\d+)?|inf)|nan)\s*LUFS")


# ----------------------------------------------------------------------------- small helpers


def _num(value: float) -> str:
    """A filter argument: fixed six decimals without trailing zeros (``1.5``, ``0``, ``-3.25``)."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def static_gain_db(voice_lufs: float | None) -> float:
    """The static loudness gain (§9 ``static``): ``clamp(-14 - I_voice, -20, +20)``; 0 dB when unmeasurable."""
    if voice_lufs is None or not math.isfinite(voice_lufs):
        return 0.0
    return max(-STATIC_GAIN_LIMIT_DB, min(STATIC_GAIN_LIMIT_DB, TARGET_LUFS - voice_lufs))


def audio_output(has_audio: bool, has_bus: bool) -> AudioOutput:
    """How P3 gets its voice (the ``-an`` decision, AR-m6).

    ``source``: the clip's own audio. ``silent``: no source audio but a bus, so the voice is
    ``silent_voice_fragment`` and the bus carries the mix. ``none``: no source audio and no bus, so P3 keeps ``-an``
    exactly as the legacy render does.
    """
    if has_audio:
        return "source"
    return "silent" if has_bus else "none"


def silent_voice_fragment(edit_duration: float) -> str:
    """The voice for a silent source with a bus: ``anullsrc`` of ``edit_duration`` (E) seconds → ``[fxa_voice0]``."""
    return f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=0:{_num(max(0.0, edit_duration))}[{VOICE_LABEL}]"


def bleep_spans_e(plan: FxPlan) -> list[tuple[float, float]]:
    """E spans the voice is muted over: every bleep cue (``tone=False`` cues mute without a tone)."""
    return sorted((c.start, c.end) for c in plan.cues if isinstance(c, BleepCue) and c.end > c.start)


def _mute_filters(bleeps_e: Iterable[tuple[float, float]]) -> list[str]:
    """``volume=0`` filters enabled over the bleep spans, ≤40 ``between`` terms each.

    The start is floored and the end ceiled to the 1 ms frame grid, so a mute begins within one frame of the
    requested sample and never lets a fragment of the word through at either edge.
    """
    spans = []
    for start, end in sorted(bleeps_e):
        a = math.floor(max(0.0, float(start)) * 1000.0) / 1000.0
        b = math.ceil(max(0.0, float(end)) * 1000.0) / 1000.0
        if b > a:
            spans.append((a, b))
    terms = [f"between(t,{_num(a)},{_num(b)})" for a, b in spans]
    return [
        f"volume=0:enable='{'+'.join(terms[i:i + MAX_ENABLE_TERMS])}'"
        for i in range(0, len(terms), MAX_ENABLE_TERMS)
    ]


def _voice_head(bleeps_e: Sequence[tuple[float, float]]) -> list[str]:
    """48 kHz stereo in 1 ms frames, then the bleep mutes (E time)."""
    return [
        f"aresample={SAMPLE_RATE}",
        "aformat=sample_fmts=fltp:channel_layouts=stereo",
        f"asetnsamples=n={MUTE_FRAME_SAMPLES}:p=0",
        *_mute_filters(bleeps_e),
    ]


def _rename_voice_output(voice_fragment: str, label: str) -> str:
    fragment = voice_fragment.strip().rstrip(";")
    suffix = f"[{VOICE_LABEL}]"
    if not fragment.endswith(suffix):
        raise ValueError(f"voice_fragment must end in {suffix}")
    return fragment[: -len(suffix)] + f"[{label}]"


def measurement_fragment(voice_fragment: str, bleeps_e: Sequence[tuple[float, float]]) -> str:
    """``voice_fragment`` with the bleep mutes applied, still producing ``[fxa_voice0]``.

    Pass it to ``measure_voice_lufs`` so the static gain is measured on the voice after bleeps (§9).
    """
    if not bleeps_e:
        return voice_fragment
    raw = _rename_voice_output(voice_fragment, "fxa_vmeas")
    return f"{raw};[fxa_vmeas]{','.join(_voice_head(bleeps_e))}[{VOICE_LABEL}]"


def _atempo_chain(rate: float) -> list[str]:
    """``atempo`` for ``rate`` (edit seconds per final second).

    Rates outside 0.5–2.0 chain equal factors that each stay inside it: two for the cue range 0.25–4.0, more for the
    rare quantised piece just past it (a 5-frame span at 4× fits one frame, so its actual rate is 5).
    """
    if rate <= 0 or abs(rate - 1.0) <= 1e-9:
        return []
    low, high = ATEMPO_RANGE
    stages = 1
    while not low - 1e-9 <= rate ** (1.0 / stages) <= high + 1e-9:
        stages += 1
    factor = rate ** (1.0 / stages)
    return [f"atempo={_num(factor)}"] * stages


def _samples(seconds: float) -> int:
    return int(round(seconds * SAMPLE_RATE))


# ----------------------------------------------------------------------------- P3 audio graph


def audio_graph(*, voice_fragment: str, bleeps_e: list[tuple[float, float]], warp: Warp, out_duration: float,
                has_bus: bool, voice_gain: float, loudness: str, static_gain_db: float | None,
                loop_crossfade: float, mix_gain_db: float | None = None) -> str:
    """The P3 audio ``filter_complex`` (§9), ending in ``[fxa_out]``. Pure: no I/O.

    ``voice_fragment`` produces ``[fxa_voice0]`` (from input 1, or ``silent_voice_fragment``). ``bleeps_e`` are E
    spans. ``warp`` maps E to F; an identity warp adds no filters. ``out_duration`` is the final (F) length the voice
    is padded and trimmed to. ``has_bus`` reads input 2 as the premixed bus. ``loop_crossfade`` > 0 crossfades the
    voice's last seconds with its own head (``loop_ending == "crossfade"``).
    """
    if loudness not in ("static", "dynamic", "none"):
        raise ValueError(f"unknown loudness mode {loudness!r}")
    out = max(0.0, float(out_duration))
    out_s = _num(out)
    parts: list[str] = [voice_fragment.strip().rstrip(";")]

    head = _voice_head(bleeps_e)
    if abs(float(voice_gain) - 1.0) > 1e-9:
        head.append(f"volume={_num(max(0.0, float(voice_gain)))}")
    pad = f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur={out_s},atrim=0:{out_s}"

    xf = min(max(0.0, float(loop_crossfade)), out / 2.0)
    voice_label = "fxa_vpre" if xf > 0 else "fxa_voice"

    if not warp.nontrivial:
        parts.append(f"[{VOICE_LABEL}]{','.join(head + [pad])}[{voice_label}]")
    else:
        parts.append(f"[{VOICE_LABEL}]{','.join(head)}[fxa_vb]")
        linear = [p for p in warp.pieces if not p.is_hold]
        if linear:
            outs = "".join(f"[fxa_ws{i}]" for i in range(len(linear)))
            parts.append(f"[fxa_vb]asplit={len(linear)}{outs}")
        else:
            parts.append("[fxa_vb]anullsink")
        pieces: list[str] = []
        split = 0
        for index, piece in enumerate(warp.pieces):
            label = f"fxa_wp{index}"
            # Every piece is cut to its exact F length in samples (cumulative rounding, so no drift): atempo's output
            # is off by up to tens of ms (55 ms for a 1 s span at 0.25×), which would slide every later piece off
            # the picture; a source that ends early is padded so the pieces after it stay aligned.
            n = max(1, _samples(piece.o0 + piece.length) - _samples(piece.o0))
            if piece.is_hold:
                parts.append(
                    f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=end_sample={n},"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[{label}]"
                )
            else:
                chain = [f"atrim={_num(piece.e0)}:{_num(piece.e1)}", "asetpts=N/SR/TB",
                         *_atempo_chain(piece.rate), f"apad=whole_len={n}", f"atrim=end_sample={n}"]
                parts.append(f"[fxa_ws{split}]{','.join(chain)}[{label}]")
                split += 1
            pieces.append(f"[{label}]")
        parts.append(f"{''.join(pieces)}concat=n={len(pieces)}:v=0:a=1,{pad}[{voice_label}]")

    if xf > 0:
        xf_s = _num(xf)
        delay = int(round((out - xf) * SAMPLE_RATE))
        parts += [
            "[fxa_vpre]asplit[fxa_vm][fxa_vh]",
            f"[fxa_vh]atrim=0:{xf_s},asetpts=N/SR/TB,afade=t=in:d={xf_s},adelay={delay}S:all=1[fxa_vhd]",
            f"[fxa_vm]afade=t=out:st={_num(out - xf)}:d={xf_s}[fxa_vmt]",
            "[fxa_vmt][fxa_vhd]amix=inputs=2:duration=first:normalize=0[fxa_voice]",
        ]

    voice, bus = "fxa_voice", "fxa_bus"
    if has_bus:
        parts.append(f"[{BUS_INPUT_INDEX}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[fxa_bus]")
    if loudness == "static":
        # The static gain normalises the voice only (D8): SFX and music keep their fixed levels relative to the
        # normalised voice instead of rising with a quiet source.
        gain = _num(static_gain_db or 0.0)
        parts.append(f"[fxa_voice]volume={gain}dB[fxa_v2]")
        voice = "fxa_v2"

    tail = [LIMITER] + ([LOUDNORM_FILTER] if loudness == "dynamic" else [])
    if loudness == "static" and has_bus and mix_gain_db is not None and abs(float(mix_gain_db)) > 1e-6:
        # The bus adds loudness on top of the normalised voice: bring the whole mix back to the target (D8 keeps the
        # SFX and music levels relative to the voice; this moves all three together).
        tail = [f"volume={_num(mix_gain_db)}dB", *tail]
    if has_bus:
        mix = "amix=inputs=2:duration=first:normalize=0:dropout_transition=0"
        parts.append(f"[{voice}][{bus}]{mix},{','.join(tail)}[fxa_out]")
    else:
        parts.append(f"[{voice}]{','.join(tail)}[fxa_out]")
    return ";".join(parts)


# ----------------------------------------------------------------------------- loudness measurement


def measure_voice_lufs(input_path: Path, voice_fragment: str, *, duration: float) -> float | None:
    """Integrated loudness (LUFS, gated) of the voice over ``[0, duration]`` E seconds: one audio-only ffmpeg pass.

    ``voice_fragment`` reads input index 1, as in P3 (use ``measurement_fragment`` to include the bleep mutes).
    None when the voice is silent or cannot be measured; the caller then applies 0 dB.
    """
    if duration <= 0:
        return None
    meter = f"[{VOICE_LABEL}]atrim=0:{_num(duration)},ebur128=framelog=verbose[fxa_meter]"
    graph = f"{voice_fragment.strip().rstrip(';')};{meter}"
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info",
        "-i", str(input_path), "-i", str(input_path),
        "-filter_complex", graph, "-map", "[fxa_meter]", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=max(120.0, 2.0 * duration))
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("fx_voice_lufs_failed: %s", exc)
        return None
    if proc.returncode != 0:
        logger.warning("fx_voice_lufs_failed: exit %s: %s", proc.returncode, proc.stderr[-1500:])
        return None
    matches = _INTEGRATED.findall(proc.stderr)
    if not matches:
        logger.warning("fx_voice_lufs_failed: no ebur128 summary")
        return None
    try:
        value = float(matches[-1])
    except ValueError:
        return None
    if not math.isfinite(value) or value <= SILENT_LUFS:
        return None
    return value


MIX_GAIN_RANGE = (-12.0, 6.0)


def measure_mix_lufs(input_path: Path, voice_fragment: str, bus_path: Path, *, voice_gain_db: float,
                     duration: float) -> float | None:
    """Integrated loudness of the voice (after ``voice_gain_db``) mixed with the bus, as P3 mixes them before the
    limiter. The warp is not applied (the auto plans carry none); with one the result is an estimate. None when it
    cannot be measured."""
    if duration <= 0:
        return None
    graph = (
        f"{voice_fragment.strip().rstrip(';')};"
        f"[{VOICE_LABEL}]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=0:{_num(duration)},volume={_num(voice_gain_db)}dB[fxa_mv];"
        f"[{BUS_INPUT_INDEX}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[fxa_mb];"
        "[fxa_mv][fxa_mb]amix=inputs=2:duration=first:normalize=0:dropout_transition=0,"
        "ebur128=framelog=verbose[fxa_meter]"
    )
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info",
        "-i", str(input_path), "-i", str(input_path), "-i", str(bus_path),
        "-filter_complex", graph, "-map", "[fxa_meter]", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=max(120.0, 2.0 * duration))
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("fx_mix_lufs_failed: %s", exc)
        return None
    matches = _INTEGRATED.findall(proc.stderr) if proc.returncode == 0 else []
    if not matches:
        logger.warning("fx_mix_lufs_failed: exit %s", proc.returncode)
        return None
    try:
        value = float(matches[-1])
    except ValueError:
        return None
    return value if math.isfinite(value) and value > SILENT_LUFS else None


def mix_gain_db(mix_lufs: float | None) -> float:
    """Gain that brings the measured mix to TARGET_LUFS, within MIX_GAIN_RANGE; 0 when unmeasured."""
    if mix_lufs is None or not math.isfinite(mix_lufs):
        return 0.0
    return max(MIX_GAIN_RANGE[0], min(MIX_GAIN_RANGE[1], TARGET_LUFS - mix_lufs))


# ----------------------------------------------------------------------------- fxbus.wav


def _decode(path: Path, sample_rate: int) -> np.ndarray:
    """Any audio file → float32 ``(n, 2)`` at ``sample_rate``. 16-bit PCM WAV at that rate is read without ffmpeg."""
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getframerate() == sample_rate and handle.getsampwidth() == 2 and handle.getnchannels() in (1, 2):
                channels = handle.getnchannels()
                raw = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
                data = raw.reshape(-1, channels)
                return np.repeat(data, 2, axis=1) if channels == 1 else data
    except (wave.Error, EOFError, OSError):
        pass
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:a:0",
         "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2", "-ar", str(sample_rate), "-"],
        capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"decode failed for {path.name}: {proc.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(proc.stdout, dtype="<f4").reshape(-1, 2).copy()


def _db(value: float) -> float:
    return float(10.0 ** (value / 20.0))


def _add(bus: np.ndarray, signal: np.ndarray, at: int) -> bool:
    """Sum ``signal`` into ``bus`` starting at sample ``at`` (negative = the head is cut off). False if nothing fits."""
    n = bus.shape[0]
    src0 = max(0, -at)
    dst0 = max(0, at)
    length = min(signal.shape[0] - src0, n - dst0)
    if length <= 0:
        return False
    bus[dst0:dst0 + length] += signal[src0:src0 + length]
    return True


def _ticker_end_e(cue: SfxCue, plan: FxPlan) -> float | None:
    """The end of a counter's count span (E): the lock sound beside the ticker, else the counter accent's lock.

    The resolver emits ``<accent>.sfx`` (ticker at the count start) and ``<accent>.sfx2`` (stamp at the lock); the
    counter accent itself ends 0.9 s after the lock. Mapped ids keep their ``@k`` suffix on every member.
    """
    base, _, repeat = cue.id.partition("@")
    if not base.endswith(".sfx"):
        return None
    accent_id = base[: -len(".sfx")]
    suffix = f"@{repeat}" if repeat else ""
    by_id = {c.id: c for c in plan.cues}
    lock = by_id.get(f"{accent_id}.sfx2{suffix}")
    if lock is not None and lock.start > cue.start:
        return lock.start
    accent = by_id.get(f"{accent_id}{suffix}")
    if accent is not None and accent.kind == "accent" and accent.variant == "counter":
        return max(cue.start, accent.end - 0.9)
    return None


def _mix_sfx(bus: np.ndarray, plan: FxPlan, warp: Warp, sample_rate: int) -> bool:
    bus_gain = sfx_bus_gain_db(plan.style, plan.sensitivity)
    cache: dict[Path, np.ndarray] = {}
    mixed = False
    for cue in plan.cues:
        if not isinstance(cue, SfxCue):
            continue
        try:
            path, norm_db = assets.mix_sfx_file(cue.sound, cue.variant, cue.seed)
            if path not in cache:
                cache[path] = _decode(path, sample_rate)
            signal = cache[path]
        except (KeyError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            logger.warning("fx_bus_sfx_skipped cue=%s sound=%s: %s", cue.id, cue.sound, exc)
            continue
        start_f = warp.to_output(cue.start)
        if cue.sound == "ticker":
            end_e = _ticker_end_e(cue, plan)
            if end_e is not None:
                keep = int(round((warp.to_output(end_e) - start_f) * sample_rate))
                if 0 < keep < signal.shape[0]:
                    signal = signal[:keep].copy()
                    fade = min(keep, int(round(TICKER_FADE * sample_rate)))
                    signal[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)[:, None]
        gain = _db(cue.gain_db + norm_db + bus_gain)
        mixed |= _add(bus, signal * np.float32(gain), int(round(start_f * sample_rate)))
    return mixed


def _mix_bleep_tones(bus: np.ndarray, plan: FxPlan, warp: Warp, sample_rate: int) -> bool:
    amplitude = _db(BLEEP_TONE_DBFS)
    ramp = max(1, int(round(BLEEP_TONE_RAMP * sample_rate)))
    mixed = False
    for cue in plan.cues:
        if not isinstance(cue, BleepCue) or not cue.tone:
            continue
        a = int(round(warp.to_output(cue.start) * sample_rate))
        b = int(round(warp.to_output(cue.end) * sample_rate))
        if b <= a:
            continue
        t = np.arange(b - a, dtype=np.float64) / sample_rate
        tone = amplitude * np.sin(2.0 * math.pi * BLEEP_TONE_HZ * t)
        edge = min(ramp, (b - a) // 2)
        if edge > 0:
            shape = np.linspace(0.0, 1.0, edge, endpoint=False)
            tone[:edge] *= shape
            tone[-edge:] *= shape[::-1]
        mixed |= _add(bus, np.repeat(tone.astype(np.float32)[:, None], 2, axis=1), a)
    return mixed


def _builtin_loop(path: Path, sample_rate: int) -> tuple[int, int] | None:
    """(start, length) in samples of a built-in bed's loop (manifest ``loop_start``, ``loop_seconds``), None for a
    drop-in.

    A built-in file carries a little of its loop's own audio before and after it, so the encoder's smeared edges are
    never played; the decoded bed is cut to exactly the loop before it is tiled, otherwise every seam would click
    and drift off the resolver's ``offset``.
    """
    try:
        resolved = path.resolve()
        resolved.relative_to(assets.builtin_dir().resolve())
    except (OSError, ValueError):
        return None
    index = assets.load_index()
    for bed_id, bed_path in index.bed_files.items():
        if bed_path.resolve() == resolved:
            seconds = float(index.bed_lengths.get(bed_id, 0.0) or 0.0)
            start = int(round(float(index.bed_starts.get(bed_id, 0.0) or 0.0) * sample_rate))
            return (start, int(round(seconds * sample_rate))) if seconds > 0 else None
    return None


def _looped(bed: np.ndarray, start: int, count: int, seamless: bool, sample_rate: int) -> np.ndarray:
    """``count`` samples of the bed looped forever, starting ``start`` samples in.

    Built-in beds are rendered circularly (their end flows into their start), so they tile exactly at their loop
    length, which is what the resolver's ``offset`` and the browser preview assume. Drop-ins are joined with a 50 ms
    equal-power crossfade at each seam.
    """
    length = bed.shape[0]
    xf = int(round(BED_SEAM_CROSSFADE * sample_rate))
    if seamless or length <= 2 * xf:
        index = (start + np.arange(count)) % length
        return bed[index]
    theta = np.linspace(0.0, math.pi / 2.0, xf, dtype=np.float32)[:, None]
    head_in = bed[:xf] * np.sin(theta)
    period = bed[xf:].copy()                           # every pass after the first starts xf in
    start = start % length
    chunks = [bed]
    total = length
    while total < start + count:
        prev_tail = chunks[-1][-xf:] * np.cos(theta)
        chunks[-1] = chunks[-1][:-xf]
        chunks.append(np.concatenate([prev_tail + head_in, period]))
        total += length - xf
    return np.concatenate(chunks)[start:start + count]


def _mix_music(bus: np.ndarray, plan: FxPlan, words_e: Sequence[dict], warp: Warp, out_duration: float,
               sample_rate: int) -> bool:
    bed = plan.music
    if bed is None:
        return False
    try:
        path, norm_db = assets.mix_bed_file(bed.bed, bed.mood, plan.seed)
        audio = _decode(path, sample_rate)
    except (KeyError, OSError, RuntimeError, subprocess.SubprocessError, ValueError, StopIteration) as exc:
        logger.warning("fx_bus_music_skipped bed=%s: %s", bed.bed, exc)
        return False
    loop = _builtin_loop(path, sample_rate)
    if loop:
        audio = audio[loop[0]:loop[0] + loop[1]]
    if audio.shape[0] == 0:
        return False
    n = bus.shape[0]
    music = _looped(audio, int(round(bed.offset * sample_rate)), n, loop is not None, sample_rate)

    words_f = []
    for word in words_e:
        try:
            start, end = float(word["start"]), float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            words_f.append((warp.to_output(start), warp.to_output(end)))
    points = duck_envelope(
        speech_spans(words_f), gain_db=bed.gain_db, duck_db=bed.duck_db, attack=bed.attack, release=bed.release,
        fade_in=bed.fade_in, fade_out=bed.fade_out, out_duration=out_duration,
    )
    times = np.array([p[0] for p in points], dtype=np.float64)
    levels = np.array([p[1] for p in points], dtype=np.float64)
    envelope_db = np.interp(np.arange(n, dtype=np.float64) / sample_rate, times, levels) + norm_db
    gain = np.power(10.0, envelope_db / 20.0).astype(np.float32)
    bus += music * gain[:, None]
    return True


def _loop_seam(bus: np.ndarray, seconds: float, sample_rate: int) -> None:
    """Equal-power crossfade of the bus's last ``seconds`` with its own first ``seconds`` (AP-M3, AR-m3)."""
    n = bus.shape[0]
    x = min(int(round(seconds * sample_rate)), n // 2)
    if x <= 0:
        return
    theta = np.linspace(0.0, math.pi / 2.0, x, dtype=np.float32)[:, None]
    bus[n - x:] = bus[n - x:] * np.cos(theta) + bus[:x] * np.sin(theta)


def _soft_clip(bus: np.ndarray) -> None:
    """``x`` below 0.8, else ``sign·(0.8 + 0.2·tanh((|x| − 0.8) / 0.2))``: never above 1.0, no hard edges."""
    magnitude = np.abs(bus)
    over = magnitude >= SOFT_CLIP_KNEE
    if over.any():
        knee = SOFT_CLIP_KNEE
        bus[over] = np.sign(bus[over]) * (knee + (1 - knee) * np.tanh((magnitude[over] - knee) / (1 - knee)))


def _write_wav(path: Path, bus: np.ndarray, sample_rate: int) -> None:
    pcm = np.clip(np.round(bus * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def build_bus(plan: FxPlan, *, words_e: list[dict], warp: Warp, out_duration: float, path: Path,
              sample_rate: int = 48000) -> bool:
    """Premix SFX, bleep tones and the ducked music bed into ``path`` (48 kHz stereo 16-bit WAV, F time).

    ``plan`` cues and ``words_e`` are on the E clock; ``warp`` maps them to F. SFX gain = cue ``gain_db`` + the file's
    normalisation gain + the intensity bus gain (§8.3). Music is not warped: it plays from ``offset`` on the F clock
    and is ducked under the speech spans of the warped words. Returns False (and writes nothing) when there is nothing
    to mix. A missing or undecodable asset is skipped with a warning: decorative audio never costs the clip.
    """
    count = int(round(max(0.0, float(out_duration)) * sample_rate))
    if count <= 0:
        return False
    bus = np.zeros((count, 2), dtype=np.float32)
    mixed = _mix_sfx(bus, plan, warp, sample_rate)
    mixed = _mix_bleep_tones(bus, plan, warp, sample_rate) or mixed
    mixed = _mix_music(bus, plan, words_e, warp, out_duration, sample_rate) or mixed
    if not mixed:
        return False
    if plan.loop_ending == "crossfade":
        _loop_seam(bus, LOOP_CROSSFADE_SECONDS, sample_rate)
    _soft_clip(bus)
    _write_wav(Path(path), bus, sample_rate)
    return True
