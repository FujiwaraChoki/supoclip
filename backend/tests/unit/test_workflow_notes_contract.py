"""Every plan note the planners emit must reach the creator.

The task page drops a note it has no text for, so a code added on one side
alone fails silently: found on a live series whose left-out chapters were
never mentioned.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / "backend" / "src" / "workflows"
CONTRACT = ROOT / "frontend" / "src" / "lib" / "workflows.ts"
MESSAGES = ROOT / "frontend" / "messages" / "en" / "task.json"
TASK_PAGE = ROOT / "frontend" / "src" / "app" / "tasks" / "[id]" / "page.tsx"

# Flagged by the eval; "the video is short" would be false to a creator.
INTERNAL_NOTES = {"RECAP_UNDER_WINDOW"}
EMITTED = re.compile(r"""(?:\.note\(\s*|["']code["']:\s*)["']([A-Z][A-Z_]+)["']""")


def emitted_note_codes() -> set[str]:
    codes: set[str] = set()
    for path in WORKFLOWS.glob("*.py"):
        codes.update(EMITTED.findall(path.read_text(encoding="utf-8")))
    return codes


@pytest.fixture(scope="module")
def frontend_available():
    if not CONTRACT.exists():
        pytest.skip("frontend sources are not part of this image")


def test_the_scan_sees_the_planners():
    assert {"CHAPTERS_LEFT_OUT", "TOPIC_BROADENED", "SPONSOR_SKIPPED", "PART_COUNT_REDUCED"} <= emitted_note_codes()


def test_every_note_is_in_the_frontend_contract(frontend_available):
    union = re.search(r"type WorkflowNoteCode =(.*?);", CONTRACT.read_text(encoding="utf-8"), re.S).group(1)
    assert emitted_note_codes() <= set(re.findall(r'"([A-Z_]+)"', union))


def test_every_note_a_creator_should_read_has_text(frontend_available):
    notes = json.loads(MESSAGES.read_text(encoding="utf-8"))["workflowNotes"]
    assert emitted_note_codes() - INTERNAL_NOTES <= set(notes)
    assert not INTERNAL_NOTES & set(notes)


def test_every_placeholder_is_required_before_the_note_shows(frontend_available):
    """A note missing a parameter is hidden, not shown half-filled."""
    notes = json.loads(MESSAGES.read_text(encoding="utf-8"))["workflowNotes"]
    page = TASK_PAGE.read_text(encoding="utf-8")
    required = dict(re.findall(r'^\s*([A-Z_]+): \[([^\]]*)\],$', page, re.M))
    for code, text in notes.items():
        placeholders = set(re.findall(r"\{(\w+)[,}]", text))
        assert placeholders <= set(re.findall(r'"(\w+)"', required.get(code, ""))), code
