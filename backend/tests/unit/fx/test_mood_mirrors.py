"""Every list of music moods outside contracts.py mirrors MOODS: the TS contract and the web client, the MCP server,
the studio AI's settings help and the self-hoster drop-in README. A mood added on one side alone is silently dropped
(the web client filters the server's list by its own; the MCP server rejects it before the API sees it)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.edit_fx.contracts import MOODS
from src.services.ai_edit_service import FX_SETTINGS

ROOT = Path(__file__).resolve().parents[4]


def _read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} is not part of this checkout")
    return path.read_text(encoding="utf-8")


def _quoted(text: str) -> list[str]:
    return re.findall(r'"([a-z_]+)"', text)


def test_typescript_mood_type_and_list():
    types = _read("frontend/src/lib/fx/core/types.ts")
    (union,) = re.findall(r"export type Mood = ([^;]+);", types)
    assert tuple(_quoted(union)) == MOODS
    request = _read("frontend/src/lib/fx/edit-style-request.ts")
    (listed,) = re.findall(r"export const MOODS: readonly Mood\[\] = \[([^\]]+)\];", request)
    assert tuple(_quoted(listed)) == MOODS


def test_mcp_music_moods():
    server = _read("mcp/src/supoclip_mcp/server.py")
    (listed,) = re.findall(r"MUSIC_MOODS = \(([^)]+)\)", server)
    assert tuple(_quoted(listed)) == ("auto", *MOODS)


def test_ai_edit_settings_help_names_every_mood():
    help_text = FX_SETTINGS["fx.music_mood"]
    assert all(re.search(rf"\b{mood}\b", help_text) for mood in ("auto", *MOODS))


def test_drop_in_readme_names_every_mood():
    readme = _read("backend/fx_library/README.txt")
    (line,) = re.findall(r"mood is one of: ([a-z ]+)", readme)
    assert tuple(line.split()) == MOODS
