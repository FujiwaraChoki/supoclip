"""Public, signed, product-update-only opt-outs. GET never changes preferences."""
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...services.product_update_email_service import (
    InvalidUnsubscribeToken, ProductUpdateEmailService, ProductUpdateEmailsNotConfigured, UNSUBSCRIBE_PATH,
)

router = APIRouter(tags=["email preferences"])
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
}


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{escape(title)} · SupoClip</title>
<style>body{{margin:0;background:#f8f7f4;color:#252321;font:16px/1.7 Arial,sans-serif}}
main{{max-width:460px;margin:12vh auto;padding:32px 24px}}.brand{{font-weight:bold;font-size:24px;margin-bottom:40px}}
h1{{font-size:30px;line-height:1.2;letter-spacing:-.8px}}p{{color:#625d56}}
button{{background:#252321;color:white;border:0;border-radius:7px;padding:14px 20px;font:inherit;cursor:pointer}}
a{{color:#625d56}}button:focus-visible,a:focus-visible{{outline:3px solid #a35325;outline-offset:4px}}
</style></head><body><main><div class="brand">SupoClip.</div><h1>{escape(title)}</h1>{body}</main></body></html>''',
        status_code=status_code, headers=HEADERS)


def _invalid() -> HTMLResponse:
    return _page("This link isn’t valid", "<p>Open the unsubscribe link in your latest SupoClip product email, or contact support@supoclip.com for help.</p>", 400)


def _unavailable() -> HTMLResponse:
    return _page("We couldn’t update your preference", "<p>Please try again later, or contact support@supoclip.com.</p>", 503)


@router.get(UNSUBSCRIBE_PATH, response_class=HTMLResponse)
async def confirm_unsubscribe(token: str = "", db: AsyncSession = Depends(get_db)):
    service = ProductUpdateEmailService(db)
    try:
        recipient = await service.validate_token(token)
    except InvalidUnsubscribeToken:
        return _invalid()
    except ProductUpdateEmailsNotConfigured:
        return _unavailable()
    if recipient["product_updates_unsubscribed_at"] is not None:
        return _success()
    action = escape(f"{UNSUBSCRIBE_PATH}?{urlencode({'token': token})}", quote=True)
    return _page("Unsubscribe from product updates?", f'''<p>You’ll stop receiving feature announcements and other SupoClip product updates.</p>
<p>This won’t change your account, security, billing, or clip-ready email preferences.</p>
<form method="post" action="{action}"><button type="submit">Unsubscribe from product updates</button></form>''')


def _success() -> HTMLResponse:
    return _page("You’re unsubscribed", "<p>You won’t receive further SupoClip product updates.</p><p>Your other email preferences are unchanged.</p><p><a href=\"/\">Back to SupoClip</a></p>")


@router.post(UNSUBSCRIBE_PATH, response_class=HTMLResponse)
async def unsubscribe(token: str = "", db: AsyncSession = Depends(get_db)):
    # Both the confirmation form and RFC 8058 one-click clients use POST.
    # No session, cookies, redirect, or extra request fields are required.
    try:
        await ProductUpdateEmailService(db).unsubscribe(token)
    except InvalidUnsubscribeToken:
        return _invalid()
    except ProductUpdateEmailsNotConfigured:
        return _unavailable()
    return _success()
