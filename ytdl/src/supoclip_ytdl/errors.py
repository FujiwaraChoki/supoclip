"""Classify yt-dlp failures so we rotate IPs only when that can help."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Failure:
    code: str
    # "blocked": YouTube refused this exit IP, rotate. "transient": network or
    # proxy trouble, retry. "fatal": the video itself cannot be fetched.
    kind: str
    message: str


class DownloadFailed(Exception):
    def __init__(self, failure: Failure):
        super().__init__(failure.message)
        self.failure = failure


class Cancelled(Exception):
    pass


class Stalled(Exception):
    pass


# Checked in order; first match wins. Messages are yt-dlp's English error text.
_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("private video",), "private", "fatal", "This YouTube video is private."),
    (("confirm your age", "age-restricted", "inappropriate for some users"),
     "age_restricted", "fatal", "This YouTube video is age-restricted and cannot be downloaded."),
    (("members-only", "join this channel", "available to this channel's members"),
     "members_only", "fatal", "This YouTube video is for channel members only."),
    (("this live event will begin", "premieres in", "is upcoming"),
     "not_started", "fatal", "This YouTube video has not premiered yet."),
    (("live stream recording is not available", "live event has ended"),
     "live", "fatal", "This YouTube live stream has no downloadable recording."),
    (("copyright",), "removed", "fatal", "This YouTube video was removed for copyright reasons."),
    (("has been removed", "account associated with this video has been terminated",
      "this video no longer exists"), "removed", "fatal", "This YouTube video has been removed."),
    (("unsupported url", "incomplete youtube id", "is not a valid url"),
     "invalid_url", "fatal", "That is not a valid YouTube video link."),
    # Geo blocks are worth one more exit: residential pools span countries.
    (("available in your country", "geo restrict", "blocked it in your country"),
     "geo_blocked", "blocked", "This YouTube video is not available in our download region."),
    (("sign in to confirm you", "not a bot", "http error 429", "too many requests",
      "this content isn't available, try again later", "rate-limit", "rate limit"),
     "bot_check", "blocked", "YouTube rate-limited the download."),
    (("http error 403", "forbidden"), "forbidden", "blocked", "YouTube refused the download."),
    (("requested format is not available", "only images are available", "drm protected"),
     "no_formats", "blocked", "YouTube returned no downloadable formats."),
    (("http error 407", "407 proxy", "proxy authentication", "tunnel connection failed", "proxyerror", "proxy error",
      "cannot connect to proxy", "unable to connect to proxy"),
     "proxy_error", "transient", "The download proxy failed."),
    (("timed out", "timeout", "connection reset", "remote end closed", "eof occurred",
      "temporary failure in name resolution", "connection refused", "incompleteread",
      "unable to download api page", "unable to download webpage", "http error 5"),
     "network", "transient", "The YouTube connection dropped."),
    # Checked after "try again later": YouTube uses this wording for soft blocks too.
    (("video unavailable", "this video is unavailable", "not available"),
     "unavailable", "fatal", "This YouTube video is unavailable."),
)


def classify(error: BaseException | str) -> Failure:
    text = str(error)
    lowered = text.lower()
    for needles, code, kind, message in _RULES:
        if any(needle in lowered for needle in needles):
            return Failure(code=code, kind=kind, message=message)
    return Failure(code="unknown", kind="transient", message="YouTube download failed.")
