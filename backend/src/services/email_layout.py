"""Shared HTML shell for outgoing email: logo header, white card, quiet footer.

Builders keep writing plain ``<p>`` fragments; ``SesEmailService`` wraps them
here. Styles are inline because many mail clients drop ``<style>`` blocks.
"""
from __future__ import annotations

import re
from html import escape

HOSTED_SITE_URL = "https://www.supoclip.com"

# Assets live in frontend/public/email-assets. Geist is self-hosted (not Google
# Fonts) so opening an email never sends the reader's IP to a third party. It
# loads in clients that allow web fonts (Apple Mail, iOS); Gmail and Outlook
# use the system stack.
ASSETS_PATH = "/email-assets"
_FONT = (
    "'Geist',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
_FONT_FACES = (
    ("geist-latin.woff2", "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,"
     "U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"),
    ("geist-latin-ext.woff2", "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,"
     "U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF"),
)
_TEXT = "#1c1917"
_MUTED = "#78716c"
_BORDER = "#e7e5e4"
_PAGE = "#f5f5f4"
_PARAGRAPH = f"margin:0 0 16px;font-size:15px;line-height:24px;color:{_TEXT};"
_LINK = f"color:{_TEXT};text-decoration:underline;"
_DOCUMENT = re.compile(r"^\s*(<!doctype|<html)", re.IGNORECASE)


def is_full_document(html: str) -> bool:
    return bool(_DOCUMENT.match(html))


def email_button(url: str, label: str) -> str:
    """A bulletproof (table-based) primary button."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:8px 0 24px;"><tr>'
        f'<td style="border-radius:8px;background:{_TEXT};">'
        # style before href so _inline_styles leaves this link alone.
        f'<a style="display:inline-block;padding:12px 22px;font-family:{_FONT};'
        "font-size:15px;font-weight:600;line-height:20px;color:#fafaf9;"
        "text-decoration:none;border-radius:8px;\" "
        f'href="{escape(url, quote=True)}" target="_blank">{escape(label)}</a>'
        "</td></tr></table>"
    )


def _asset_base(site: str) -> str | None:
    # Mail clients won't load images or fonts from localhost or plain-http hosts;
    # those emails fall back to a text wordmark and system fonts.
    return f"{site}{ASSETS_PATH}" if site.startswith("https://") else None


def _font_style(assets: str | None) -> str:
    if not assets:
        return ""
    faces = "".join(
        "@font-face{font-family:'Geist';font-style:normal;font-weight:400 700;"
        f"font-display:swap;src:url({assets}/{name}) format('woff2');unicode-range:{ranges};}}"
        for name, ranges in _FONT_FACES
    )
    return f"<style>{faces}</style>\n"


def _logo(assets: str | None) -> str:
    # The mark sits on its own white tile so it stays visible when Gmail or
    # Outlook darken the page in dark mode (they never recolour images).
    if not assets:
        return ""
    return (
        f'<img src="{assets}/logo.png" width="32" height="32" alt="" '
        'style="display:inline-block;vertical-align:middle;border:0;">'
    )


def _inline_styles(body: str) -> str:
    body = re.sub(r"<p>", f'<p style="{_PARAGRAPH}">', body)
    return re.sub(r"<a href=", f'<a style="{_LINK}" href=', body)


def render_email_html(body: str, *, app_url: str = "") -> str:
    site = (app_url or HOSTED_SITE_URL).rstrip("/")
    assets = _asset_base(site)
    logo = _logo(assets)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>SupoClip</title>
{_font_style(assets)}</head>
<body style="margin:0;padding:0;background:{_PAGE};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_PAGE};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;font-family:{_FONT};">
<tr><td style="padding:0 4px 20px;">
<a href="{escape(site, quote=True)}" target="_blank" style="text-decoration:none;color:{_TEXT};">
{logo}
<span style="display:inline-block;vertical-align:middle;{"margin-left:10px;" if logo else ""}font-size:17px;font-weight:700;letter-spacing:-0.2px;color:{_TEXT};">SupoClip</span>
</a>
</td></tr>
<tr><td style="background:#ffffff;border:1px solid {_BORDER};border-radius:12px;padding:32px 32px 16px;">
{_inline_styles(body)}
</td></tr>
<tr><td style="padding:20px 4px 0;font-size:12px;line-height:18px;color:{_MUTED};">
SupoClip turns long videos into short clips.<br>
<a href="{escape(site, quote=True)}" target="_blank" style="color:{_MUTED};text-decoration:underline;">{escape(site.split("://", 1)[-1])}</a>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
