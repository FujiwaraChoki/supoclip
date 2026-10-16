import pytest

from supoclip_ytdl.config import Route
from supoclip_ytdl.errors import classify
from supoclip_ytdl.routing import Router, redact


@pytest.mark.parametrize("message, code, kind", [
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies", "bot_check", "blocked"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate", "age_restricted", "fatal"),
    ("ERROR: [youtube] abc: Video unavailable. This content isn't available, try again later.", "bot_check", "blocked"),
    ("ERROR: [youtube] abc: Video unavailable", "unavailable", "fatal"),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "private", "fatal"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "forbidden", "blocked"),
    ("ERROR: [youtube] abc: Requested format is not available", "no_formats", "blocked"),
    ("ERROR: Unable to download API page: ('Unable to connect to proxy', OSError('Tunnel connection failed: 407 Proxy Authentication Required'))", "proxy_error", "transient"),
    ("ERROR: [download] Got error: The read operation timed out", "network", "transient"),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content", "members_only", "fatal"),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "geo_blocked", "blocked"),
    ("ERROR: [youtube] abc: This live stream recording is not available.", "live", "fatal"),
    ("something nobody has seen before", "unknown", "transient"),
])
def test_classify(message, code, kind):
    failure = classify(message)
    assert (failure.code, failure.kind) == (code, kind)
    assert "ERROR" not in failure.message


def test_redact_strips_proxy_credentials():
    text = "Unable to connect to proxy http://user-session-abc:s3cret@gate.example.com:7777"
    assert "s3cret" not in redact(text) and "user-session" not in redact(text)
    assert "gate.example.com:7777" in redact(text)


def test_plan_orders_routes_by_preference_and_attempts():
    router = Router((Route("direct", attempts=1), Route("res", "http://u-{session}:p@h:1", attempts=3)), 3, 60)
    assert [r.name for r in router.plan()] == ["direct", "res", "res", "res"]


def test_each_exit_gets_a_fresh_sticky_session():
    route = Route("res", "http://user-session-{session}:pw@gate:7777")
    first, second = Router.exit_for(route), Router.exit_for(route)
    assert first.session != second.session
    assert first.proxy == f"http://user-session-{first.session}:pw@gate:7777"


def test_circuit_opens_after_repeated_blocks_and_recovers():
    now = [1000.0]
    direct, res = Route("direct", attempts=1), Route("res", "http://x", attempts=2)
    router = Router((direct, res), threshold=2, cooldown=60, clock=lambda: now[0])
    router.record(direct, "blocked")
    assert [r.name for r in router.plan()][0] == "direct"
    router.record(direct, "blocked")
    assert [r.name for r in router.plan()] == ["res", "res"]
    now[0] += 61
    assert [r.name for r in router.plan()][0] == "direct"


def test_circuit_backoff_grows_and_resets_on_success():
    now = [0.0]
    route = Route("res", "http://x")
    router = Router((route,), threshold=1, cooldown=10, clock=lambda: now[0])
    router.record(route, "blocked")
    assert router.stats["res"].open_until == 10
    router.record(route, "blocked")
    assert router.stats["res"].open_until == 20
    router.record(route, "success")
    router.record(route, "blocked")
    assert router.stats["res"].open_until == 10


def test_all_open_falls_back_to_full_plan():
    route = Route("res", "http://x", attempts=2)
    router = Router((route,), threshold=1, cooldown=600)
    router.record(route, "blocked")
    assert len(router.plan()) == 2


def test_transient_and_fatal_do_not_trip_circuit():
    route = Route("res", "http://x")
    router = Router((route,), threshold=1, cooldown=600)
    router.record(route, "failed")
    router.record(route, "fatal")
    assert router.stats["res"].open_until == 0
