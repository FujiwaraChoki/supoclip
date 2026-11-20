"""Reject configurations that could route uploads into an unreadable replica."""
import pytest

from src.config import Config


def configure_mirror(monkeypatch, *, failover="true", preference="fallback"):
    for key, value in {
        "MEDIA_FALLBACK_BUCKET": "replica",
        "MEDIA_FALLBACK_ENDPOINT": "https://r2.example",
        "MEDIA_FALLBACK_ACCESS_KEY_ID": "test-key",
        "MEDIA_FALLBACK_SECRET_ACCESS_KEY": "test-secret",
        "MEDIA_FAILOVER_ENABLED": failover,
        "MEDIA_PREFERRED_STORE": preference,
    }.items():
        monkeypatch.setenv(key, value)


def test_r2_preference_requires_failover(monkeypatch):
    configure_mirror(monkeypatch, failover="false")
    with pytest.raises(ValueError, match="requires MEDIA_FAILOVER_ENABLED"):
        Config()


def test_unknown_preference_is_rejected(monkeypatch):
    configure_mirror(monkeypatch, preference="r2")
    with pytest.raises(ValueError, match="must be primary or fallback"):
        Config()


def test_config_passes_preference_without_swapping_bucket_identities(monkeypatch, tmp_path):
    from unittest.mock import Mock
    from src.media_storage import MediaStorage

    configure_mirror(monkeypatch)
    monkeypatch.setenv("MEDIA_S3_BUCKET", "original")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    monkeypatch.setattr("src.media_storage.boto3.client", Mock())
    client = MediaStorage(Config()).client
    assert client.preferred_store == "fallback"
    assert client.bucket == "original"
    assert client.stores["fallback"][1] == "replica"
