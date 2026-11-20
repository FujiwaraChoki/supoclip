from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.social_publish_email_service import (
    SocialPublishEmailService,
    SocialPublishRecipient,
)


def email_config(**extra):
    return SimpleNamespace(
        aws_region="eu-west-1",
        aws_access_key_id="k",
        aws_secret_access_key="s",
        ses_from_email="SupoClip <no-reply@supoclip.com>",
        app_base_url="https://app.supoclip.com",
        **extra,
    )


def test_builds_publish_email_with_live_link_and_escaped_fields():
    service = SocialPublishEmailService(email_config())
    content = service._build_clip_published_email(
        recipient=SocialPublishRecipient(
            email="ada@example.com",
            name="Ada <Lovelace>",
            first_name="Ada",
        ),
        provider="youtube",
        title="Why <hooks> win",
        external_url="https://www.youtube.com/shorts/abc123",
        task_id="task-42",
        account_display_name="Ada's Channel",
        account_username="adacodes",
    )

    assert content.subject == "Your clip is live on YouTube Shorts"
    assert "Hi Ada," in content.html
    assert "<strong>Why &lt;hooks&gt; win</strong>" in content.html
    assert "<strong>YouTube Shorts</strong> (Ada&#x27;s Channel (@adacodes))" in content.html
    assert 'href="https://www.youtube.com/shorts/abc123"' in content.html
    assert "View on YouTube Shorts" in content.html
    assert 'href="https://app.supoclip.com/tasks/task-42"' in content.html
    assert 'href="https://app.supoclip.com/settings"' in content.html
    assert 'Your clip "Why <hooks> win" has been published to YouTube Shorts' in content.text
    assert "View on YouTube Shorts: https://www.youtube.com/shorts/abc123" in content.text
    assert "Open in SupoClip: https://app.supoclip.com/tasks/task-42" in content.text
    assert "Notification settings: https://app.supoclip.com/settings" in content.text


def test_builds_publish_email_without_external_url_or_title():
    service = SocialPublishEmailService(email_config())
    content = service._build_clip_published_email(
        recipient=SocialPublishRecipient(email="ada@example.com"),
        provider="tiktok",
        title=None,
        external_url="javascript:alert(1)",
        task_id=None,
        account_display_name=None,
        account_username="@creator",
    )

    assert content.subject == "Your clip is live on TikTok"
    assert "javascript:" not in content.html
    assert "javascript:" not in content.text
    assert "<strong>TikTok</strong> (@creator)" in content.html
    assert 'href="https://app.supoclip.com/analytics"' in content.html
    assert "Open in SupoClip" in content.html
    assert 'href="https://app.supoclip.com/settings"' in content.html


@pytest.mark.asyncio
async def test_send_clip_published_email_delegates_to_ses():
    service = SocialPublishEmailService(email_config())
    service.email_service.send_email = AsyncMock(return_value={"MessageId": "msg-1"})

    result = await service.send_clip_published_email(
        recipient=SocialPublishRecipient(email="ada@example.com", first_name="Ada"),
        provider="instagram",
        title="Launch clip",
        external_url="https://www.instagram.com/reel/xyz/",
        task_id="task-7",
    )

    assert result == {"MessageId": "msg-1"}
    service.email_service.send_email.assert_awaited_once()
    recipient, content = service.email_service.send_email.await_args.args
    assert recipient == "ada@example.com"
    assert content.subject == "Your clip is live on Instagram Reels"
