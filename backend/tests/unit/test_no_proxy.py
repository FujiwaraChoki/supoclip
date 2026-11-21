"""A bracketed IPv6 entry in NO_PROXY must not break httpx clients.

httpx 0.28 does not recognise "[::1]" as an IPv6 hostname, so it builds the
bypass mount "all://*[::1]" and raises InvalidURL while parsing the port. That
kills every client that trusts the environment, including the ones the
AssemblyAI and pydantic-ai SDKs construct internally.
"""

import os

import httpx
import pytest
from httpx._utils import get_environment_proxies

from src.config import normalize_no_proxy


def test_bracketed_ipv6_host_is_unbracketed(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,[::1],::1")
    normalize_no_proxy()
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1,::1,::1"


def test_client_constructs_and_bypass_is_preserved(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1")
    normalize_no_proxy()
    with httpx.Client():
        pass
    mounts = get_environment_proxies()
    assert mounts["all://[::1]"] is None
    assert mounts["all://localhost"] is None


def test_lowercase_variable_is_normalized_too(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setenv("no_proxy", "[::1]")
    normalize_no_proxy()
    assert os.environ["no_proxy"] == "::1"


def test_non_ipv6_brackets_are_left_alone(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "[not-an-address],example.com")
    normalize_no_proxy()
    assert os.environ["NO_PROXY"] == "[not-an-address],example.com"


def test_already_bare_list_is_untouched(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,::1")
    normalize_no_proxy()
    assert os.environ["NO_PROXY"] == "localhost,::1"


def test_absent_variable_is_a_noop(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    normalize_no_proxy()
    assert "NO_PROXY" not in os.environ


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_value_is_a_noop(monkeypatch, value):
    monkeypatch.setenv("NO_PROXY", value)
    normalize_no_proxy()
    assert os.environ["NO_PROXY"] == value