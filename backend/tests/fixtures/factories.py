from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError


async def create_user(
    session,
    *,
    user_id: str | None = None,
    email: str | None = None,
    name: str = "Test User",
    is_admin: bool = False,
    plan: str = "free",
    subscription_status: str = "inactive",
):
    user_id = user_id or str(uuid4())
    email = email or f"{user_id}@example.com"
    now = datetime.now(timezone.utc)
    await session.execute(
        text("DELETE FROM users WHERE id = :id OR email = :email"),
        {"id": user_id, "email": email},
    )
    await session.execute(
        text(
            """
            INSERT INTO users (
                id, name, email, "emailVerified", "createdAt", "updatedAt",
                is_admin, plan, subscription_status
            ) VALUES (
                :id, :name, :email, false, :created_at, :updated_at,
                :is_admin, :plan, :subscription_status
            )
            """
        ),
        {
            "id": user_id,
            "name": name,
            "email": email,
            "created_at": now,
            "updated_at": now,
            "is_admin": is_admin,
            "plan": plan,
            "subscription_status": subscription_status,
        },
    )
    await session.commit()
    return {
        "id": user_id,
        "email": email,
        "name": name,
    }


async def create_source(
    session,
    *,
    source_id: str | None = None,
    title: str = "Seeded Source",
    source_type: str = "youtube",
    url: str = "https://www.youtube.com/watch?v=seeded",
):
    source_id = source_id or str(uuid4())
    try:
        await session.execute(
            text(
                """
                INSERT INTO sources (id, type, title, url, created_at, updated_at)
                VALUES (:id, :type, :title, :url, NOW(), NOW())
                """
            ),
            {
                "id": source_id,
                "type": source_type,
                "title": title,
                "url": url,
            },
        )
    except ProgrammingError:
        await session.rollback()
        await session.execute(
            text(
                """
                INSERT INTO sources (id, type, title, created_at, updated_at)
                VALUES (:id, :type, :title, NOW(), NOW())
                """
            ),
            {
                "id": source_id,
                "type": source_type,
                "title": title,
            },
        )
    await session.commit()
    return {"id": source_id, "title": title}


async def create_task(
    session,
    *,
    task_id: str | None = None,
    user_id: str,
    source_id: str,
    status: str = "completed",
):
    task_id = task_id or str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO tasks (
                id, user_id, source_id, generated_clips_ids, status,
                font_family, font_size, font_color, created_at, updated_at
            ) VALUES (
                :id, :user_id, :source_id, ARRAY[]::VARCHAR(36)[], :status,
                'TikTokSans-Regular', 24, '#FFFFFF', NOW(), NOW()
            )
            """
        ),
        {
            "id": task_id,
            "user_id": user_id,
            "source_id": source_id,
            "status": status,
        },
    )
    await session.commit()
    return {"id": task_id}


async def create_clip(
    session,
    *,
    clip_id: str | None = None,
    task_id: str,
    text_value: str = "Seeded clip",
):
    clip_id = clip_id or str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO generated_clips (
                id, task_id, filename, file_path, start_time, end_time, duration,
                text, relevance_score, reasoning, clip_order, created_at, updated_at
            ) VALUES (
                :id, :task_id, 'seeded.mp4', '/tmp/seeded.mp4', '00:00', '00:10', 10,
                :text, 0.95, 'Seeded clip', 1, NOW(), NOW()
            )
            """
        ),
        {
            "id": clip_id,
            "task_id": task_id,
            "text": text_value,
        },
    )
    await session.execute(
        text("UPDATE tasks SET generated_clips_ids = ARRAY[:clip_id]::VARCHAR(36)[] WHERE id = :task_id"),
        {"clip_id": clip_id, "task_id": task_id},
    )
    await session.commit()
    return {"id": clip_id}
