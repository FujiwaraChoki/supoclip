from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Optional

from ..config import Config
from .email_service import EmailContent, SesEmailService, first_name_for


@dataclass(frozen=True)
class TaskCompletionRecipient:
    email: str
    name: Optional[str] = None
    first_name: Optional[str] = None


class TaskCompletionEmailService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.email_service = SesEmailService(self.config)
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
        workflow: str = "highlights",
        topic: str = "",
    ) -> dict:
        content = self._build_task_completed_email(
            recipient=recipient,
            task_id=task_id,
            source_title=source_title,
            clips_count=clips_count,
            workflow=workflow,
            topic=topic,
        )
        return await self.email_service.send_email(recipient.email, content)

    def _build_task_completed_email(
        self,
        *,
        recipient: TaskCompletionRecipient,
        task_id: str,
        source_title: Optional[str],
        clips_count: int,
        workflow: str = "highlights",
        topic: str = "",
    ) -> EmailContent:
        first_name = first_name_for(
            first_name=recipient.first_name,
            full_name=recipient.name,
        )
        task_url = f"{self.app_url}/tasks/{task_id}"
        clips_label = f"{clips_count} clip" if clips_count == 1 else f"{clips_count} clips"
        safe_source_title = escape(source_title.strip()) if source_title else "your video"
        subject = "Your SupoClip clips are ready"
        # What the generation made, as (headline, plain-text headline, count line).
        headline_html = f"Your clips for <strong>{safe_source_title}</strong> are ready."
        headline_text = f"Your clips for {source_title or 'your video'} are ready."
        count_line = f"We generated {clips_label} for you."
        link_label = "Open your finished clips"
        if workflow == "series":
            subject = f"Your {clips_count}-part series is ready"
            headline_html = f"Your {clips_count}-part series from <strong>{safe_source_title}</strong> is ready."
            headline_text = f"Your {clips_count}-part series from {source_title or 'your video'} is ready."
            count_line = "Every part picks up where the last one ended."
            link_label = "Open your series"
        elif workflow in ("recap", "trailer"):
            # "Your recap of the talk", "Your trailer for the talk".
            preposition = "of" if workflow == "recap" else "for"
            subject = f"Your {workflow} is ready"
            headline_html = f"Your {workflow} {preposition} <strong>{safe_source_title}</strong> is ready."
            headline_text = f"Your {workflow} {preposition} {source_title or 'your video'} is ready."
            count_line = (
                "It covers the main points in one short video."
                if workflow == "recap"
                else "A short preview that makes people want to watch the whole video."
            )
            link_label = f"Open your {workflow}"
        elif workflow == "topic" and topic.strip():
            subject = f"Your clips about {topic.strip()} are ready"
            headline_html = (
                f"Your clips about <strong>{escape(topic.strip())}</strong> "
                f"from <strong>{safe_source_title}</strong> are ready."
            )
            headline_text = f"Your clips about {topic.strip()} from {source_title or 'your video'} are ready."

        return EmailContent(
            subject=subject,
            html=(
                f"<p>Hi {escape(first_name)},</p>"
                f"<p>{headline_html}</p>"
                f"<p>{escape(count_line)}</p>"
                f'<p><a href="{escape(task_url)}">{escape(link_label)}</a></p>'
                "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                f"{headline_text}\n"
                f"{count_line}\n\n"
                f"{link_label}: {task_url}\n\n"
                "Team SupoClip"
            ),
        )
