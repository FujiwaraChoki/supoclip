"""edit_fx.rng: the canonical vectors of §4.2 (WP3a asserts the same ones in TypeScript)."""

from src.edit_fx.contracts import EDIT_FX_VERSION
from src.edit_fx.rng import cue_seed, fnv1a32, hash01, mulberry32, plan_seed


def test_fnv1a32_vectors():
    assert fnv1a32("") == 2166136261
    assert fnv1a32("a") == 3826002220
    assert fnv1a32("fx-v1") == 2961888143
    assert fnv1a32("ünï") == 458365526


def test_mulberry32_first_draws():
    draw = mulberry32(42)
    assert [round(draw(), 10) for _ in range(3)] == [0.6011037519, 0.4482905590, 0.8524657935]


def test_hash01_vector():
    assert hash01(7, 3) == 0.44320501177571714


def test_draws_are_unit_interval_and_deterministic():
    for seed in (0, 1, 0xFFFFFFFF, 2961888143):
        a, b = mulberry32(seed), mulberry32(seed)
        values = [a() for _ in range(200)]
        assert values == [b() for _ in range(200)]
        assert all(0.0 <= v < 1.0 for v in values)


def test_seeds_wrap_to_32_bits():
    assert mulberry32(2**32 + 5)() == mulberry32(5)()
    assert 0 <= hash01(0xFFFFFFFF, 2**40) < 1


def test_seed_helpers_hash_their_strings():
    assert cue_seed(12, "b3", "sfx") == fnv1a32("12:b3:sfx")
    assert plan_seed("task-1", 4) == fnv1a32(f"task-1|4|{EDIT_FX_VERSION}")
    assert cue_seed(12, "b3", "sfx") != cue_seed(12, "b3", "zoom")
