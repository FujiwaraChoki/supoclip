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
