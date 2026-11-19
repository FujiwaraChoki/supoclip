"""The Redis that tests write to comes from the environment, never a default.

A host that also runs SupoClip has its own Redis on 127.0.0.1:6379, so a
fallback to that address would let a local test run write into it.
"""

import os

import pytest


def redis_address() -> tuple[str, int]:
    host, port = os.getenv("REDIS_HOST"), os.getenv("REDIS_PORT")
    if not host or not port:
        pytest.skip("Set REDIS_HOST and REDIS_PORT to a disposable Redis to run this test")
    return host, int(port)
