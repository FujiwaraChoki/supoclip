from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Optional

from ..config import Config
from .email_service import EmailContent, ResendEmailService, first_name_for


@dataclass(frozen=True)
class TaskCompletionRecipient:
    email: str
    name: Optional[str] = None
    first_name: Optional[str] = None


class TaskCompletionEmailService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.email_service = ResendEmailService(self.config)
        self.app_url = self.config.app_base_url

    @property
    def is_configured(self) -> bool:
        return self.email_service.is_configured

    async def send_task_completed_email(
        self,
        *,
        recipient: TaskCompletionRecipient,
        task_id: str,
        source_title: Optional[str],
        clips_count: int,
    ) -> dict:
        content = self._build_task_completed_email(
            recipient=recipient,
            task_id=task_id,
            source_title=source_title,
            clips_count=clips_count,
        )
        return await self.email_service.send_email(recipient.email, content)

    def _build_task_completed_email(
        self,
        *,
        recipient: TaskCompletionRecipient,
        task_id: str,
        source_title: Optional[str],
        clips_count: int,
    ) -> EmailContent:
        first_name = first_name_for(
            first_name=recipient.first_name,
            full_name=recipient.name,
        )
        task_url = f"{self.app_url}/tasks/{task_id}"
        clips_label = f"{clips_count} clip" if clips_count == 1 else f"{clips_count} clips"
        safe_source_title = escape(source_title.strip()) if source_title else "your video"
        subject = "Your SupoClip clips are ready"

        return EmailContent(
            subject=subject,
            html=(
                f"<p>Hi {escape(first_name)},</p>"
                f"<p>Your clips for <strong>{safe_source_title}</strong> are ready.</p>"
                f"<p>We generated {clips_label} for you.</p>"
                f'<p><a href="{escape(task_url)}">Open your finished clips</a></p>'
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                f"Your clips for {source_title or 'your video'} are ready.\n"
                f"We generated {clips_label} for you.\n\n"
                f"Open your finished clips: {task_url}\n\n"
                "Team SupoClip"
            ),
        )
