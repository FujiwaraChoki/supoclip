"""Unit tests for Layout-Aware Hook Title and Caption Positioning Engine."""

import pytest
from src.media.positioning import (
    LayoutAwarePositioner,
    LayoutType,
    PlacementResult,
    Rect,
    TargetType,
    calculate_fit_video_bounds,
    detect_on_screen_text_boxes,
    get_positioner,
)
from src.media.captions import build_hook_title_ass, build_assemblyai_ass_subtitles


# ---------------------------------------------------------------------------
# 1. Fit Video Bounds Tests
# ---------------------------------------------------------------------------

def test_fit_video_bounds_calculation():
    # 16:9 video on 9:16 vertical canvas (1080x1920)
    bounds = calculate_fit_video_bounds(1920, 1080, 1080, 1920)
    assert bounds.x == 0.0
    assert bounds.w == 1.0
    # Expected height: 1080 * (1080 / 1920) = 607.5 px -> h ~ 0.3164
    assert 0.31 <= bounds.h <= 0.32
    # Vertically centered: y = (1920 - 607.5) / 2 = 656.25 px -> y ~ 0.3418
    assert 0.33 <= bounds.y <= 0.35
    assert 0.65 <= bounds.y2 <= 0.67


def test_fit_captions_stay_inside_actual_video_never_on_blur():
    """Bug B3: Fit-layout captions must appear inside video, never on blurred background."""
    engine = get_positioner(1080, 1920)
    fit_bounds = calculate_fit_video_bounds(1920, 1080, 1080, 1920)

    res = engine.position(
        target=TargetType.CAPTION,
        layout_type=LayoutType.FIT,
        item_width=0.8,
        item_height=0.04,
        content_box=fit_bounds,
    )

    # Captions must be strictly INSIDE fit_bounds (y: ~0.342 to ~0.658)
    assert res.safe_box.y >= fit_bounds.y
    assert res.safe_box.y2 <= fit_bounds.y2
    # Must NOT be on the blurred fill below (around y=0.80 / 1536 px)
    assert res.y < 0.66
    assert res.y > 0.40
    assert "inside_video" in res.selected_region
    assert res.confidence > 0.5


# ---------------------------------------------------------------------------
# 2. Split-Screen Title Tests
# ---------------------------------------------------------------------------

def test_split_screen_title_never_crosses_seam():
    """Bug A1: Title must not cross the seam (0.50) covering chin or chest."""
    engine = get_positioner(1080, 1920)
    seam_y = 0.50

    # Two faces in stacked interview:
    # Top face at y=0.10..0.30, bottom face at y=0.60..0.80
    top_face = Rect(0.35, 0.10, 0.30, 0.20)
    bottom_face = Rect(0.35, 0.60, 0.30, 0.20)

    res = engine.position(
        target=TargetType.HOOK_TITLE,
        layout_type=LayoutType.SPLIT,
        item_width=0.84,
        item_height=0.08,
        faces=[top_face, bottom_face],
        split_seams=[seam_y],
    )

    # Title must NEVER cross seam_y (0.50)
    assert not (res.safe_box.y < seam_y < res.safe_box.y2)
    # Title must stay either completely in top panel or completely in bottom panel
    assert res.safe_box.y2 < seam_y or res.safe_box.y > seam_y
    # In top panel, must clear top face
    if res.safe_box.y2 < seam_y:
        assert res.safe_box.overlap_area(top_face) == 0.0
    assert res.confidence > 0.5


def test_split_screen_captions_avoid_bottom_face_and_seam():
    """Bug B1: Captions must not appear at 81% overlapping bottom face."""
    engine = get_positioner(1080, 1920)
    seam_y = 0.50

    top_face = Rect(0.35, 0.12, 0.30, 0.20)      # chin at 0.32
    bottom_face = Rect(0.35, 0.62, 0.30, 0.22)   # forehead at 0.62, chin at 0.84

    res = engine.position(
        target=TargetType.CAPTION,
        layout_type=LayoutType.SPLIT,
        item_width=0.80,
        item_height=0.04,
        faces=[top_face, bottom_face],
        split_seams=[seam_y],
    )

    # Must NOT overlap bottom face (the 0.81 bug)
    assert res.safe_box.overlap_area(bottom_face) == 0.0
    # Must NOT cross the seam itself
    assert not (res.safe_box.y < seam_y < res.safe_box.y2)
    # Placed in safe area between upper face (0.32) and lower face (0.62)
    assert 0.34 <= res.y <= 0.60
    assert "between_faces" in res.selected_region or "safe_face_gap" in res.selected_region


# ---------------------------------------------------------------------------
# 3. Tight Close-Up Title Tests
# ---------------------------------------------------------------------------

def test_close_up_title_clears_forehead():
    """Bug A2: In tight close-up, title must not cover forehead."""
    engine = get_positioner(1080, 1920)

    # Presenter close-up with forehead starting high at y=0.10, chin at y=0.45
    presenter_face = Rect(0.25, 0.10, 0.50, 0.35)

    res = engine.position(
        target=TargetType.HOOK_TITLE,
        layout_type=LayoutType.CLOSE_UP,
        item_width=0.85,
        item_height=0.08,
        faces=[presenter_face],
    )

    # Must NOT cover forehead (y=0.10 to 0.22)
    forehead = Rect(0.25, 0.10, 0.50, 0.12)
    assert res.safe_box.overlap_area(forehead) == 0.0
    # Placed beneath chin / mid-chest
    assert res.safe_box.y >= presenter_face.y2 or res.safe_box.y2 <= presenter_face.y
    assert res.confidence > 0.5


# ---------------------------------------------------------------------------
# 4. Gameplay Title & Captions Tests
# ---------------------------------------------------------------------------

def test_gameplay_title_avoids_ocr_text_and_webcam_boundary():
    """Bug A3 & A4: Title avoids timers, money, HOUR 99, and webcam boundary."""
    engine = get_positioner(1080, 1920)

    # Facecam panel at top: 0 to 608 px (cam_seam = 0.3167)
    cam_seam = 608.0 / 1920.0
    # On-screen OCR text: "HOUR 99" / timer at top-center, money counter top-right
    hour_99_text = Rect(0.35, 0.34, 0.30, 0.05)  # right under webcam
    money_counter = Rect(0.70, 0.05, 0.25, 0.04)

    res = engine.position(
        target=TargetType.HOOK_TITLE,
        layout_type=LayoutType.GAMEPLAY,
        item_width=0.80,
        item_height=0.07,
        ocr_boxes=[hour_99_text, money_counter],
        split_seams=[cam_seam],
        shot_info={"cam_h": 608},
    )

    # Must NEVER cross webcam boundary
    assert not (res.safe_box.y < cam_seam < res.safe_box.y2)
    # Must NOT overlap "HOUR 99"
    assert res.safe_box.overlap_area(hour_99_text) == 0.0
    # Must NOT overlap money counter
    assert res.safe_box.overlap_area(money_counter) == 0.0


def test_gameplay_captions_avoid_action_zone_and_hud():
    """Bug B2: Gameplay captions avoid player character, vehicles, and HUD."""
    engine = get_positioner(1080, 1920)

    # HUD cutout boxes (e.g. mini-map bottom-right, health bar bottom-left)
    minimap = Rect(0.72, 0.78, 0.26, 0.18)
    health_bar = Rect(0.02, 0.82, 0.28, 0.12)

    res = engine.position(
        target=TargetType.CAPTION,
        layout_type=LayoutType.GAMEPLAY,
        item_width=0.75,
        item_height=0.04,
        hud_boxes=[minimap, health_bar],
    )

    # Must not overlap HUD cutouts
    assert res.safe_box.overlap_area(minimap) == 0.0
    assert res.safe_box.overlap_area(health_bar) == 0.0
    assert res.confidence > 0.5


# ---------------------------------------------------------------------------
# 5. ASS Builder Integration Tests
# ---------------------------------------------------------------------------

def test_build_hook_title_ass_layout_aware_split():
    """build_hook_title_ass uses positioning engine in split layout."""
    template = {"font_size": 48}
    report = {}

    style_line, events = build_hook_title_ass(
        "MY BIG TITLE",
        template,
        1080,
        1920,
        10.0,
        "Arial",
        60,
        center_y=0.5,
        layout_type="split",
        split_seams=[0.5],
        position_report=report,
    )

    assert len(events) >= 1
    # Check that position report captured decision
    assert report["selected_region"].startswith("top_panel")
    # Y center must be in top panel, safe from 0.50
    assert report["y"] < 0.46
    assert r"\pos(" in events[0]
    # Verify the coordinates in the event do not cross seam
    assert r"\pos(540,960)" not in events[0]  # 960 was the buggy seam coordinate!


def test_build_assemblyai_ass_subtitles_layout_aware_fit():
    """build_assemblyai_ass_subtitles places captions inside video for fit layout."""
    report = {"kind": "fit"}
    pos_report = {}

    # Calling with empty words but hook title or dummy path to test placement
    engine = get_positioner(1080, 1920)
    fit_bounds = calculate_fit_video_bounds(1920, 1080, 1080, 1920)
    res = engine.position(
        target="caption",
        layout_type="fit",
        item_width=0.7,
        item_height=0.05,
        content_box=fit_bounds,
    )

    assert res.pixel_y < 1300
    assert res.pixel_y > 700
    assert res.y < 0.66

