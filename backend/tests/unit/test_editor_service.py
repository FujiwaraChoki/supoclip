from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import editor_service
from src.services.editor_service import (
    AssetMetadata,
    EditorAssetError,
    EditorAssetInUse,
    EditorService,
    classify_editor_upload,
    probe_editor_asset,
    transcode_editor_asset_for_browser,
)


def project(task_id: str = "task-1", *, duration: float = 30) -> dict:
    return {
        "schemaVersion": 1,
        "id": "project-1",
        "name": "Editor project",
        "taskId": task_id,
        "version": 1,
        "canvas": {
            "width": 1080,
            "height": 1920,
            "background": "#000000",
            "fps": 30,
        },
        "duration": duration,
        "items": [],
    }


def test_classify_editor_upload_sanitizes_name_and_requires_matching_type():
    assert classify_editor_upload("../cuts/intro.mp4", "video/mp4; charset=binary") == (
        "intro.mp4",
        "video/mp4",
        "video",
    )

    with pytest.raises(EditorAssetError, match="extension"):
        classify_editor_upload("intro.png", "video/mp4")

    with pytest.raises(EditorAssetError, match="Unsupported"):
        classify_editor_upload("payload.svg", "image/svg+xml")

    with pytest.raises(EditorAssetError, match="Unsupported"):
        classify_editor_upload("animation.gif", "image/gif")


def test_probe_rejects_animated_webp_before_it_reaches_the_browser(tmp_path):
    animated = tmp_path / "animation.webp"
    animated.write_bytes(b"RIFF\x0c\x00\x00\x00WEBPANIM\x00\x00\x00\x00")

    with pytest.raises(EditorAssetError, match="Animated WebP"):
        probe_editor_asset(animated, "image")


def test_incompatible_video_is_normalized_to_h264_mp4(monkeypatch, tmp_path):
    source = tmp_path / "phone.mov"
    source.write_bytes(b"source")

    def fake_run(command, **_kwargs):
        from pathlib import Path

        Path(command[-1]).write_bytes(b"normalized")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(editor_service.subprocess, "run", fake_run)
    output = transcode_editor_asset_for_browser(
        source,
        "video",
        AssetMetadata(
            duration=3,
            width=1920,
            height=1080,
            video_codec="hevc",
            audio_codec="aac",
            pixel_format="yuv420p10le",
        ),
    )

    assert output == tmp_path / "phone.mp4"
    assert output.read_bytes() == b"normalized"
    assert not source.exists()


@pytest.mark.asyncio
async def test_save_project_is_bounded_and_passes_expected_version(monkeypatch):
    now = datetime.now(timezone.utc)
    repository = SimpleNamespace(
        save_project=AsyncMock(
            return_value={
                "project": {"items": []},
                "version": 3,
                "updated_at": now,
            }
        )
    )
    service = EditorService(
        object(),
        config=SimpleNamespace(temp_dir="/tmp/editor-service-test"),
        repository=repository,
    )

    payload = project()
    result = await service.save_project("task-1", payload, 2)

    assert result == {"project": {"items": []}, "version": 3, "updated_at": now}
    repository.save_project.assert_awaited_once_with(service.db, "task-1", payload, 2)

    monkeypatch.setattr(editor_service, "MAX_EDITOR_PROJECT_BYTES", 4)
    with pytest.raises(ValueError, match="too large"):
        await service.save_project("task-1", project(), 3)


@pytest.mark.asyncio
async def test_save_project_rejects_malformed_or_cross_task_documents():
    repository = SimpleNamespace(save_project=AsyncMock())
    service = EditorService(
        object(),
        config=SimpleNamespace(temp_dir="/tmp/editor-service-test"),
        repository=repository,
    )

    with pytest.raises(ValueError, match="schema"):
        await service.save_project("task-1", {"items": []}, 0)
    with pytest.raises(ValueError, match="schema"):
        await service.save_project("task-1", project("task-2"), 0)

    repository.save_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_asset_rejects_saved_timeline_references():
    repository = SimpleNamespace(
        get_asset=AsyncMock(return_value=SimpleNamespace(id="asset-1")),
        get_project=AsyncMock(
            return_value=SimpleNamespace(
                project={"items": [{"assetId": "asset-1"}]}
            )
        ),
        delete_asset=AsyncMock(),
    )
    service = EditorService(
        object(),
        config=SimpleNamespace(temp_dir="/tmp/editor-service-test"),
        repository=repository,
    )

    with pytest.raises(EditorAssetInUse, match="saved timeline"):
        await service.delete_asset("task-1", "asset-1")

    repository.delete_asset.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_task_assets_removes_only_the_hashed_task_directory(tmp_path):
    service = EditorService(
        object(),
        config=SimpleNamespace(temp_dir=str(tmp_path)),
        repository=SimpleNamespace(),
    )
    task_root = service._asset_root("task-1")
    task_root.mkdir(parents=True)
    (task_root / "upload.mp4").write_bytes(b"media")
    sibling_root = service._asset_root("task-2")
    sibling_root.mkdir(parents=True)
    (sibling_root / "keep.mp4").write_bytes(b"media")

    await service.delete_task_assets("task-1")

    assert not task_root.exists()
    assert (sibling_root / "keep.mp4").is_file()
