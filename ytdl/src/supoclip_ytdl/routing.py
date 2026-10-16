"""Route selection, per-route circuit breakers and usage accounting."""
from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field

from .config import Route

_CREDENTIALS = re.compile(r"(?<=://)[^@/\s]+@")


def redact(text: str) -> str:
    """Strip proxy credentials from anything that may reach logs or clients."""
    return _CREDENTIALS.sub("***@", text)


@dataclass
class RouteStats:
    attempts: int = 0
    successes: int = 0
    blocked: int = 0
    failures: int = 0
    bytes: int = 0
    consecutive_blocks: int = 0
    open_until: float = 0.0
    trips: int = 0
    last_error: str | None = None


@dataclass
class Exit:
    """A concrete attempt: the route plus the proxy URL for one sticky session."""

    route: Route
    proxy: str | None
    session: str


class Router:
    def __init__(self, routes: tuple[Route, ...], threshold: int, cooldown: float,
                 clock=time.monotonic):
        self.routes = routes
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._lock = threading.Lock()
        self.stats: dict[str, RouteStats] = {r.name: RouteStats() for r in routes}

    def plan(self) -> list[Route]:
        """Routes in preference order, one entry per allowed attempt.

        Open circuits are skipped; if every route is open, fall back to the full
        plan so a recovering YouTube is noticed instead of failing forever.
        """
        now = self._clock()
        with self._lock:
            available = [r for r in self.routes if self.stats[r.name].open_until <= now]
        chosen = available or list(self.routes)
        return [route for route in chosen for _ in range(route.attempts)]

    @staticmethod
    def exit_for(route: Route) -> Exit:
        session = secrets.token_hex(6)
        proxy = route.proxy.replace("{session}", session) if route.proxy else None
        return Exit(route=route, proxy=proxy, session=session)

    def record(self, route: Route, outcome: str, nbytes: int = 0, error: str | None = None) -> None:
        """outcome: success | blocked | failed | fatal (input problem, not the route's fault)."""
        with self._lock:
            stats = self.stats[route.name]
            stats.attempts += 1
            stats.bytes += nbytes
            if outcome == "success":
                stats.successes += 1
                stats.consecutive_blocks = 0
                stats.trips = 0
            elif outcome == "blocked":
                stats.blocked += 1
                stats.consecutive_blocks += 1
                if stats.consecutive_blocks >= self.threshold:
                    # Exponential backoff for routes that keep getting blocked, capped at 8x.
                    stats.open_until = self._clock() + self.cooldown * (2 ** min(stats.trips, 3))
                    stats.trips += 1
                    stats.consecutive_blocks = 0
            elif outcome == "failed":
                stats.failures += 1
            if error:
                stats.last_error = redact(error)[:300]

    def snapshot(self) -> dict:
        now = self._clock()
        with self._lock:
            return {
                r.name: {
                    "proxied": r.proxy is not None,
                    "attempts": s.attempts,
                    "successes": s.successes,
                    "blocked": s.blocked,
                    "failures": s.failures,
                    "bytes": s.bytes,
                    "circuit_open_seconds": max(0, round(s.open_until - now)),
                    "last_error": s.last_error,
                }
                for r in self.routes
                for s in [self.stats[r.name]]
            }
