from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Optional

from ..config import Config
from .email_layout import email_button
from .email_previews import MAX_CLIP_PREVIEWS, TaskPreviews
from .email_service import EmailContent, InlineImage, SesEmailService, first_name_for

# Display widths; the JPEGs are about 2x for sharp retina rendering.
_SOURCE_WIDTH = 320
_CLIP_WIDTH = 100


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
        previews: Optional[TaskPreviews] = None,
    ) -> dict:
        content = self._build_task_completed_email(
            recipient=recipient,
            task_id=task_id,
            source_title=source_title,
            clips_count=clips_count,
            previews=previews,
        )
        return await self.email_service.send_email(recipient.email, content)

    def _build_task_completed_email(
        self,
        *,
        recipient: TaskCompletionRecipient,
        task_id: str,
        source_title: Optional[str],
        clips_count: int,
        previews: Optional[TaskPreviews] = None,
    ) -> EmailContent:
        first_name = first_name_for(
            first_name=recipient.first_name,
            full_name=recipient.name,
        )
        task_url = f"{self.app_url}/tasks/{task_id}"
        clips_label = f"{clips_count} clip" if clips_count == 1 else f"{clips_count} clips"
        safe_source_title = escape(source_title.strip()) if source_title else "your video"
        subject = "Your SupoClip clips are ready"
        previews = previews or TaskPreviews()
        clip_titles = [clip.title.strip() for clip in previews.clips if clip.title and clip.title.strip()]

        return EmailContent(
            subject=subject,
            html=(
                self._source_preview_html(previews, task_url, source_title)
                + f"<p>Hi {escape(first_name)},</p>"
                f"<p>Your clips for <strong>{safe_source_title}</strong> are ready.</p>"
                f"<p>We generated {clips_label} for you.</p>"
                + self._clip_previews_html(previews, task_url)
                + email_button(task_url, "Open your clips")
                + "<p>Team SupoClip</p>"
            ),
            text=(
                f"Hi {first_name},\n\n"
                f"Your clips for {source_title or 'your video'} are ready.\n"
                f"We generated {clips_label} for you.\n\n"
                + "".join(f"- {title}\n" for title in clip_titles)
                + ("\n" if clip_titles else "")
                + f"Open your finished clips: {task_url}\n\n"
                "Team SupoClip"
            ),
            inline_images=(
                ((InlineImage("source@supoclip", previews.source),) if previews.source else ())
                + tuple(InlineImage(f"clip{i}@supoclip", clip.image) for i, clip in enumerate(previews.clips))
            ),
        )

    @staticmethod
    def _source_preview_html(
        previews: TaskPreviews, task_url: str, source_title: Optional[str]
    ) -> str:
        if not previews.source:
            return ""
        return (
            f'<a style="display:block;margin:0 0 24px;" href="{escape(task_url)}" target="_blank">'
            f'<img src="cid:source@supoclip" width="{_SOURCE_WIDTH}" '
            f'alt="{escape((source_title or "").strip() or "Your video")}" '
            f'style="display:block;width:100%;max-width:{_SOURCE_WIDTH}px;height:auto;border:0;border-radius:8px;"></a>'
        )

    @staticmethod
    def _clip_previews_html(previews: TaskPreviews, task_url: str) -> str:
        if not previews.clips:
            return ""
        cells = []
        for i, clip in enumerate(previews.clips):
            title = (
                '<p style="margin:6px 0 0;font-size:12px;line-height:16px;color:#44403c;">'
                f"{escape(clip.title.strip())}</p>"
                if clip.title and clip.title.strip()
                else ""
            )
            cells.append(
                # Percentage cells so three clips shrink to fit narrow phone screens.
                f'<td width="33%" valign="top" style="width:33%;padding:0 10px 0 0;">'
                f'<a style="display:block;" href="{escape(task_url)}" target="_blank">'
                f'<img src="cid:clip{i}@supoclip" width="{_CLIP_WIDTH}" '
                f'alt="{escape((clip.title or "").strip() or f"Clip {i + 1}")}" '
                f'style="display:block;width:100%;max-width:{_CLIP_WIDTH}px;height:auto;border:0;border-radius:8px;"></a>'
                f"{title}</td>"
            )
        # Empty cells keep one or two clips at the same size as three.
        cells += ['<td width="33%"></td>'] * (MAX_CLIP_PREVIEWS - len(cells))
        return (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            # max-width keeps the row compact while still shrinking on phones.
            f'width="100%" style="max-width:{MAX_CLIP_PREVIEWS * (_CLIP_WIDTH + 10)}px;margin:4px 0 16px;table-layout:fixed;"><tr>{"".join(cells)}</tr></table>'
        )
