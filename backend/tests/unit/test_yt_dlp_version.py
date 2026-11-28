"""Guards for the yt-dlp release the YouTube downloads depend on (offline, no network)."""

from importlib.metadata import version

# yt-dlp before 2026.11.23 loads a YouTube player variant that the EJS challenge
# solver can't run, so every "n" challenge fails ("n challenge solving failed:
# a.addOnDisposeCallback is not a function") and formats get throttled or dropped.
# https://github.com/yt-dlp/yt-dlp/issues/18123
MIN_YT_DLP = (2026, 11, 23)


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(".")[:3])


def test_installed_yt_dlp_has_the_n_challenge_fix():
    assert _version_tuple(version("yt-dlp")) >= MIN_YT_DLP


def test_installed_yt_dlp_ejs_matches_the_version_yt_dlp_expects():
    # yt-dlp only trusts solver scripts from the yt-dlp-ejs package when its
    # version matches the one this yt-dlp release was built against.
    import yt_dlp_ejs
    from yt_dlp.extractor.youtube.jsc._builtin.vendor import _info

    assert yt_dlp_ejs.version == _info.VERSION


def test_ejs_challenge_solver_scripts_load():
    import yt_dlp_ejs.yt.solver

    core = yt_dlp_ejs.yt.solver.core()
    lib = yt_dlp_ejs.yt.solver.lib()

    assert isinstance(core, str) and core.strip()
    assert isinstance(lib, str) and lib.strip()
