"""Turn a run of the admin's info text into HTML with clickable links.

The welcome text and a link's info text are typed by an admin, and what they
hold is mostly things a guest wants to tap: a phone number to call, an email
address, a page with check-in instructions. So addresses, emails and phone
numbers become links, and nothing else in the text can ever become markup.
app/richtext.py calls this for the text between the formatting tags it
allows; a tag's own text never comes here.

Detection runs on the RAW text and the output is assembled one segment at a
time: plain runs go through markupsafe.escape, and each anchor is built from
an escaped href and escaped link text. Escaping first and matching afterwards
would break both halves: `&` in a query string turns into `&amp;` and ends up
inside the href twice-encoded, and the phone matcher would read entity
characters as part of a number. The result is a Markup the template prints
as it is, so no template ever needs `|safe`.

Matching order is URLs, then emails, then phone numbers only in the text left
over. A URL can contain an "@" and long runs of digits, and an email can
contain digits, and none of those may turn into a second, nested link.
"""
import re
from functools import lru_cache

import phonenumbers
from markupsafe import Markup, escape

# https only. An http: link would send a guest to a page anyone on the path can
# rewrite, and javascript:, data: and the like are not links at all. They stay
# in the text, escaped, exactly as typed. Stops at whitespace and at characters
# that cannot sit unencoded in a URL anyway, so a URL in quotes or angle
# brackets does not swallow its closing delimiter.
_URL = re.compile(r"https://[^\s<>\"'`]+", re.IGNORECASE)

# Deliberately conservative: a common local part, a dotted domain, and an
# alphabetic top-level domain. An address this misses stays readable text; one
# it wrongly matched would be a link to nowhere.
_EMAIL = re.compile(
    r"(?<![\w.%+-])"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
    r"(?![\w@-])"
)

# Sentence punctuation that follows a URL far more often than it ends one.
_TRAILING = ".,;:!?'\""

# Links leave the guest page, so they open in a new tab and the opened page
# gets no handle back to this one. The app already sends Referrer-Policy:
# no-referrer; noreferrer here says the same for this link on its own.
_ANCHOR_ATTRS = 'target="_blank" rel="noopener noreferrer"'

# One entry per distinct (text, region). An install has one welcome text and a
# handful of links, so this is generous; it only bounds a pathological case.
_CACHE_SIZE = 256


def _trim_url(url: str) -> str:
    """Drop trailing punctuation that belongs to the sentence, not the URL.

    A closing parenthesis is kept only while it balances an opening one inside
    the URL, so "(see https://x.ca/a_(b))" keeps the "(b)" and loses the last ")".
    """
    while url:
        last = url[-1]
        if last in _TRAILING:
            url = url[:-1]
        elif last == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        else:
            break
    return url


def _anchor(href: str, text: str) -> str:
    return f'<a href="{escape(href)}" {_ANCHOR_ATTRS}>{escape(text)}</a>'


def _gaps(length: int, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The stretches of [0, length) that no span covers, in order."""
    gaps, pos = [], 0
    for start, end in sorted(spans):
        if start > pos:
            gaps.append((pos, start))
        pos = max(pos, end)
    if pos < length:
        gaps.append((pos, length))
    return gaps


def _url_links(text: str) -> list[tuple[int, int, str]]:
    links = []
    for m in _URL.finditer(text):
        url = _trim_url(m.group())
        # "https://" with nothing after it once punctuation is gone is no link.
        if len(url) > len("https://"):
            links.append((m.start(), m.start() + len(url), _anchor(url, url)))
    return links


def _email_links(text: str, gaps: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    links = []
    for gap_start, gap_end in gaps:
        for m in _EMAIL.finditer(text, gap_start, gap_end):
            links.append((m.start(), m.end(), _anchor(f"mailto:{m.group()}", m.group())))
    return links


def _phone_links(
    text: str, gaps: list[tuple[int, int]], region: str | None
) -> list[tuple[int, int, str]]:
    """Phone numbers, found by libphonenumber at the VALID leniency.

    VALID, not POSSIBLE: a number has to be one the numbering plan actually
    assigns, so an order number or a door code that merely has the right
    length stays text. With no region only numbers written with a leading +
    can be read, which is the honest answer when the house's country is
    unknown. Each gap is matched on its own so a match can never straddle a
    URL or an email.
    """
    links = []
    for gap_start, gap_end in gaps:
        segment = text[gap_start:gap_end]
        matcher = phonenumbers.PhoneNumberMatcher(
            segment, region, leniency=phonenumbers.Leniency.VALID
        )
        for match in matcher:
            e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
            links.append((
                gap_start + match.start,
                gap_start + match.end,
                _anchor(f"tel:{e164}", match.raw_string),
            ))
    return links


@lru_cache(maxsize=_CACHE_SIZE)
def linkify(text: str, region: str | None) -> Markup:
    """The text as safe HTML, with https URLs, emails and phone numbers linked.

    `region` is the ISO 3166-1 alpha-2 code national phone numbers are read
    in (see app/region.py), or None to link only numbers that start with +.
    Newlines are kept as they are; the page shows them with CSS
    white-space: pre-line rather than <br>, so this never emits a tag that is
    not an anchor. Formatting tags are app/richtext.py's job.
    """
    links = _url_links(text)
    links += _email_links(text, _gaps(len(text), [(s, e) for s, e, _ in links]))
    links += _phone_links(text, _gaps(len(text), [(s, e) for s, e, _ in links]), region)

    out, pos = [], 0
    for start, end, html in sorted(links):
        out.append(str(escape(text[pos:start])))
        out.append(html)
        pos = end
    out.append(str(escape(text[pos:])))
    return Markup("".join(out))
