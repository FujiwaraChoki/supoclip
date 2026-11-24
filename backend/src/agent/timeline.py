"""The transcript as the page shows it.

Raw content blocks hold context blocks, thinking and tool plumbing the person
never sees. This turns them into a short list of items: what the person said,
what the agent said, each step it took (with its card), and notices from
SupoClip (a project finished).
"""

from __future__ import annotations

from typing import Any, Optional

from .loop import input_brief


def build(rows: list[dict], open_run: Optional[dict]) -> list[dict]:
    cards: dict[str, dict] = {}
    for row in rows:
        display = row.get("display") or {}
        for tool_use_id, entry in (display.get("cards") or {}).items():
            cards[tool_use_id] = entry
    pending = {}
    if open_run and open_run.get("status") == "awaiting_approval":
        pending_cards = (open_run.get("pending") or {}).get("cards") or {}
        cards.update(pending_cards)
        pending = {item["id"]: item for item in (open_run.get("pending") or {}).get("items") or []}

    items: list[dict[str, Any]] = []
    for row in rows:
        display = row.get("display") or {}
        if row["role"] == "user":
            kind = display.get("kind")
            if kind == "user":
                items.append(
                    {
                        "type": "user",
                        "id": f"m{row['id']}",
                        "text": display.get("text") or "",
                        "attachments": display.get("attachments") or [],
                        "created_at": row.get("created_at"),
                    }
                )
            elif kind == "notice":
                items.append(
                    {
                        "type": "notice",
                        "id": f"m{row['id']}",
                        "notice": display.get("notice") or {},
                        "created_at": row.get("created_at"),
                    }
                )
            continue
        for index, block in enumerate(row.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and (block.get("text") or "").strip():
                if items and items[-1]["type"] == "text" and items[-1].get("run_id") == row.get("run_id"):
                    items[-1]["text"] += "\n\n" + block["text"]
                else:
                    items.append(
                        {"type": "text", "id": f"m{row['id']}-{index}", "text": block["text"], "run_id": row.get("run_id")}
                    )
            elif block.get("type") == "tool_use":
                entry = cards.get(block.get("id"))
                status = "done"
                if entry is None:
                    status = "awaiting_approval" if block.get("id") in pending else (
                        "running" if open_run and open_run.get("status") in {"queued", "running"} else "interrupted"
                    )
                elif entry.get("declined"):
                    status = "declined"
                elif not entry.get("ok"):
                    status = "error"
                items.append(
                    {
                        "type": "tool",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": input_brief(block.get("name", ""), block.get("input")),
                        "status": status,
                        "card": (entry or {}).get("card"),
                        "error": (entry or {}).get("error"),
                        **({"approval": pending[block["id"]]} if block.get("id") in pending else {}),
                    }
                )
    return items
