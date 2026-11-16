from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src import font_registry
from src.api.routes import media


@pytest.fixture()
def isolated_font_dirs(monkeypatch, tmp_path):
    system_dir = tmp_path / "system-fonts"
    user_dir = tmp_path / "user-fonts"
    system_dir.mkdir()
    user_dir.mkdir()
    monkeypatch.setattr(font_registry, "FONTS_DIR", system_dir)
    monkeypatch.setattr(font_registry, "USER_FONTS_DIR", user_dir)
    return system_dir, user_dir


@pytest.mark.asyncio
async def test_delete_font_removes_only_the_authenticated_users_upload(
    monkeypatch, isolated_font_dirs
):
    _, user_root = isolated_font_dirs
    owned_font = user_root / "user-1" / "usr-user-1-brand.ttf"
    owned_font.parent.mkdir()
    owned_font.write_bytes(b"font")
    monkeypatch.setattr(
        media, "_get_authenticated_user_id", AsyncMock(return_value="user-1")
    )

    response = await media.delete_font("usr-user-1-brand", object(), object())

    assert response["font_name"] == "usr-user-1-brand"
    assert not owned_font.exists()


@pytest.mark.asyncio
async def test_delete_font_cannot_delete_another_users_upload(
    monkeypatch, isolated_font_dirs
):
    _, user_root = isolated_font_dirs
    other_font = user_root / "user-2" / "usr-user-2-brand.ttf"
    other_font.parent.mkdir()
    other_font.write_bytes(b"font")
    monkeypatch.setattr(
        media, "_get_authenticated_user_id", AsyncMock(return_value="user-1")
    )

    with pytest.raises(HTTPException) as exc_info:
        await media.delete_font("usr-user-2-brand", object(), object())

    assert exc_info.value.status_code == 404
    assert other_font.exists()


@pytest.mark.asyncio
async def test_delete_font_accepts_full_filename(monkeypatch, isolated_font_dirs):
    _, user_root = isolated_font_dirs
    owned_font = user_root / "user-1" / "usr-user-1-brand.ttf"
    owned_font.parent.mkdir()
    owned_font.write_bytes(b"font")
    monkeypatch.setattr(
        media, "_get_authenticated_user_id", AsyncMock(return_value="user-1")
    )

    response = await media.delete_font("usr-user-1-brand.ttf", object(), object())

    assert response["font_name"] == "usr-user-1-brand"
    assert not owned_font.exists()


@pytest.mark.asyncio
async def test_delete_font_rejects_path_traversal(monkeypatch, isolated_font_dirs):
    _, user_root = isolated_font_dirs
    other_font = user_root / "user-2" / "usr-user-2-brand.ttf"
    other_font.parent.mkdir()
    other_font.write_bytes(b"font")
    monkeypatch.setattr(
        media, "_get_authenticated_user_id", AsyncMock(return_value="user-1")
    )

    with pytest.raises(HTTPException) as exc_info:
        await media.delete_font(
            "../user-2/usr-user-2-brand.ttf", object(), object()
        )

    assert exc_info.value.status_code == 404
    assert other_font.exists()


@pytest.mark.asyncio
async def test_delete_font_rejects_bundled_system_font(
    monkeypatch, isolated_font_dirs
):
    system_dir, _ = isolated_font_dirs
    system_font = system_dir / "Inter.ttf"
    system_font.write_bytes(b"font")
    monkeypatch.setattr(
        media, "_get_authenticated_user_id", AsyncMock(return_value="user-1")
    )

    with pytest.raises(HTTPException) as exc_info:
        await media.delete_font("Inter", object(), object())

    assert exc_info.value.status_code == 403
    assert system_font.exists()


@pytest.mark.asyncio
async def test_delete_font_requires_authentication(monkeypatch, isolated_font_dirs):
    monkeypatch.setattr(
        media,
        "_get_authenticated_user_id",
        AsyncMock(side_effect=HTTPException(status_code=401, detail="Unauthorized")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await media.delete_font("usr-user-1-brand", object(), object())

    assert exc_info.value.status_code == 401


def _font_upload(name="Brand.ttf", body=b"font bytes"):
    from io import BytesIO

    from starlette.datastructures import UploadFile

    return UploadFile(file=BytesIO(body), filename=name)


@pytest.fixture()
def paid_user(monkeypatch):
    monkeypatch.setattr(media, "_get_authenticated_user_id", AsyncMock(return_value="user-1"))
    billing = AsyncMock()
    billing.get_usage_summary.return_value = {"monetization_enabled": False}
    monkeypatch.setattr(media, "BillingService", lambda _db: billing)


@pytest.mark.asyncio
async def test_uploaded_font_is_stored_where_every_worker_can_fetch_it(monkeypatch, isolated_font_dirs, paid_user):
    _, user_root = isolated_font_dirs
    published = []
    monkeypatch.setattr(media, "publish_user_font", lambda path, user: published.append((path.read_bytes(), user)))

    response = await media.upload_font(object(), _font_upload(), object())

    stored = user_root / "user-1" / response["font"]["filename"]
    assert stored.read_bytes() == b"font bytes"
    assert published == [(b"font bytes", "user-1")]

    removed = []
    monkeypatch.setattr(media, "unpublish_user_font", lambda name, user: removed.append((name, user)))
    await media.delete_font(response["font"]["name"], object(), object())
    assert removed == [(stored.name, "user-1")] and not stored.exists()


@pytest.mark.asyncio
async def test_font_upload_fails_cleanly_when_it_cannot_be_stored(monkeypatch, isolated_font_dirs, paid_user):
    """A font only on the API's disk would silently render as the default on another machine."""
    from botocore.exceptions import EndpointConnectionError

    _, user_root = isolated_font_dirs

    def unavailable(*_args):
        raise EndpointConnectionError(endpoint_url="https://storage.test")

    monkeypatch.setattr(media, "publish_user_font", unavailable)
    with pytest.raises(HTTPException) as exc_info:
        await media.upload_font(object(), _font_upload(), object())
    assert exc_info.value.status_code == 503
    assert not list(user_root.rglob("*.ttf"))

    # Deleting still succeeds for the user when the stored copy cannot be removed.
    font = user_root / "user-1" / "usr-user-1-brand.ttf"
    font.parent.mkdir(exist_ok=True)
    font.write_bytes(b"font")
    monkeypatch.setattr(media, "unpublish_user_font", unavailable)
    assert (await media.delete_font("usr-user-1-brand", object(), object()))["font_name"] == "usr-user-1-brand"
    assert not font.exists()
