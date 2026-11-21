#!/usr/bin/env python3
"""Emit the emoji set the dynamic-edit atlas must cover (FINAL.md §12).

    TONE_EMOJI values ∪ LLM_ALLOWED_EMOJI ∪ EMOJI_KEYWORD_MAP values

in that order, first occurrence wins, as a JSON array (stdout or `--out`). The order is the atlas cell order, so
it only changes when one of the three sources changes. build_emoji_atlas.ts reads this list.

    cd backend && .venv/bin/python scripts/fx/emoji_list.py --out /tmp/emoji.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.edit_fx.constants import LLM_ALLOWED_EMOJI, TONE_EMOJI  # noqa: E402
from src.emoji_captions import EMOJI_KEYWORD_MAP  # noqa: E402


def emoji_set() -> list[str]:
    ordered: dict[str, None] = {}
    for emoji in (*TONE_EMOJI.values(), *LLM_ALLOWED_EMOJI, *EMOJI_KEYWORD_MAP.values()):
        emoji = emoji.strip()
        if emoji:
            ordered.setdefault(emoji, None)
    return list(ordered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="write the JSON array here instead of stdout")
    args = parser.parse_args(argv)
    payload = json.dumps(emoji_set(), ensure_ascii=False)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
