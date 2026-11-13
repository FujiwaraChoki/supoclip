#!/usr/bin/env python3
"""Synthesise the built-in music beds, one seamless 32-bar loop per mood (FINAL.md §12).

A small numpy step sequencer: kick = sine pitch drop, snare = noise + 180 Hz tone, hats = high-passed noise,
bass = saturated sine, pad = detuned PolyBLEP saws through a one-pole low-pass, plus a per-mood lead voice
(additive plucks, electric-piano or bell tones). Each mood has its own tempo, key and progression:

    hype 124 / minor · chill 82 / maj7 lofi · dramatic 70 / minor strings · funny 112 / major bouncy
    inspiring 96 / major · dark 88 / phrygian

**Seamless by construction.** Every note, reverb tail and filter is rendered into a circular buffer exactly one loop
long: whatever rings past the last bar wraps onto the first one, so the end of the file flows into its start with no
seam at all (stronger than a crossfade). The arrangement is sparse, the master gets a gentle dip where speech lives
(1-3 kHz), and each bed is normalised to about -20 LUFS so it sits under a voice. Output: 48 kHz stereo AAC 128k .m4a.

    cd backend && .venv/bin/python scripts/fx/synth_music.py [--out DIR] [--only hype ...]

The numpy render is deterministic (`render_mood(mood)` returns the same samples every run).
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from synth_sfx import (  # noqa: E402  (sibling script)
    SR,
    bandpass_response,
    db_to_gain,
    exp_env,
    fft_filter,
    highpass_response,
    lowpass_response,
    phase_from_freq,
    saw,
    write_wav,
)

BARS = 32
BEATS_PER_BAR = 4
TARGET_LUFS = -20.0
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUT = BACKEND_DIR / "src" / "fx_assets" / "music"


def midi_hz(note: float) -> float:
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def loop_samples(bpm: float) -> int:
    return int(round(BARS * BEATS_PER_BAR * 60.0 / bpm * SR))


# ----------------------------------------------------------------------------- instruments (mono, with tails)


def kick(rng: np.random.Generator, *, hi: float = 150.0, lo: float = 48.0, decay: float = 0.28,
         click: float = 0.5) -> np.ndarray:
    n = int(SR * decay * 3.2)
    t = np.arange(n) / SR
    freq = lo + (hi - lo) * np.exp(-t / 0.035)
    body = np.sin(phase_from_freq(freq)) * exp_env(n, decay, attack=0.0015)
    tick = fft_filter(rng.standard_normal(n) * exp_env(n, 0.0025), bandpass_response(1200, 6000))
    return np.tanh(1.6 * body) + click * tick / (np.max(np.abs(tick)) + 1e-9) * 0.35


def snare(rng: np.random.Generator, *, tone: float = 180.0, decay: float = 0.16, bright: float = 1.0) -> np.ndarray:
    n = int(SR * decay * 3.5)
    t = np.arange(n) / SR
    body = np.sin(phase_from_freq(tone * (1 + 0.25 * np.exp(-t / 0.01)))) * exp_env(n, decay * 0.45, attack=0.001)
    noise = rng.standard_normal(n) * exp_env(n, decay, attack=0.0008)
    noise = fft_filter(noise, bandpass_response(900 * bright, 9000 * bright))
    noise /= np.max(np.abs(noise)) + 1e-9
    return 0.7 * body + 0.75 * noise


def clap(rng: np.random.Generator, *, decay: float = 0.12) -> np.ndarray:
    n = int(SR * (decay * 3 + 0.03))
    out = np.zeros(n)
    for k, off in enumerate((0.0, 0.009, 0.018)):  # three hands, slightly apart
        m = n - int(off * SR)
        burst = rng.standard_normal(m) * exp_env(m, 0.006 if k < 2 else decay, attack=0.0005)
        out[int(off * SR):] += burst
    out = fft_filter(out, bandpass_response(1000, 7000))
    return out / (np.max(np.abs(out)) + 1e-9)


def hat(rng: np.random.Generator, *, decay: float = 0.035, cutoff: float = 7000.0) -> np.ndarray:
    n = int(SR * max(decay * 4, 0.02))
    noise = rng.standard_normal(n) * exp_env(n, decay, attack=0.0004)
    # a little metallic ring: square partials
    t = np.arange(n) / SR
    metal = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (3_150.0, 4_270.0, 5_830.0, 7_370.0)) * 0.08
    out = fft_filter(noise + metal * exp_env(n, decay), highpass_response(cutoff, order=3))
    return out / (np.max(np.abs(out)) + 1e-9)


def bass(note: float, dur: float, *, drive: float = 2.0, decay: float | None = None) -> np.ndarray:
    n = int(SR * (dur + 0.12))
    t = np.arange(n) / SR
    f = midi_hz(note)
    tone = np.sin(2 * np.pi * f * t) + 0.18 * np.sin(4 * np.pi * f * t)
    env = np.minimum(1.0, t / 0.006)
    env *= np.where(t < dur, 1.0, np.exp(-(t - dur) / 0.03))
    if decay:
        env *= np.exp(-t / decay)
    out = np.tanh(drive * tone * env) / np.tanh(drive)
    return fft_filter(out, lowpass_response(900.0, order=2))


def one_pole_lowpass_response(cutoff: float) -> Callable[[np.ndarray], np.ndarray]:
    """|H(f)| of y[n] = (1-a)·x[n] + a·y[n-1], the pad's one-pole low-pass."""
    a = math.exp(-2 * math.pi * cutoff / SR)

    def resp(f: np.ndarray) -> np.ndarray:
        w = 2 * np.pi * f / SR
        return (1 - a) / np.abs(1 - a * np.exp(-1j * w))

    return resp


def pad(rng: np.random.Generator, notes: tuple[float, ...], dur: float, *, cutoff: float = 1400.0,
        attack: float = 0.35, release: float = 0.9, detune_cents: float = 9.0, voices: int = 3,
        vibrato: float = 0.0) -> np.ndarray:
    """Detuned saws through a one-pole (two cascaded) low-pass. Returns one channel; call twice for stereo."""
    n = int(SR * (dur + release * 3))
    t = np.arange(n) / SR
    out = np.zeros(n)
    for note in notes:
        base = midi_hz(note)
        for v in range(voices):
            cents = (v - (voices - 1) / 2) * detune_cents + rng.uniform(-2, 2)
            f = base * 2 ** (cents / 1200)
            if vibrato:
                f = f * (1 + vibrato * np.sin(2 * np.pi * 5.2 * t + rng.uniform(0, 6.28)) * np.minimum(1, t / 0.8))
                out += saw(f, phase0=rng.random())
            else:
                out += saw(f, n, phase0=rng.random())
    lp = one_pole_lowpass_response(cutoff)
    out = fft_filter(out, lambda f: lp(f) * lp(f))
    env = np.minimum(1.0, t / attack) ** 1.5
    env *= np.where(t < dur, 1.0, np.exp(-(t - dur) / (release / 2.5)))
    return out * env / (len(notes) * voices) ** 0.5


def pluck(note: float, *, decay: float = 0.35, harmonics: int = 10, bright: float = 1.0, inharm: float = 0.0,
          length: float | None = None) -> np.ndarray:
    """Additive pluck: higher partials decay faster (string/mallet-like)."""
    n = int(SR * (length or decay * 4))
    t = np.arange(n) / SR
    f0 = midi_hz(note)
    out = np.zeros(n)
    for k in range(1, harmonics + 1):
        fk = f0 * k * math.sqrt(1 + inharm * k * k)
        if fk > SR * 0.45:
            break
        amp = (1.0 / k) ** (1.6 / bright)
        out += amp * np.sin(2 * np.pi * fk * t + 0.3 * k) * np.exp(-t * k / (decay * bright * 1.0 + 1e-9) * 0.35)
    out *= np.minimum(1.0, t / 0.002) * np.exp(-t / decay)
    return out


def epiano(note: float, dur: float) -> np.ndarray:
    """Rhodes-ish tone: sine carrier with a decaying FM bell and soft tremolo."""
    n = int(SR * (dur + 0.8))
    t = np.arange(n) / SR
    f = midi_hz(note)
    index = 1.6 * np.exp(-t / 0.25) + 0.15
    tone = np.sin(2 * np.pi * f * t + index * np.sin(2 * np.pi * f * t))
    tone += 0.25 * np.sin(2 * np.pi * 14.0 * f * t) * np.exp(-t / 0.03)  # tine ping
    env = np.minimum(1.0, t / 0.004) * np.exp(-t / 1.6)
    env *= np.where(t < dur, 1.0, np.exp(-(t - dur) / 0.12))
    return tone * env * (1 + 0.12 * np.sin(2 * np.pi * 4.5 * t))


def bell(note: float, *, decay: float = 1.4) -> np.ndarray:
    n = int(SR * decay * 3)
    t = np.arange(n) / SR
    f = midi_hz(note)
    out = np.zeros(n)
    for ratio, amp, d in ((1.0, 1.0, 1.0), (2.76, 0.45, 0.55), (5.4, 0.22, 0.3), (8.93, 0.1, 0.18), (0.5, 0.2, 1.2)):
        out += amp * np.sin(2 * np.pi * f * ratio * t) * np.exp(-t / (decay * d))
    return out * np.minimum(1.0, t / 0.002)


def tom(rng: np.random.Generator, *, freq: float = 70.0, decay: float = 0.5) -> np.ndarray:
    """Cinematic low drum (taiko-like): pitched body + skin noise."""
    n = int(SR * decay * 3)
    t = np.arange(n) / SR
    body = np.sin(phase_from_freq(freq * (1 + 0.5 * np.exp(-t / 0.04)))) * exp_env(n, decay, attack=0.002)
    skin = fft_filter(rng.standard_normal(n) * exp_env(n, 0.05), bandpass_response(100, 1500))
    return np.tanh(1.8 * body) + 0.5 * skin / (np.max(np.abs(skin)) + 1e-9)


def swell(rng: np.random.Generator, dur: float, *, low: float = 400.0, high: float = 6000.0) -> np.ndarray:
    """Reverse-cymbal style noise swell that ends exactly at `dur`."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = fft_filter(rng.standard_normal(n), bandpass_response(low, high))
    return noise / (np.max(np.abs(noise)) + 1e-9) * (t / dur) ** 3


# ----------------------------------------------------------------------------- sequencer


@dataclass
class Song:
    bpm: float
    seed: int
    swing: float = 0.0                       # 0..0.5: delay of odd 16ths as a fraction of a 16th
    n: int = 0
    buses: dict[str, np.ndarray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.n = loop_samples(self.bpm)
        self.rng = np.random.default_rng(self.seed)
        self.beat = 60.0 / self.bpm

    @property
    def bar(self) -> float:
        return self.beat * BEATS_PER_BAR

    def at(self, bar: int, beat: float) -> int:
        """Sample index of a position (bar 0..31, beat in quarter notes), swing applied to odd 16ths."""
        sixteenth = beat * 4
        if self.swing and abs(sixteenth - round(sixteenth)) < 1e-6 and int(round(sixteenth)) % 2 == 1:
            beat += self.swing / 4
        return int(round((bar * BEATS_PER_BAR + beat) * self.beat * SR)) % self.n

    def add(self, bus: str, sound: np.ndarray, bar: int, beat: float, *, gain: float = 1.0, pan: float = 0.0,
            jitter: float = 0.0, right: np.ndarray | None = None) -> None:
        """Mix a mono sound (or a stereo pair via `right`) into a bus circularly: tails wrap to the loop start."""
        buf = self.buses.setdefault(bus, np.zeros((2, self.n)))
        start = self.at(bar, beat)
        if jitter:
            start = (start + int(self.rng.uniform(-jitter, jitter) * SR)) % self.n
        left_gain = gain * math.cos((pan + 1) * math.pi / 4) * math.sqrt(2)
        right_gain = gain * math.sin((pan + 1) * math.pi / 4) * math.sqrt(2)
        for ch, (snd, g) in enumerate(((sound, left_gain), (sound if right is None else right, right_gain))):
            if len(snd) <= self.n - start:
                buf[ch, start:start + len(snd)] += snd * g
            else:  # wraps past the loop end onto the start
                np.add.at(buf[ch], (start + np.arange(len(snd))) % self.n, snd * g)

    def bus(self, name: str) -> np.ndarray:
        return self.buses.get(name, np.zeros((2, self.n)))


def circular_filter(x: np.ndarray, response: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    return np.stack([fft_filter(ch, response, circular=True) for ch in x])


def reverb(song: Song, x: np.ndarray, *, rt60: float, tone: float = 5000.0, predelay: float = 0.018) -> np.ndarray:
    """Circular convolution with a decorrelated stereo exponential-noise impulse response (tails wrap seamlessly)."""
    rng = np.random.default_rng(song.seed + 7)
    length = min(int(SR * rt60 * 1.1), song.n)
    t = np.arange(length) / SR
    decay = np.exp(-6.9 * t / rt60)
    out = np.empty_like(x)
    for ch in range(2):
        ir = np.zeros(song.n)
        early = rng.standard_normal(length) * decay
        early = fft_filter(early, lowpass_response(tone, order=1))
        pd = int(predelay * SR)
        ir[pd:pd + length] = early[: song.n - pd]
        ir /= np.sqrt(np.sum(ir**2)) + 1e-12
        out[ch] = np.fft.irfft(np.fft.rfft(x[ch]) * np.fft.rfft(ir), song.n)
    return out


def pump(song: Song, depth: float = 0.5, release: float = 0.22) -> np.ndarray:
    """Sidechain-style gain curve ducking on every beat (four-on-the-floor)."""
    t = np.arange(song.n) / SR
    phase = np.mod(t, song.beat)
    return 1.0 - depth * np.exp(-phase / (release / 3)) * np.minimum(1.0, phase / 0.004 + 0.6)


def section(bar: int) -> str:
    """Arrangement: four 8-bar sections A B A B'. Bars 15 and 31 (the turns) carry the same fill."""
    return "AB"[(bar // 8) % 2]


# ----------------------------------------------------------------------------- moods


def compose_hype(song: Song) -> None:
    r = song.rng
    prog = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]  # Am F C G
    bass_roots = [45, 41, 36, 43]
    k, cl, ch, oh = kick(r, hi=160, lo=50), clap(r), hat(r, decay=0.03), hat(r, decay=0.16, cutoff=6500)
    for bar in range(BARS):
        sec, chord = section(bar), prog[bar % 4]
        for beat in range(4):
            song.add("drums", k, bar, beat, gain=0.95)
            song.add("drums", ch, bar, beat + 0.5, gain=0.22 if sec == "A" else 0.28, pan=0.25, jitter=0.002)
            if sec == "B":
                song.add("drums", ch, bar, beat + 0.25, gain=0.1, pan=-0.2, jitter=0.002)
                song.add("drums", ch, bar, beat + 0.75, gain=0.12, pan=-0.2, jitter=0.002)
        song.add("drums", cl, bar, 1, gain=0.42, pan=-0.05)
        song.add("drums", cl, bar, 3, gain=0.42, pan=0.05)
        if sec == "B":
            song.add("drums", oh, bar, 3.5, gain=0.16, pan=0.3)
        for beat in range(4):  # house bass on the off-beats
            song.add("bass", bass(bass_roots[bar % 4], song.beat * 0.4, drive=2.5), bar, beat + 0.5, gain=0.5)
        if bar % 2 == 0:
            for pan_ in (-0.7, 0.7):
                song.add("pad", pad(r, chord, song.bar * 2 - 0.1, cutoff=1500, attack=0.08), bar, 0,
                         gain=0.28, pan=pan_)
        if sec == "B":  # 16th pluck arpeggio, every other step
            arp = [chord[0] + 12, chord[1] + 12, chord[2] + 12, chord[1] + 24]
            for step in range(0, 16, 2):
                note = arp[(step // 2) % 4]
                song.add("lead", pluck(note, decay=0.16, harmonics=12), bar, step / 4, gain=0.22,
                         pan=0.35 if (step // 2) % 2 else -0.35)
        if bar % 8 == 7:
            song.add("fx", swell(r, song.bar * 0.5, low=1500), bar, 2, gain=0.18)
    song.buses["pad"] *= pump(song, depth=0.55)
    song.buses["bass"] *= pump(song, depth=0.35)


def compose_chill(song: Song) -> None:
    r = song.rng
    prog = [(53, 57, 60, 64), (52, 55, 59, 62), (50, 53, 57, 60), (48, 52, 55, 59)]  # Fmaj7 Em7 Dm7 Cmaj7
    roots = [41, 40, 38, 36]
    k = kick(r, hi=110, lo=52, decay=0.22, click=0.15)
    sn = fft_filter(snare(r, tone=190, decay=0.13, bright=0.7), lowpass_response(4500))
    ch = fft_filter(hat(r, decay=0.022, cutoff=6000), lowpass_response(9000))
    melody = [(0.0, 72), (0.75, 74), (1.5, 76), (3.0, 79), (6.0, 77), (6.5, 76), (7.0, 74), (11.0, 72),
              (11.5, 71), (12.0, 72), (14.5, 67)]
    for bar in range(BARS):
        sec, chord = section(bar), prog[bar % 4]
        song.add("drums", k, bar, 0, gain=0.7)
        song.add("drums", k, bar, 2.5, gain=0.55)
        if bar % 2:
            song.add("drums", k, bar, 1.75, gain=0.35)
        song.add("drums", sn, bar, 1, gain=0.35, jitter=0.004)
        song.add("drums", sn, bar, 3, gain=0.35, jitter=0.004)
        if sec == "B" or bar % 2:
            for step in range(8):
                song.add("drums", ch, bar, step / 2, gain=0.1 + 0.05 * (step % 2 == 0) * r.random(),
                         pan=0.3, jitter=0.003)
        song.add("bass", bass(roots[bar % 4], song.bar * 0.55, drive=1.4), bar, 0, gain=0.55)
        song.add("bass", bass(roots[bar % 4] + 7, song.beat * 0.8, drive=1.4), bar, 2.75, gain=0.4)
        if bar % 2 == 0:  # a soft, dark bed under the comping so bars never drop into silence
            for pan_ in (-0.6, 0.6):
                song.add("pad", pad(r, chord, song.bar * 2 - 0.1, cutoff=700, attack=0.5, release=1.0),
                         bar, 0, gain=0.12, pan=pan_)
        for beat, length in ((0.0, 1.4), (1.75, 1.2)):  # lazy comping
            voices = [epiano(n_, song.beat * length) for n_ in chord]
            rolled = sum(np.pad(v, (int(i * 0.012 * SR), 0))[: len(v)] for i, v in enumerate(voices))
            song.add("keys", rolled, bar, beat, gain=0.13, pan=-0.15)
        if sec == "B":
            for beat, note in melody:
                if int(beat // 4) == bar % 4:
                    song.add("lead", pluck(note, decay=0.45, harmonics=6, bright=0.6), bar, beat % 4,
                             gain=0.16, pan=0.25)
    # vinyl crackle: sparse soft clicks across the whole loop (circular)
    crackle = np.zeros(song.n)
    pos = r.integers(0, song.n, size=int(song.n / SR * 9))
    crackle[pos] = r.choice((-1.0, 1.0), size=len(pos)) * r.random(len(pos)) ** 2
    crackle = fft_filter(crackle, bandpass_response(1500, 7000), circular=True)
    song.buses["fx"] = np.stack([crackle, np.roll(crackle, 731)]) * 0.5


def compose_dramatic(song: Song) -> None:
    r = song.rng
    prog = [(50, 57, 62, 65), (46, 53, 58, 62), (43, 50, 55, 58), (45, 52, 57, 61)]  # Dm Bb Gm A
    roots = [38, 34, 31, 33]
    low, high = tom(r, freq=62, decay=0.6), tom(r, freq=95, decay=0.35)
    for bar in range(BARS):
        sec, chord = section(bar), prog[bar % 4]
        song.add("drums", low, bar, 0, gain=0.8)
        song.add("drums", low, bar, 2, gain=0.55)
        if sec == "B":
            song.add("drums", high, bar, 3.5, gain=0.4, pan=0.2)
            song.add("drums", high, bar, 3.75, gain=0.3, pan=-0.2)
        for pan_ in (-0.8, 0.8):
            song.add("pad", pad(r, chord, song.bar - 0.05, cutoff=1100, attack=0.9, release=1.6,
                                detune_cents=7, vibrato=0.0025), bar, 0, gain=0.3, pan=pan_)
        song.add("bass", bass(roots[bar % 4], song.bar * 0.95, drive=1.2), bar, 0, gain=0.45)
        if sec == "B":  # staccato low-string ostinato in 8ths
            for step in range(8):
                note = roots[bar % 4] + 12 + (7 if step % 4 == 2 else 0)
                st = fft_filter(saw(midi_hz(note), int(SR * 0.22)) * exp_env(int(SR * 0.22), 0.07, attack=0.004),
                                lowpass_response(1600))
                song.add("lead", st, bar, step / 2, gain=0.14, pan=0.4 if step % 2 else -0.4)
        if bar % 8 == 7:
            song.add("fx", swell(r, song.bar * 0.5, low=600, high=5000), bar, 2, gain=0.2)


def compose_funny(song: Song) -> None:
    r = song.rng
    prog = [(60, 64, 67), (57, 60, 64), (53, 57, 60), (55, 59, 62)]  # C Am F G
    roots = [36, 33, 29, 31]
    k, sn = kick(r, hi=130, lo=60, decay=0.18, click=0.3), snare(r, tone=220, decay=0.1)
    block = fft_filter(np.sin(2 * np.pi * 1250 * np.arange(int(SR * 0.08)) / SR) * exp_env(int(SR * 0.08), 0.015),
                       bandpass_response(600, 4000))
    melody = [(0, 72), (0.5, 76), (1, 79), (1.75, 76), (2.5, 74), (3, 72),
              (4, 69), (4.5, 72), (5, 76), (6, 74), (6.5, 72),
              (8, 69), (8.5, 72), (9, 77), (10, 76), (10.5, 74), (11, 72),
              (12, 71), (12.5, 74), (13, 79), (14, 77), (14.75, 74), (15.5, 71)]
    for bar in range(BARS):
        sec, chord = section(bar), prog[bar % 4]
        song.add("drums", k, bar, 0, gain=0.6)
        song.add("drums", k, bar, 2, gain=0.55)
        song.add("drums", sn, bar, 1, gain=0.3)
        song.add("drums", sn, bar, 3, gain=0.3)
        for beat in (0.5, 1.5, 2.5, 3.5):
            song.add("drums", block, bar, beat, gain=0.18, pan=0.45 if beat % 2 > 1 else -0.45)
        for beat in range(4):  # oom-pah: root / fifth, staccato
            note = roots[bar % 4] + (0 if beat % 2 == 0 else 7)
            song.add("bass", bass(note, song.beat * 0.35, drive=1.8, decay=0.25), bar, beat, gain=0.55)
            if beat % 2 == 1:
                stab = sum(pluck(n_, decay=0.09, harmonics=8) for n_ in chord)
                song.add("keys", stab, bar, beat, gain=0.13, pan=0.1)
        if sec == "B":
            for beat, note in melody:
                if int(beat // 4) == bar % 4:
                    song.add("lead", pluck(note + 12, decay=0.12, harmonics=9, bright=1.3), bar, beat % 4,
                             gain=0.2, pan=-0.2)
        if bar % 8 == 7:
            song.add("fx", sum(pluck(n_, decay=0.08) for n_ in (84, 88, 91)), bar, 3.5, gain=0.12)


def compose_inspiring(song: Song) -> None:
    r = song.rng
    prog = [(62, 66, 69), (57, 61, 64), (59, 62, 66), (55, 59, 62)]  # D A Bm G
    roots = [38, 33, 35, 31]
    k, cl = kick(r, hi=120, lo=50, decay=0.3, click=0.2), clap(r, decay=0.15)
    for bar in range(BARS):
        sec, chord = section(bar), prog[bar % 4]
        arp = [chord[0], chord[1], chord[2], chord[0] + 12, chord[2], chord[1], chord[0] + 12, chord[1] + 12]
        for step, note in enumerate(arp):  # piano-like 8th arpeggio
            song.add("keys", pluck(note, decay=0.9, harmonics=9, bright=0.8, inharm=0.0004), bar, step / 2,
                     gain=0.17, pan=-0.3 + 0.6 * (step / 7))
        song.add("bass", bass(roots[bar % 4], song.bar * 0.97, drive=1.1), bar, 0, gain=0.45)
        for pan_ in (-0.6, 0.6):
            song.add("pad", pad(r, chord, song.bar - 0.05, cutoff=1800, attack=0.6, release=1.2), bar, 0,
                     gain=0.17, pan=pan_)
        if sec == "B":
            song.add("drums", k, bar, 0, gain=0.6)
            song.add("drums", k, bar, 2, gain=0.5)
            song.add("drums", k, bar, 2.5, gain=0.3)
            song.add("drums", cl, bar, 1, gain=0.28)
            song.add("drums", cl, bar, 3, gain=0.28)
            if bar % 2 == 0:
                song.add("lead", bell(chord[2] + 12, decay=0.9), bar, 0, gain=0.07, pan=0.3)
        if bar % 8 == 7:
            song.add("fx", swell(r, song.bar, low=800, high=8000), bar, 0, gain=0.14)


def compose_dark(song: Song) -> None:
    r = song.rng
    prog = [(40, 47, 52), (40, 47, 52), (41, 48, 53), (40, 47, 50)]  # E5 E5 F5 Em(no3)/D: phrygian b2 pull
    k = kick(r, hi=110, lo=42, decay=0.38, click=0.1)
    tick = hat(r, decay=0.015, cutoff=8000)
    for bar in range(BARS):
        sec, chord = section(bar), prog[bar % 4]
        song.add("drums", k, bar, 0, gain=0.75)
        song.add("drums", k, bar, 2.5, gain=0.55)
        for step in range(16):
            accent = 1.0 if step % 4 == 2 else 0.45
            if sec == "A" and step % 2:
                continue
            song.add("drums", tick, bar, step / 4, gain=0.08 * accent, pan=0.35, jitter=0.002)
        if sec == "B":
            song.add("drums", snare(r, tone=160, decay=0.22, bright=0.6), bar, 3, gain=0.25)
        song.add("bass", bass(28, song.bar * 0.98, drive=2.2), bar, 0, gain=0.4)  # E1 drone
        for pan_ in (-0.7, 0.7):
            song.add("pad", pad(r, chord, song.bar - 0.05, cutoff=650, attack=1.2, release=1.8, detune_cents=12),
                     bar, 0, gain=0.34, pan=pan_)
        if sec == "B" and bar % 2 == 1:
            for beat, note in ((0.0, 71), (1.5, 72), (3.0, 71)):  # B4 C5 B4: the b2 rub
                song.add("lead", bell(note, decay=1.1), bar, beat, gain=0.07, pan=-0.3)
        if bar % 8 == 7:
            song.add("fx", swell(r, song.bar, low=300, high=3000), bar, 0, gain=0.16)


@dataclass(frozen=True)
class MoodSpec:
    mood: str
    bpm: int
    seed: int
    compose: Callable[[Song], None]
    swing: float = 0.0
    reverb: tuple[float, float] = (1.4, 0.18)          # (rt60 s, wet level on pad/keys/lead/fx)
    levels: dict[str, float] = field(default_factory=dict)


MOODS: tuple[MoodSpec, ...] = (
    MoodSpec("hype", 124, 3101, compose_hype, reverb=(1.2, 0.16)),
    MoodSpec("chill", 82, 3102, compose_chill, swing=0.32, reverb=(1.6, 0.2)),
    MoodSpec("dramatic", 70, 3103, compose_dramatic, reverb=(3.2, 0.32)),
    MoodSpec("funny", 112, 3104, compose_funny, reverb=(0.8, 0.12)),
    MoodSpec("inspiring", 96, 3105, compose_inspiring, reverb=(2.4, 0.26)),
    MoodSpec("dark", 88, 3106, compose_dark, reverb=(2.8, 0.3)),
)
BPM: dict[str, int] = {m.mood: m.bpm for m in MOODS}


def spec_for(mood: str) -> MoodSpec:
    for spec in MOODS:
        if spec.mood == mood:
            return spec
    raise KeyError(mood)


def speech_pocket(f: np.ndarray) -> np.ndarray:
    """-3.5 dB bell around 1.8 kHz (where speech intelligibility lives) + a 30 Hz rumble high-pass."""
    safe = np.maximum(f, 1.0)
    dip = 1.0 - (1.0 - db_to_gain(-3.5)) * np.exp(-0.5 * (np.log2(safe / 1800.0) / 0.8) ** 2)
    return dip * highpass_response(30.0, order=2)(f)


def render_mood(mood: str) -> np.ndarray:
    """Stereo float loop (2, n), exactly `loop_samples(bpm)` long, before loudness normalisation."""
    spec = spec_for(mood)
    song = Song(spec.bpm, spec.seed, swing=spec.swing)
    spec.compose(song)
    dry = song.bus("drums") + song.bus("bass") + song.bus("pad") + song.bus("keys") + song.bus("lead")
    send = song.bus("pad") + song.bus("keys") + song.bus("lead") + song.bus("fx") + 0.3 * song.bus("drums")
    rt60, wet = spec.reverb
    mix = dry + song.bus("fx") + wet * reverb(song, send, rt60=rt60)
    mix = circular_filter(mix, speech_pocket)
    peak = float(np.max(np.abs(mix))) + 1e-12
    mix = mix / peak * 0.7
    return np.tanh(1.2 * mix) / np.tanh(1.2)  # gentle glue


def measure_lufs(path: Path) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    matches = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)
    if not matches:
        raise RuntimeError(f"no loudness for {path}")
    return float(matches[-1])


def encode_m4a(samples: np.ndarray, out: Path) -> None:
    """Normalise to TARGET_LUFS (measured by ffmpeg ebur128) and encode AAC 128k, bit-exact flags, no metadata."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "bed.wav"
        interleaved = samples.T.reshape(-1)
        write_wav(wav, interleaved * 0.5, channels=2)
        gain_db = TARGET_LUFS - measure_lufs(wav)
        scaled = interleaved * 0.5 * db_to_gain(gain_db)
        if np.max(np.abs(scaled)) > db_to_gain(-1.0):
            raise RuntimeError(f"{out.name}: peak above -1 dBFS at {TARGET_LUFS} LUFS")
        write_wav(wav, scaled, channels=2)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "128k",
             "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact", "-movflags", "+faststart",
             str(out)],
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", nargs="*", help="moods to render (default: all)")
    args = parser.parse_args(argv)
    for spec in MOODS:
        if args.only and spec.mood not in args.only:
            continue
        path = args.out / f"{spec.mood}.m4a"
        encode_m4a(render_mood(spec.mood), path)
        print(f"{path} ({loop_samples(spec.bpm) / SR:.3f}s @ {spec.bpm} bpm)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
