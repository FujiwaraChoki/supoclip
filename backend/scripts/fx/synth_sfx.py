#!/usr/bin/env python3
"""Synthesised dynamic-edit sound effects: a code-only fallback set.

The built-in SFX in src/fx_assets/sfx are CC0 recordings from Freesound (see SOURCES-CC0.txt and
write_manifest.py); this script no longer writes there. It is kept as a fallback for anyone who wants sounds made
entirely from code: it writes one file per recipe under drop-in names (``<sound>-synth<variant>.wav``), so pointing
FX_LIBRARY_DIR at the parent of ``--out`` makes the mixer use them instead of the built-ins.

Every file is made from code: numpy oscillators, noise and filters, a fixed
`np.random.default_rng(<seed>)` per file, peak-normalised to -6 dBFS with 5 ms
edge fades and written as 48 kHz mono 16-bit WAV with the stdlib `wave` module.
Nothing is sampled from a recording, so the files are CC0.

    cd backend && .venv/bin/python scripts/fx/synth_sfx.py --out fx_library/sfx [--only riser_0 ...]

The output is deterministic: the same numpy build writes the same bytes.
"""

from __future__ import annotations

import argparse
import math
import sys
import wave
from pathlib import Path
from typing import Callable

import numpy as np

SR = 48_000
PEAK_DBFS = -6.0
EDGE_FADE = 0.005
MAX_SECONDS = 2.5



# ----------------------------------------------------------------------------- DSP helpers


def seconds(n: float) -> int:
    return int(round(n * SR))


def time_axis(duration: float) -> np.ndarray:
    return np.arange(seconds(duration)) / SR


def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def phase_from_freq(freq: np.ndarray) -> np.ndarray:
    """Integrated phase (radians) of an instantaneous frequency curve in Hz."""
    return 2.0 * np.pi * np.cumsum(freq) / SR


def poly_blep(t: np.ndarray, dt: np.ndarray) -> np.ndarray:
    """PolyBLEP residual for a band-limited saw. t is phase in [0, 1), dt the phase step."""
    out = np.zeros_like(t)
    dt = np.maximum(dt, 1e-9)
    lo = t < dt
    x = t[lo] / dt[lo]
    out[lo] = x + x - x * x - 1.0
    hi = t > 1.0 - dt
    x = (t[hi] - 1.0) / dt[hi]
    out[hi] = x * x + x + x + 1.0
    return out


def saw(freq: np.ndarray | float, n: int | None = None, phase0: float = 0.0) -> np.ndarray:
    """Band-limited (PolyBLEP) sawtooth in [-1, 1] following `freq` (Hz, scalar or per sample)."""
    if np.isscalar(freq):
        freq = np.full(int(n or 0), float(freq))
    freq = np.asarray(freq, dtype=np.float64)
    dt = freq / SR
    phase = (phase0 + np.cumsum(dt) - dt[0]) % 1.0 if len(dt) else dt
    return 2.0 * phase - 1.0 - poly_blep(phase, dt)


def pink_noise(rng: np.random.Generator, n: int) -> np.ndarray:
    """1/f noise by spectral shaping of white noise."""
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    f[0] = f[1] if len(f) > 1 else 1.0
    spec /= np.sqrt(f)
    out = np.fft.irfft(spec, n)
    return out / (np.max(np.abs(out)) + 1e-12)


def fft_filter(x: np.ndarray, response: Callable[[np.ndarray], np.ndarray], *, circular: bool = False) -> np.ndarray:
    """Zero-phase filtering by a magnitude response (Hz → gain). Linear unless `circular` (loops)."""
    n = len(x)
    size = n if circular else 1 << int(math.ceil(math.log2(max(2, 2 * n))))
    spec = np.fft.rfft(x, size)
    spec *= response(np.fft.rfftfreq(size, 1.0 / SR))
    return np.fft.irfft(spec, size)[:n]


def lowpass_response(cutoff: float, order: int = 2) -> Callable[[np.ndarray], np.ndarray]:
    return lambda f: 1.0 / np.sqrt(1.0 + (f / cutoff) ** (2 * order))


def highpass_response(cutoff: float, order: int = 2) -> Callable[[np.ndarray], np.ndarray]:
    def resp(f: np.ndarray) -> np.ndarray:
        safe = np.maximum(f, 1e-6)
        return 1.0 / np.sqrt(1.0 + (cutoff / safe) ** (2 * order))

    return resp


def bandpass_response(low: float, high: float, order: int = 2) -> Callable[[np.ndarray], np.ndarray]:
    lp, hp = lowpass_response(high, order), highpass_response(low, order)
    return lambda f: lp(f) * hp(f)


def stft_filter(
    x: np.ndarray,
    gain: Callable[[np.ndarray, float], np.ndarray],
    *,
    frame: int = 2048,
    hop: int = 256,
) -> np.ndarray:
    """Time-varying zero-phase filter: gain(freqs, t_seconds) per STFT frame, Hann overlap-add."""
    n = len(x)
    pad = frame
    padded = np.concatenate([np.zeros(pad), x, np.zeros(pad + frame)])
    window = np.hanning(frame + 1)[:-1]
    freqs = np.fft.rfftfreq(frame, 1.0 / SR)
    out = np.zeros_like(padded)
    norm = np.zeros_like(padded)
    for start in range(0, len(padded) - frame, hop):
        seg = padded[start : start + frame] * window
        t = (start + frame / 2 - pad) / SR
        spec = np.fft.rfft(seg) * gain(freqs, t)
        out[start : start + frame] += np.fft.irfft(spec, frame) * window
        norm[start : start + frame] += window * window
    out /= np.maximum(norm, 1e-8)
    return out[pad : pad + n]


def log_bell(freqs: np.ndarray, centre: float, octaves: float) -> np.ndarray:
    """Band-pass bell on a log-frequency axis (Gaussian, `octaves` = one standard deviation)."""
    safe = np.maximum(freqs, 1.0)
    return np.exp(-0.5 * (np.log2(safe / centre) / octaves) ** 2)


def svf_bandpass(x: np.ndarray, centre: np.ndarray, q: float) -> np.ndarray:
    """Resonant TPT state-variable band-pass with a per-sample centre frequency (Hz)."""
    out = np.empty_like(x)
    g_all = np.tan(np.pi * np.clip(centre, 20.0, SR * 0.45) / SR)
    k = 1.0 / q
    ic1 = ic2 = 0.0
    for i in range(len(x)):
        g = g_all[i]
        a1 = 1.0 / (1.0 + g * (g + k))
        v3 = x[i] - ic2
        v1 = a1 * ic1 + g * a1 * v3
        v2 = ic2 + g * v1
        ic1 = 2.0 * v1 - ic1
        ic2 = 2.0 * v2 - ic2
        out[i] = v1
    return out


def exp_env(n: int, decay: float, attack: float = 0.0) -> np.ndarray:
    """Exponential decay (time constant `decay` s) with an optional linear attack."""
    t = np.arange(n) / SR
    env = np.exp(-t / max(decay, 1e-6))
    if attack > 0:
        a = seconds(attack)
        env[:a] *= np.linspace(0.0, 1.0, a, endpoint=False)
    return env


def fit(x: np.ndarray, n: int) -> np.ndarray:
    out = np.zeros(n)
    m = min(n, len(x))
    out[:m] = x[:m]
    return out


def place(buf: np.ndarray, sound: np.ndarray, at: int) -> None:
    end = min(len(buf), at + len(sound))
    if end > at:
        buf[at:end] += sound[: end - at]


def edge_fades(x: np.ndarray, fade: float = EDGE_FADE) -> np.ndarray:
    n = min(seconds(fade), len(x) // 2)
    if n <= 0:
        return x
    ramp = np.sin(np.linspace(0.0, np.pi / 2, n)) ** 2
    out = x.copy()
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def finish(x: np.ndarray, peak_dbfs: float = PEAK_DBFS) -> np.ndarray:
    """DC block, 5 ms edge fades, remove residual mean, peak-normalise."""
    x = fft_filter(x, highpass_response(18.0, order=2))
    x = edge_fades(x)
    x = x - np.mean(x) * edge_fades(np.ones_like(x))
    peak = float(np.max(np.abs(x)))
    if peak <= 0:
        raise ValueError("silent sound")
    return x * (db_to_gain(peak_dbfs) / peak)


def to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2")


def write_wav(path: Path, x: np.ndarray, *, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = to_int16(x)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(channels)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(pcm.tobytes())


# ----------------------------------------------------------------------------- sounds


def whoosh(rng: np.random.Generator, *, length: float, f0: float, f1: float, peak_at: float, width: float) -> np.ndarray:
    """Pink noise through a band-pass swept f0 → f1 (exponential), under a skewed Hann envelope."""
    n = seconds(length)
    noise = pink_noise(rng, n) + 0.25 * rng.standard_normal(n) / 3.0

    def gain(freqs: np.ndarray, t: float) -> np.ndarray:
        p = min(max(t / length, 0.0), 1.0)
        # ease-in-out sweep so the motion accelerates through the middle like a pass-by
        eased = 0.5 - 0.5 * math.cos(math.pi * p)
        centre = f0 * (f1 / f0) ** eased
        return log_bell(freqs, centre, width) + 0.08 * log_bell(freqs, centre * 2.0, width)

    swept = stft_filter(noise, gain, frame=1024, hop=128)
    u = np.linspace(0.0, 1.0, n)
    warped = np.where(u < peak_at, 0.5 * u / peak_at, 0.5 + 0.5 * (u - peak_at) / (1.0 - peak_at))
    env = np.sin(np.pi * warped) ** 2  # Hann, peak moved to `peak_at`
    return swept * env


def impact(rng: np.random.Generator, *, f_start: float, f_end: float, tail: float, crack: float) -> np.ndarray:
    """Sub drop (f_start → f_end) + a low-mid punch + noise click/knock, tanh-saturated. The punch and knock are what
    a phone speaker (nothing below ~150 Hz) actually plays; the sub body alone would be inaudible there."""
    n = seconds(0.12 + tail * 1.6)
    t = np.arange(n) / SR
    freq = f_end + (f_start - f_end) * np.exp(-t / 0.09)
    body = np.sin(phase_from_freq(freq)) * exp_env(n, tail, attack=0.002)
    punch = np.sin(phase_from_freq(70.0 + 110.0 * np.exp(-t / 0.025))) * exp_env(n, 0.07, attack=0.001)
    click = fit(rng.standard_normal(seconds(0.005)) * np.linspace(1, 0, seconds(0.005)), n)
    click = fft_filter(click, bandpass_response(800, 9000))
    knock = fft_filter(rng.standard_normal(n) * exp_env(n, 0.03), bandpass_response(120, 2600))
    knock /= np.max(np.abs(knock)) + 1e-9
    mix = 1.2 * body + 0.9 * punch + 0.9 * click / (np.max(np.abs(click)) + 1e-9) + 0.5 * crack * knock
    return np.tanh(3.0 * mix) * exp_env(n, tail * 1.4)


def pop(rng: np.random.Generator, *, f_start: float, f_end: float, length: float, drop: float) -> np.ndarray:
    n = seconds(length)
    t = np.arange(n) / SR
    freq = f_end + (f_start - f_end) * np.exp(-t / drop)
    tone = np.sin(phase_from_freq(freq)) * exp_env(n, length * 0.35, attack=0.001)
    pluck = fft_filter(rng.standard_normal(n) * exp_env(n, 0.003), bandpass_response(1500, 7000))
    return np.tanh(1.5 * tone) + 0.12 * pluck


def riser(rng: np.random.Generator, *, length: float, f0: float) -> np.ndarray:
    n = seconds(length)
    t = np.arange(n) / SR
    p = t / length
    freq = f0 * 4.0 ** (p**1.6)  # two octaves, exponential, accelerating
    stack = sum(
        saw(freq * ratio * (1.0 + det), phase0=ph) * amp
        for ratio, det, ph, amp in (
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 0.006, 0.31, 0.8),
            (1.0, -0.007, 0.63, 0.8),
            (1.5, 0.003, 0.12, 0.45),
            (2.0, -0.004, 0.77, 0.35),
        )
    )
    noise = pink_noise(rng, n) * 0.9 + rng.standard_normal(n) * 0.15
    swell = p**2.2
    mix = stack * (0.3 + 0.7 * p**1.3) * 0.55 + noise * swell * 0.55

    def gain(freqs: np.ndarray, tc: float) -> np.ndarray:
        q = min(max(tc / length, 0.0), 1.0)
        hp = 180.0 * (3200.0 / 180.0) ** (q**1.4)
        lp = 1800.0 * (14000.0 / 1800.0) ** q
        safe = np.maximum(freqs, 1.0)
        return 1.0 / np.sqrt(1.0 + (hp / safe) ** 4) / np.sqrt(1.0 + (safe / lp) ** 4)

    shaped = stft_filter(mix, gain, frame=2048, hop=256)
    shaped *= 0.15 + 0.85 * p**1.2
    return np.tanh(1.3 * shaped / (np.max(np.abs(shaped)) + 1e-9))


def record_scratch(rng: np.random.Generator) -> np.ndarray:
    length = 0.5
    n = seconds(length)
    # source material: a bright, slightly noisy chord we "scratch" through
    src_len = seconds(2.0)
    src = sum(saw(f, src_len, phase0=0.1 * i) for i, f in enumerate((196.0, 247.0, 294.0, 392.0)))
    src = src * 0.25 + 0.35 * rng.standard_normal(src_len)
    # hand motion: forward-back-forward strokes, playback rate swings through zero (resample curve)
    t = np.arange(n) / SR
    rate = 3.2 * np.sin(2 * np.pi * (t / length) * 2.0 + 0.3) * (1.0 - 0.35 * t / length) + 0.4
    pos = 0.6 * SR + np.cumsum(rate)
    pos = np.clip(pos, 0, src_len - 2)
    i0 = np.floor(pos).astype(int)
    frac = pos - i0
    scratched = src[i0] * (1 - frac) + src[i0 + 1] * frac
    speed = np.abs(rate)
    scratched *= np.clip(speed / 2.0, 0.0, 1.0) ** 0.7  # quiet when the record stops
    centre = 1400.0 * 2.0 ** (0.9 * np.sin(2 * np.pi * 8.0 * t)) * (0.6 + 0.4 * np.clip(speed / 3.0, 0, 1.2))
    res = svf_bandpass(scratched, centre, q=4.5)
    body = fft_filter(scratched, bandpass_response(300, 3500)) * 0.35
    return np.tanh(2.0 * (res + body) / (np.max(np.abs(res + body)) + 1e-9))


def boom(rng: np.random.Generator) -> np.ndarray:
    """An original low boom: 50 → 38 Hz sine, 30 ms attack, saturation, sub layer, plus a low-mid thump and a short
    rumble so it still reads on small speakers."""
    n = seconds(2.0)
    t = np.arange(n) / SR
    freq = 38.0 + 12.0 * np.exp(-t / 0.35)
    main = np.sin(phase_from_freq(freq)) * exp_env(n, 0.62, attack=0.03)
    sub = np.sin(phase_from_freq(freq * 0.5 + 0.2)) * exp_env(n, 0.9, attack=0.06) * 0.55
    thump = fft_filter(rng.standard_normal(n) * exp_env(n, 0.06, attack=0.004), bandpass_response(70, 900))
    thump /= np.max(np.abs(thump)) + 1e-9
    rumble = fft_filter(rng.standard_normal(n) * exp_env(n, 0.55, attack=0.02), bandpass_response(60, 320))
    rumble /= np.max(np.abs(rumble)) + 1e-9
    mix = np.tanh(3.2 * (main + 0.5 * thump)) + sub + 0.35 * rumble
    tail = np.where(t < 1.6, 1.0, np.cos(np.clip((t - 1.6) / 0.4, 0, 1) * np.pi / 2) ** 2)
    return mix * tail


def cash_register(rng: np.random.Generator) -> np.ndarray:
    n = seconds(1.3)
    out = np.zeros(n)
    # mechanical drawer: two short noise clunks
    for at, dur, lo, hi, amp in ((0.0, 0.035, 300, 4000, 1.0), (0.045, 0.06, 150, 2200, 0.8)):
        m = seconds(dur)
        burst = fft_filter(rng.standard_normal(m) * exp_env(m, dur / 3), bandpass_response(lo, hi))
        place(out, amp * burst / (np.max(np.abs(burst)) + 1e-9), seconds(at))
    # the "ching": two bell strikes, inharmonic partials (ratios 1 : 2.76 : 5.4 on top of 2.1 k / 3.4 k / 5.6 k)
    partials = ((2100.0, 1.0, 0.45), (3400.0, 0.7, 0.32), (5600.0, 0.55, 0.22),
                (2100.0 * 2.76, 0.35, 0.18), (2100.0 * 5.4, 0.18, 0.09))
    for at, amp in ((0.10, 0.7), (0.17, 1.0)):
        m = n - seconds(at)
        tt = np.arange(m) / SR
        bell = sum(a * np.sin(2 * np.pi * f * tt + k) * np.exp(-tt / d) for k, (f, a, d) in enumerate(partials))
        bell *= np.minimum(1.0, tt / 0.0015)
        place(out, amp * 0.55 * bell, seconds(at))
    return np.tanh(1.4 * out)


def ticker(rng: np.random.Generator) -> np.ndarray:
    length, rate = 1.2, 24.0
    n = seconds(length)
    out = np.zeros(n)
    click_n = seconds(0.018)
    count = int(length * rate)
    for i in range(count):
        centre = 3200.0 if i % 2 == 0 else 2600.0
        burst = rng.standard_normal(click_n) * exp_env(click_n, 0.0035, attack=0.0004)
        burst = fft_filter(burst, lambda f, c=centre: log_bell(f, c, 0.45) + 0.3 * log_bell(f, 900.0, 0.5))
        burst /= np.max(np.abs(burst)) + 1e-9
        place(out, burst * (0.85 + 0.15 * rng.random()), seconds(i / rate))
    return out


def slap(rng: np.random.Generator, *, body_hz: float, bright: float) -> np.ndarray:
    n = seconds(0.2)
    t = np.arange(n) / SR
    crack = fft_filter(rng.standard_normal(n) * exp_env(n, 0.012, attack=0.0005), highpass_response(bright))
    crack /= np.max(np.abs(crack)) + 1e-9
    thump = np.sin(phase_from_freq(body_hz * (1 + 0.6 * np.exp(-t / 0.01)))) * exp_env(n, 0.045, attack=0.001)
    flesh = fft_filter(rng.standard_normal(n) * exp_env(n, 0.03), bandpass_response(250, 1400)) * 0.6
    return crack + 0.8 * thump + flesh / (np.max(np.abs(flesh)) + 1e-9) * 0.35


def stamp(rng: np.random.Generator) -> np.ndarray:
    n = seconds(0.35)
    t = np.arange(n) / SR
    thud = np.sin(phase_from_freq(62 + 40 * np.exp(-t / 0.02))) * exp_env(n, 0.07, attack=0.001)
    knock = fft_filter(rng.standard_normal(n) * exp_env(n, 0.02, attack=0.0008), bandpass_response(90, 900))
    knock /= np.max(np.abs(knock)) + 1e-9
    rustle_src = rng.standard_normal(n)
    crinkle = (rng.random(n // 240 + 1) ** 3).repeat(240)[:n]  # paper crinkle: random grain amplitudes
    rustle = fft_filter(rustle_src * crinkle, bandpass_response(1800, 7500))
    rustle *= exp_env(n, 0.09, attack=0.004)
    rustle /= np.max(np.abs(rustle)) + 1e-9
    return np.tanh(1.8 * (thud + 0.7 * knock)) + 0.28 * rustle


def glitch(rng: np.random.Generator, *, tones: bool) -> np.ndarray:
    n = seconds(0.25)
    t = np.arange(n) / SR
    gate_len = SR // 30  # 30 Hz gate
    gates = rng.random(n // gate_len + 1) < 0.62
    gates[0] = True
    gate = gates.repeat(gate_len)[:n].astype(float)
    src = rng.standard_normal(n) * 0.6
    if tones:
        freqs = rng.choice((440.0, 660.0, 990.0, 1320.0, 1760.0), size=n // gate_len + 1).repeat(gate_len)[:n]
        src += np.sign(np.sin(phase_from_freq(freqs))) * 0.5
    else:
        src += np.sin(2 * np.pi * 120 * t) * 0.4
    # sample-and-hold decimation (per gate a different "sample rate") + 4-bit crush
    hold = rng.choice((6, 9, 14, 22), size=n // gate_len + 1).repeat(gate_len)[:n]
    idx = (np.arange(n) // hold) * hold
    crushed = src[np.minimum(idx, n - 1)]
    crushed = np.round(np.clip(crushed, -1, 1) * 7.0) / 7.0
    # short ramps on gate edges so the gating clicks but does not pop
    smooth = fft_filter(gate, lowpass_response(900.0))
    return crushed * np.clip(smooth, 0, 1) * np.linspace(1.0, 0.75, n)


def tape_stop(rng: np.random.Generator) -> np.ndarray:
    length = 0.62
    n = seconds(length)
    t = np.arange(n) / SR
    rate = np.clip(1.0 - t / 0.6, 0.0, 1.0) ** 1.8
    out = np.zeros(n)
    for f, a in ((220.0, 1.0), (261.63, 0.8), (329.63, 0.7), (110.0, 0.9)):
        freq = f * rate
        out += a * (0.6 * np.sin(phase_from_freq(freq)) + 0.4 * saw(freq + 1e-3))
    out = stft_filter(out, lambda fr, tc: lowpass_response(300 + 5000 * max(0.0, 1 - tc / 0.6) ** 2)(fr),
                      frame=1024, hop=128)
    return out * np.clip(rate * 1.4, 0, 1) ** 0.5


# (file id, sound, variant, seed, builder)
SOUNDS: tuple[tuple[str, str, int, int, Callable[[np.random.Generator], np.ndarray]], ...] = (
    ("whoosh_0", "whoosh", 0, 1101, lambda r: whoosh(r, length=0.45, f0=300, f1=4000, peak_at=0.55, width=0.45)),
    ("whoosh_1", "whoosh", 1, 1102, lambda r: whoosh(r, length=0.35, f0=500, f1=5200, peak_at=0.62, width=0.38)),
    ("whoosh_2", "whoosh", 2, 1103, lambda r: whoosh(r, length=0.6, f0=260, f1=3000, peak_at=0.48, width=0.55)),
    ("impact_0", "impact", 0, 1201, lambda r: impact(r, f_start=55, f_end=35, tail=0.4, crack=0.6)),
    ("impact_1", "impact", 1, 1202, lambda r: impact(r, f_start=62, f_end=32, tail=0.55, crack=1.0)),
    ("pop_0", "pop", 0, 1301, lambda r: pop(r, f_start=900, f_end=300, length=0.06, drop=0.012)),
    ("pop_1", "pop", 1, 1302, lambda r: pop(r, f_start=1300, f_end=450, length=0.05, drop=0.009)),
    ("pop_2", "pop", 2, 1303, lambda r: pop(r, f_start=700, f_end=240, length=0.08, drop=0.018)),
    ("riser_0", "riser", 0, 1401, lambda r: riser(r, length=1.5, f0=110)),
    ("riser_1", "riser", 1, 1402, lambda r: riser(r, length=2.5, f0=82.4)),
    ("record_scratch_0", "record_scratch", 0, 1501, record_scratch),
    ("boom_0", "boom", 0, 1601, boom),
    ("cash_register_0", "cash_register", 0, 1701, cash_register),
    ("ticker_0", "ticker", 0, 1801, ticker),
    ("slap_0", "slap", 0, 1901, lambda r: slap(r, body_hz=150, bright=1800)),
    ("slap_1", "slap", 1, 1902, lambda r: slap(r, body_hz=172, bright=2600)),
    ("stamp_0", "stamp", 0, 2001, stamp),
    ("glitch_0", "glitch", 0, 2101, lambda r: glitch(r, tones=False)),
    ("glitch_1", "glitch", 1, 2102, lambda r: glitch(r, tones=True)),
    ("tape_stop_0", "tape_stop", 0, 2201, tape_stop),
)


def render(file_id: str) -> np.ndarray:
    """Final float samples (peak -6 dBFS) for one built-in file id."""
    for fid, _sound, _variant, seed, build in SOUNDS:
        if fid == file_id:
            x = finish(build(np.random.default_rng(seed)))
            if len(x) > seconds(MAX_SECONDS):
                raise ValueError(f"{file_id} longer than {MAX_SECONDS}s")
            return x
    raise KeyError(file_id)


def drop_in_name(file_id: str) -> str:
    """``riser_1`` -> ``riser-synth1.wav``: an FX_LIBRARY_DIR drop-in name for the recipe's sound."""
    for fid, sound, variant, _seed, _build in SOUNDS:
        if fid == file_id:
            return f"{sound}-synth{variant}.wav"
    raise KeyError(file_id)


def synthesise(out_dir: Path, only: set[str] | None = None) -> list[Path]:
    written = []
    for fid, *_rest in SOUNDS:
        if only and fid not in only:
            continue
        path = out_dir / drop_in_name(fid)
        write_wav(path, render(fid))
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="output directory, e.g. fx_library/sfx")
    parser.add_argument("--only", nargs="*", help="recipe ids to render, e.g. riser_0 (default: all)")
    args = parser.parse_args(argv)
    for path in synthesise(args.out, set(args.only) if args.only else None):
        print(path.relative_to(args.out.parent) if path.is_relative_to(args.out.parent) else path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
