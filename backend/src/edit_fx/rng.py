"""Integer-only PRNG for dynamic edits (G3), mirrored bit-exactly by frontend/src/lib/fx/core/rng.ts.

Seeds come from strings (beat ids, purposes, task ids) through FNV-1a; floats are never hashed. Every random choice
the resolver or a drawer makes is ``mulberry32(cue_seed(...))`` or ``hash01(seed, n)``, so a plan and its frames are
reproducible on every machine and in the browser.
"""

from __future__ import annotations

from .contracts import EDIT_FX_VERSION

M32 = 0xFFFFFFFF


def fnv1a32(text: str) -> int:
    h = 0x811C9DC5
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & M32
    return h


def mulberry32(seed: int):
    state = seed & M32

    def next_float() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & M32
        t = state
        t = ((t ^ (t >> 15)) * (t | 1)) & M32
        t = (t ^ ((t + (((t ^ (t >> 7)) * (t | 61)) & M32)) & M32)) & M32
        return ((t ^ (t >> 14)) & M32) / 4294967296

    return next_float


def hash01(seed: int, n: int) -> float:
    return mulberry32((seed ^ ((n * 0x9E3779B1) & M32)) & M32)()


def cue_seed(plan_seed: int, beat_id: str, purpose: str) -> int:
    return fnv1a32(f"{plan_seed}:{beat_id}:{purpose}")


def plan_seed(task_id: str, clip_index: int) -> int:
    return fnv1a32(f"{task_id}|{clip_index}|{EDIT_FX_VERSION}")
