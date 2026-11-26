"""Campaign images (``campaign_images``): stored PNGs a project shows above its clips."""

from typing import Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..media.campaign_image import NormalizedImage

# A person keeps their most recent images; older ones are removed when a new one arrives.
# A project whose image was pruned renders without it.
KEPT_IMAGES_PER_USER = 20


async def save_campaign_image(db: AsyncSession, user_id: str, image: NormalizedImage) -> str:
    image_id = str(uuid4())
    try:
        await db.execute(
            text(
                """
                INSERT INTO campaign_images (id, user_id, content_type, data, width, height, byte_size)
                VALUES (:id, :user_id, 'image/png', :data, :width, :height, :byte_size)
                """
            ),
            {
                "id": image_id,
                "user_id": user_id,
                "data": image.png,
                "width": image.width,
                "height": image.height,
                "byte_size": len(image.png),
            },
        )
        await db.execute(
            text(
                """
                DELETE FROM campaign_images
                WHERE user_id = :user_id AND id NOT IN (
                    SELECT id FROM campaign_images WHERE user_id = :user_id
                    ORDER BY created_at DESC, id DESC LIMIT :kept
                )
                """
            ),
            {"user_id": user_id, "kept": KEPT_IMAGES_PER_USER},
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return image_id


async def campaign_image_belongs_to(db: AsyncSession, image_id: str, user_id: str) -> bool:
    row = await db.execute(
        text("SELECT 1 FROM campaign_images WHERE id = :id AND user_id = :user_id"),
        {"id": image_id, "user_id": user_id},
    )
    return row.scalar() is not None


async def load_campaign_image(db: AsyncSession, image_id: str) -> Optional[bytes]:
    row = await db.execute(
        text("SELECT data FROM campaign_images WHERE id = :id"), {"id": image_id}
    )
    data = row.scalar()
    return bytes(data) if data is not None else None
