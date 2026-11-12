from src.services.email_layout import email_button, is_full_document, render_email_html


def test_wraps_fragment_with_logo_and_styled_paragraphs():
    html = render_email_html('<p>Hi <a href="https://x.test">there</a></p>', app_url="https://app.test")
    assert is_full_document(html)
    assert 'src="https://app.test/email-assets/logo.png"' in html
    assert "url(https://app.test/email-assets/geist-latin.woff2)" in html
    assert "googleapis" not in html
    assert '<p style="' in html and '<a style="' in html


def test_plain_http_hosts_get_a_text_wordmark_and_their_own_link():
    html = render_email_html("<p>x</p>", app_url="http://clips.lan:3107")
    assert "<img" not in html and "@font-face" not in html
    assert 'href="http://clips.lan:3107"' in html and "supoclip.com" not in html


def test_button_escapes_and_keeps_its_own_style():
    button = email_button('https://x.test/?a=1&b="2"', "Go <now>")
    assert "a=1&amp;b=&quot;2&quot;" in button and "Go &lt;now&gt;" in button
    assert render_email_html(button).count('style="display:inline-block;padding') == 1
