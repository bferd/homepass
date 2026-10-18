"""Info text to HTML (app/linkify.py).

What is pinned here: nothing the admin types can become markup other than an
anchor this module built, only https URLs, emails and real phone numbers turn
into links, and no link ever lands inside another.

Every phone number used below is checked with phonenumbers.is_valid_number
(or shown to be invalid) by the tests themselves, so a metadata update that
reassigns one fails loudly here instead of quietly changing what a test means.
"""
import re

import phonenumbers
import pytest
from markupsafe import Markup

from app.linkify import linkify

# Canada's federal government switchboard area: a real, valid NANP number.
CA_NATIONAL = "613-996-2000"
CA_E164 = "+16139962000"
UK_INTERNATIONAL = "+44 20 7946 0958"
UK_E164 = "+442079460958"


def _valid(number: str, region: str | None) -> bool:
    try:
        return phonenumbers.is_valid_number(phonenumbers.parse(number, region))
    except phonenumbers.NumberParseException:
        return False


def _hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]*)"', html)


def test_the_test_numbers_are_what_they_claim():
    assert _valid(CA_NATIONAL, "CA")
    assert _valid(UK_INTERNATIONAL, None)
    assert not _valid("+1 555", None)
    assert not _valid("123456789", "CA")
    assert not _valid("4165550199123", "CA")


def test_returns_markup():
    assert isinstance(linkify("hello", None), Markup)
    assert isinstance(linkify("", "CA"), Markup)


# ---------------------------------------------------------------------------
# Nothing typed becomes markup
# ---------------------------------------------------------------------------

def test_script_tags_are_escaped():
    out = str(linkify("<script>alert(1)</script>", None))
    assert "<script" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_attribute_breakout_is_escaped():
    out = str(linkify('"><img src=x onerror=alert(1)>', None))
    assert "<img" not in out
    assert "&#34;&gt;&lt;img" in out


@pytest.mark.parametrize("text", [
    "javascript:alert(1)",
    "http://example.com/page",
    "data:text/html,<b>x</b>",
    "ftp://example.com/file",
])
def test_only_https_urls_link(text):
    out = str(linkify(text, None))
    assert "<a " not in out
    assert "<b>" not in out


def test_https_scheme_alone_is_not_a_link():
    assert "<a " not in str(linkify("https://.", None))


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def test_ampersand_in_a_query_is_escaped_once():
    out = str(linkify("https://example.com/a?b=1&c=2", None))
    assert _hrefs(out) == ["https://example.com/a?b=1&amp;c=2"]
    assert "&amp;amp;" not in out


@pytest.mark.parametrize("trailer", [".", ",", ";", ":", "!", "?", ")", "?!", ")."])
def test_trailing_punctuation_stays_outside_the_link(trailer):
    out = str(linkify(f"See https://example.com/path{trailer}", None))
    assert _hrefs(out) == ["https://example.com/path"]
    assert out.endswith(f"</a>{trailer}")


def test_balanced_parenthesis_stays_in_the_url():
    out = str(linkify("(see https://en.wikipedia.org/wiki/Foo_(bar))", None))
    assert _hrefs(out) == ["https://en.wikipedia.org/wiki/Foo_(bar)"]
    assert out.endswith("</a>)")


def test_url_followed_by_a_phone_number():
    out = str(linkify(f"Book at https://example.com/book or call {CA_NATIONAL}.", "CA"))
    assert _hrefs(out) == ["https://example.com/book", f"tel:{CA_E164}"]


def test_digits_inside_a_url_are_not_a_phone_number():
    out = str(linkify("https://example.com/16139962000/info", "CA"))
    assert _hrefs(out) == ["https://example.com/16139962000/info"]
    assert "tel:" not in out


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

def test_email_in_a_sentence():
    out = str(linkify("Questions? Write to host@example.com, any time.", None))
    assert _hrefs(out) == ["mailto:host@example.com"]
    assert ">host@example.com</a>, any time." in out


def test_email_inside_a_url_is_not_linked_twice():
    out = str(linkify("https://example.com/contact?to=host@example.com", None))
    assert _hrefs(out) == ["https://example.com/contact?to=host@example.com"]


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

def test_plus_number_links_without_a_region():
    out = str(linkify(f"Call {UK_INTERNATIONAL} if locked out", None))
    assert _hrefs(out) == [f"tel:{UK_E164}"]
    # The link text is the number as the admin wrote it.
    assert f">{UK_INTERNATIONAL}</a>" in out


def test_national_number_needs_a_region():
    assert _hrefs(str(linkify(f"Call {CA_NATIONAL}", "CA"))) == [f"tel:{CA_E164}"]
    assert "<a " not in str(linkify(f"Call {CA_NATIONAL}", None))


@pytest.mark.parametrize("text", ["Call +1 555 now", "Dial 911", "Ext. 42"])
def test_invalid_or_short_numbers_do_not_link(text):
    assert "<a " not in str(linkify(text, "CA"))


@pytest.mark.parametrize("text", [
    "Order #123456789 has shipped",
    "Booking ref 4165550199123",
    "Door code 1234#",
])
def test_order_numbers_do_not_link(text):
    assert "<a " not in str(linkify(text, "CA"))


# ---------------------------------------------------------------------------
# Shape of the output
# ---------------------------------------------------------------------------

def test_newlines_are_kept():
    out = str(linkify(f"Welcome!\nWi-Fi: Cottage\n\nCall {UK_INTERNATIONAL}", None))
    assert out.startswith("Welcome!\nWi-Fi: Cottage\n\nCall <a ")
    assert "<br" not in out


def test_every_anchor_opens_safely():
    out = str(linkify(
        f"https://example.com host@example.com {UK_INTERNATIONAL} or {CA_NATIONAL}", "CA"
    ))
    anchors = re.findall(r"<a [^>]*>", out)
    assert len(anchors) == 4
    for a in anchors:
        assert 'target="_blank"' in a
        assert 'rel="noopener noreferrer"' in a
