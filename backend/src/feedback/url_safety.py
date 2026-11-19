"""Webhook URLs admins enter: https only, public addresses only (no SSRF).

Checked twice: when an admin saves the URL (``validate_webhook_url`` and
``resolve_public_address``), and again before every send, because DNS can change
between the two. The sender then connects to the address it checked, not to a
second lookup (see ``delivery.post_pinned``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048
DISCORD_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet")


class UnsafeWebhookUrl(ValueError):
    """``code`` is stable for the API and the admin UI's translations."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WebhookTarget:
    url: str
    host: str
    port: int


def _address_is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped or address.sixtofour
        if mapped is not None:
            return _address_is_public(mapped)
        if address.teredo is not None:
            return False
    return bool(address.is_global) and not address.is_multicast and not address.is_reserved


def validate_webhook_url(url: str, kind: str) -> WebhookTarget:
    """Shape checks that need no network. Raises ``UnsafeWebhookUrl``."""
    url = (url or "").strip()
    if not url:
        raise UnsafeWebhookUrl("url_required", "Enter the webhook URL.")
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeWebhookUrl("url_too_long", "The webhook URL is too long.")
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise UnsafeWebhookUrl("url_invalid", "The webhook URL is not valid.")
    try:
        parts = urlsplit(url)
        port = parts.port or 443
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError as exc:
        raise UnsafeWebhookUrl("url_invalid", "The webhook URL is not valid.") from exc
    if parts.scheme.lower() != "https":
        raise UnsafeWebhookUrl("url_not_https", "Webhook URLs must start with https://.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeWebhookUrl("url_has_credentials", "Webhook URLs can't contain a user name or password.")
    if not host:
        raise UnsafeWebhookUrl("url_invalid", "The webhook URL is not valid.")

    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not _address_is_public(literal):
            raise UnsafeWebhookUrl("url_private_address", "Webhook URLs must point to a public internet address.")
    elif host == "localhost" or host.endswith(_BLOCKED_SUFFIXES) or "." not in host:
        raise UnsafeWebhookUrl("url_private_address", "Webhook URLs must point to a public internet address.")

    if literal is None:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise UnsafeWebhookUrl("url_invalid", "The webhook URL is not valid.") from exc

    if kind == "discord":
        if host not in DISCORD_HOSTS or not parts.path.startswith("/api/webhooks/"):
            raise UnsafeWebhookUrl(
                "url_not_discord", "Discord webhook URLs look like https://discord.com/api/webhooks/…"
            )
    return WebhookTarget(url=url, host=host, port=port)


async def resolve_public_address(target: WebhookTarget) -> str:
    """The address to connect to. Every address the name resolves to must be public."""
    try:
        literal = ipaddress.ip_address(target.host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM), timeout=5
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise UnsafeWebhookUrl("url_unresolvable", "The webhook's host name could not be resolved.") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0].split("%", 1)[0]))
            except ValueError:
                continue
    if not addresses:
        raise UnsafeWebhookUrl("url_unresolvable", "The webhook's host name could not be resolved.")
    if not all(_address_is_public(address) for address in addresses):
        raise UnsafeWebhookUrl("url_private_address", "Webhook URLs must point to a public internet address.")
    # Prefer IPv4: some container networks have no IPv6 route out.
    addresses.sort(key=lambda address: address.version)
    return str(addresses[0])


def url_hint(url: str) -> str:
    """What the admin UI shows instead of the URL: host and the last four characters."""
    parts = urlsplit(url)
    tail = (parts.path + ("?" + parts.query if parts.query else "")).rstrip("/")
    return f"{(parts.hostname or '').lower()}/…{tail[-4:]}" if len(tail) > 4 else f"{(parts.hostname or '').lower()}/…"
