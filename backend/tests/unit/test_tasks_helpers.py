from src.api.routes.tasks import (
    _build_public_task,
    _normalize_font_color,
    _normalize_font_family,
    _normalize_font_size,
)


def test_normalize_font_size_bounds_values():
    assert _normalize_font_size(None) is None
    assert _normalize_font_size("") is None
    assert _normalize_font_size("4") == 12
    assert _normalize_font_size("120") == 72


def test_normalize_font_color_accepts_hex_values():
    assert _normalize_font_color(None) is None
    assert _normalize_font_color("  ") is None
    assert _normalize_font_color("#abcdef") == "#ABCDEF"
    assert _normalize_font_color("blue") == "#FFFFFF"


def test_normalize_font_family_passes_through_empty_values_as_none():
    assert _normalize_font_family(None) is None
    assert _normalize_font_family("  ") is None
    assert _normalize_font_family("Inter") == "Inter"


def test_build_public_task_excludes_owner_and_file_system_fields():
    public_task = _build_public_task(
        {
            "id": "task-1",
            "user_id": "owner-1",
            "source_title": "Shared source",
            "source_type": "youtube",
            "source_url": "https://private.example/video",
            "status": "completed",
            "clips_count": 1,
            "created_at": "2026-07-22T00:00:00Z",
            "updated_at": "2026-07-22T00:01:00Z",
            "clips": [
                {
                    "id": "clip-1",
                    "filename": "clip.mp4",
                    "file_path": "/private/clip.mp4",
                    "clip_order": 1,
                    "text": "A shared transcript",
                }
            ],
        },
        "token-1",
    )

    assert public_task["source_title"] == "Shared source"
    assert "user_id" not in public_task
    assert "source_url" not in public_task
    assert "file_path" not in public_task["clips"][0]
    assert public_task["clips"][0]["video_url"] == (
        "/tasks/shared/token-1/clips/clip-1/file"
    )
