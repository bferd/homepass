"""The welcome text and a link's info text on the guest page.

The global welcome text and the link's own text share a card at the top of
the live guest page, unless the link's text is flagged for the footer, in
which case it gets a box of its own below the cards. A pending page shows the
welcome text only. Neither text reaches any other guest response: not /state,
the manifest, a refusal page, the PIN or device screens, or the page's
<title> and meta tags.
"""
import re
import time

import httpx
import pytest

from app import database as db
from app import guest_pin
from app.config import settings

WELCOME = "Welcome to the cottage!\nWi-Fi: Cottage"
OWN = "Door code 4821"
CA_NATIONAL = "613-996-2000"
CA_E164 = "+16139962000"


async def _token(slug="info", info_text=OWN, footer=False, **kw):
    kw.setdefault("expires_at", int(time.time()) + 86400)
    return await db.create_token(
        label=f"Token {slug}", slug=slug, entity_ids=["light.living_room"],
        ip_allowlist=None, info_text=info_text, info_text_in_footer=footer, **kw,
    )


@pytest.fixture
def welcome(monkeypatch):
    def _set(text):
        monkeypatch.setattr(settings, "welcome_text", text)
    _set(WELCOME)
    return _set


@pytest.fixture
def no_welcome(monkeypatch):
    monkeypatch.setattr(settings, "welcome_text", "")


def _block(html: str, element_id: str) -> str | None:
    """The inner text container of the card or footer box, or None."""
    match = re.search(rf'<div id="{element_id}"[^>]*>(.*?)</div>', html, re.S)
    return match.group(1) if match else None


def _top(html):
    return _block(html, "info-card-text")


def _footer(html):
    return _block(html, "info-footer-text")


def _hash(html):
    match = re.search(r'data-info-hash="([0-9a-f]*)"', html)
    return match.group(1) if match else None


async def _page(client, slug="info"):
    resp = await client.get(f"/g/{slug}")
    assert resp.status_code == 200
    return resp.text


# ---------------------------------------------------------------------------
# Where each text goes
# ---------------------------------------------------------------------------

async def test_global_only(client, test_db, mock_ha_client, welcome):
    await _token(info_text="")
    html = await _page(client)
    assert _top(html) == WELCOME
    assert _footer(html) is None


async def test_link_only(client, test_db, mock_ha_client, no_welcome):
    await _token()
    html = await _page(client)
    assert _top(html) == OWN
    assert _footer(html) is None


async def test_both_share_the_card_global_first(client, test_db, mock_ha_client, welcome):
    await _token()
    html = await _page(client)
    assert _top(html) == f"{WELCOME}\n\n{OWN}"
    assert _footer(html) is None


async def test_footer_flag_with_link_text_only(client, test_db, mock_ha_client, no_welcome):
    await _token(footer=True)
    html = await _page(client)
    assert 'id="info-card"' not in html
    assert _footer(html) == OWN


async def test_footer_flag_with_both(client, test_db, mock_ha_client, welcome):
    await _token(footer=True)
    html = await _page(client)
    assert _top(html) == WELCOME
    assert _footer(html) == OWN


async def test_footer_flag_with_global_text_only(client, test_db, mock_ha_client, welcome):
    await _token(info_text="", footer=True)
    html = await _page(client)
    assert _top(html) == WELCOME
    assert 'id="info-footer"' not in html


async def test_neither_renders_nothing(client, test_db, mock_ha_client, no_welcome):
    await _token(info_text="")
    html = await _page(client)
    for marker in ('id="info-card"', 'id="info-footer"', 'class="info-text', "data-info-hash",
                   'aria-label="Information"'):
        assert marker not in html
    # Nothing to link, so no Home Assistant lookup for a region either.
    mock_ha_client["get_country"].assert_not_called()


async def test_the_card_sits_above_the_cards_and_the_footer_below(
    client, test_db, mock_ha_client, welcome
):
    await _token(footer=True)
    html = await _page(client)
    order = [html.index(m) for m in (
        "</header>", 'id="info-card"', 'id="install-banner"', 'id="cards-container"',
        "</main>", 'id="info-footer"', 'id="expired-overlay"',
    )]
    assert order == sorted(order)
    assert 'aria-label="Information"' in html


async def test_the_card_label_is_translated(client, test_db, mock_ha_client, welcome):
    await _token()
    resp = await client.get("/g/info", headers={"Accept-Language": "de"})
    assert 'aria-label="Informationen"' in resp.text


# ---------------------------------------------------------------------------
# Pending page
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("footer", [False, True])
async def test_pending_page_shows_the_welcome_text_only(
    client, test_db, mock_ha_client, welcome, footer
):
    await _token(footer=footer, starts_at=int(time.time()) + 3600)
    html = await _page(client)
    assert 'id="pending-banner"' in html
    assert _top(html) == WELCOME
    assert OWN not in html
    assert 'id="info-footer"' not in html


async def test_pending_page_with_only_link_text_renders_nothing(
    client, test_db, mock_ha_client, no_welcome
):
    await _token(starts_at=int(time.time()) + 3600)
    html = await _page(client)
    assert 'id="info-card"' not in html and OWN not in html


# ---------------------------------------------------------------------------
# Escaping and links on the real page
# ---------------------------------------------------------------------------

async def test_dangerous_markup_stays_inert(client, test_db, mock_ha_client, no_welcome):
    # Formatting tags are allowed now; everything else is still only text.
    await _token(info_text='<script>alert(1)</script> and "><img src=x onerror=alert(1)>')
    text = _top(await _page(client))
    assert "<script>alert(1)" not in text and "<img" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&#34;&gt;&lt;img src=x onerror=alert(1)&gt;" in text


async def test_only_https_links_and_ampersands_are_escaped(
    client, test_db, mock_ha_client, no_welcome
):
    await _token(info_text=(
        "javascript:alert(1) http://plain.example/x "
        "https://example.com/book?a=1&b=2 mail host@example.com"
    ))
    text = _top(await _page(client))
    anchors = re.findall(r"<a [^>]*>", text)
    assert anchors == [
        '<a href="https://example.com/book?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">',
        '<a href="mailto:host@example.com" target="_blank" rel="noopener noreferrer">',
    ]
    assert "javascript:alert(1)" in text and 'href="javascript' not in text
    assert "http://plain.example/x" in text and 'href="http:' not in text


# ---------------------------------------------------------------------------
# Phone region
# ---------------------------------------------------------------------------

async def _tel_links(client):
    return re.findall(r'href="(tel:[^"]+)"', _top(await _page(client)) or "")


async def test_national_number_links_with_the_region_setting(
    client, test_db, mock_ha_client, no_welcome, monkeypatch
):
    monkeypatch.setattr(settings, "phone_region", "CA")
    await _token(info_text=f"Call {CA_NATIONAL}")
    assert await _tel_links(client) == [f"tel:{CA_E164}"]


async def test_national_number_links_with_home_assistants_country(
    client, test_db, mock_ha_client, no_welcome
):
    mock_ha_client["get_country"].return_value = "CA"
    await _token(info_text=f"Call {CA_NATIONAL}")
    assert await _tel_links(client) == [f"tel:{CA_E164}"]


async def test_national_number_links_with_the_time_zone_alone(
    client, test_db, mock_ha_client, no_welcome, monkeypatch
):
    monkeypatch.setattr(settings, "timezone", "America/Toronto")
    await _token(info_text=f"Call {CA_NATIONAL}")
    assert await _tel_links(client) == [f"tel:{CA_E164}"]


async def test_the_region_setting_beats_home_assistant(
    client, test_db, mock_ha_client, no_welcome, monkeypatch
):
    # A UK number that is no number at all in Canada.
    monkeypatch.setattr(settings, "phone_region", "GB")
    mock_ha_client["get_country"].return_value = "CA"
    await _token(info_text="Call 020 7946 0018")
    assert await _tel_links(client) == ["tel:+442079460018"]


async def test_international_number_links_with_no_region(
    client, test_db, mock_ha_client, no_welcome
):
    # The conftest mock: zone UTC, no country, no setting.
    await _token(info_text="Call +1 613 996 2000")
    assert await _tel_links(client) == [f"tel:{CA_E164}"]


async def test_national_number_stays_text_with_no_region(
    client, test_db, mock_ha_client, no_welcome
):
    await _token(info_text=f"Call {CA_NATIONAL}")
    html = await _page(client)
    assert await _tel_links(client) == []
    assert CA_NATIONAL in _top(html)


# ---------------------------------------------------------------------------
# Formatting tags on the real page
# ---------------------------------------------------------------------------

RICH = '<h2>Welcome</h2>\n<b onclick="x">Door</b> code <font color="RED">4821</font>'
RICH_HTML = '<h2>Welcome</h2><b>Door</b> code <font color="red">4821</font>'


async def test_formatting_renders_in_the_card(client, test_db, mock_ha_client, no_welcome):
    await _token(info_text=RICH)
    assert _top(await _page(client)) == RICH_HTML


async def test_formatting_renders_in_the_footer(client, test_db, mock_ha_client, no_welcome):
    await _token(info_text=RICH, footer=True)
    assert _footer(await _page(client)) == RICH_HTML


async def test_formatting_renders_in_the_welcome_text_on_a_pending_page(
    client, test_db, mock_ha_client, welcome
):
    welcome("<i>Hello</i> <script>x</script>")
    await _token(starts_at=int(time.time()) + 3600)
    html = await _page(client)
    assert 'id="pending-banner"' in html
    assert _top(html) == "<i>Hello</i> &lt;script&gt;x&lt;/script&gt;"


async def test_both_texts_are_rendered_on_their_own(client, test_db, mock_ha_client, welcome):
    # A tag left open in the welcome text is closed before the link's text.
    welcome("<b>Hi")
    await _token(info_text="</b>plain")
    assert _top(await _page(client)) == "<b>Hi</b>\n\n&lt;/b&gt;plain"


async def test_stored_text_is_sanitised_on_every_render(client, test_db, mock_ha_client, no_welcome):
    # Written straight to the database, past the API's validation and length
    # check: the page still trusts none of it.
    token = await _token(info_text="ok")
    hostile = '<img src=x onerror=alert(1)><a href="javascript:alert(1)">x</a>' * 40
    await db.set_token_info(token["id"], hostile, None)
    text = _top(await _page(client))
    assert "<img" not in text and "<a href=\"javascript" not in text
    assert text.count("&lt;img src=x onerror=alert(1)&gt;") == 40


# ---------------------------------------------------------------------------
# Collapsed first line
# ---------------------------------------------------------------------------

def _line(html):
    return _block(html, "info-card-line")


async def test_first_line_keeps_its_formatting(client, test_db, mock_ha_client, welcome):
    welcome("\n<b>Welcome</b> to the <i>cottage</i>!\nWi-Fi: Cottage")
    await _token()
    html = await _page(client)
    assert _line(html) == "<b>Welcome</b> to the <i>cottage</i>!"
    assert "Wi-Fi" not in _line(html) and OWN not in _line(html)
    # The full card still holds everything.
    assert _top(html) == "<b>Welcome</b> to the <i>cottage</i>!\nWi-Fi: Cottage\n\n" + OWN


async def test_first_line_heading_list_and_links(client, test_db, mock_ha_client, monkeypatch, no_welcome):
    monkeypatch.setattr(settings, "phone_region", "CA")
    await _token(info_text=f"<ul>\n<li>Call <b>{CA_NATIONAL}</b></li>\n<li>Later item</li>\n</ul>")
    html = await _page(client)
    assert _line(html) == (
        f'<ul><li>Call <b><a href="tel:{CA_E164}" target="_blank" rel="noopener noreferrer">'
        f"{CA_NATIONAL}</a></b></li></ul>"
    )
    assert "Later item" not in _line(html) and "Later item" in _top(html)


async def test_first_line_shows_literal_text_escaped(client, test_db, mock_ha_client, no_welcome):
    await _token(info_text="&lt;b&gt; <script>x</script>\nsecond")
    line = _line(await _page(client))
    assert line == "&amp;lt;b&amp;gt; &lt;script&gt;x&lt;/script&gt;"


@pytest.mark.parametrize("text", ["<b></b><br><ul></ul>", "<i> </i>\n<br>"])
async def test_first_line_falls_back_to_the_label(client, test_db, mock_ha_client, no_welcome, text):
    await _token(info_text=text)
    assert _line(await _page(client)) == "Information"
    resp = await client.get("/g/info", headers={"Accept-Language": "de"})
    assert _line(resp.text) == "Informationen"


async def test_first_line_comes_from_the_link_text_when_welcome_is_only_tags(
    client, test_db, mock_ha_client, welcome
):
    welcome("<b></b>")
    await _token(info_text="Door <u>code</u> 4821\nsecond")
    assert _line(await _page(client)) == "Door <u>code</u> 4821"


async def test_pending_card_has_the_same_strip(client, test_db, mock_ha_client, welcome):
    welcome("<h2>Hello</h2>\nsecond")
    await _token(starts_at=int(time.time()) + 3600)
    html = await _page(client)
    assert 'id="pending-banner"' in html
    assert _line(html) == "<h2>Hello</h2>"


async def test_long_first_line_is_kept_whole_for_the_ellipsis(
    client, test_db, mock_ha_client, no_welcome
):
    long = "word " * 150
    await _token(info_text=long + "\nsecond")
    line = _line(await _page(client))
    assert line == long and "second" not in line


async def test_ids_are_unique_on_a_rich_page(client, test_db, mock_ha_client, welcome):
    welcome("<h1>One</h1><ul><li>a</li></ul>")
    await _token(info_text="<b>x</b>\ny")
    ids = re.findall(r'\sid="([^"]+)"', await _page(client))
    assert len(ids) == len(set(ids))


async def test_footer_box_has_no_first_line(client, test_db, mock_ha_client, no_welcome):
    await _token(info_text="one\ntwo", footer=True)
    html = await _page(client)
    assert 'id="info-card-line"' not in html
    assert _footer(html) == "one\ntwo"


async def test_collapsed_css_swaps_the_text_for_the_line(client, test_db, mock_ha_client, welcome):
    await _token()
    html = await _page(client)
    assert '#info-card[data-collapsed="true"] #info-card-text { display: none; }' in html
    assert '#info-card[data-collapsed="true"] .info-line { display: block; }' in html
    # The strip shares the card's formatting rules and keeps blocks on one line.
    assert re.search(r'<div id="info-card-line" class="info-text info-line ', html)
    assert re.search(r"#info-card-line > \*, #info-card-line li \{ margin: 0; white-space: nowrap; "
                     r"overflow: hidden; text-overflow: ellipsis; \}", html)
    assert "#info-card-line ul, #info-card-line ol { padding-left: 0; list-style-position: inside; }" in html
    assert re.search(r"\.info-line \{ display: none;[^}]*text-overflow: ellipsis;", html)


async def test_hash_follows_the_raw_text_tags_included(
    client, test_db, mock_ha_client, no_welcome
):
    token = await _token(info_text="Door code 4821")
    first = _hash(await _page(client))
    await db.set_token_info(token["id"], "<b>Door code 4821</b>", None)
    assert _hash(await _page(client)) != first


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------

async def test_hash_is_stable_and_follows_both_texts(
    client, test_db, mock_ha_client, welcome
):
    token = await _token()
    first = _hash(await _page(client))
    assert re.fullmatch(r"[0-9a-f]{12}", first)
    assert _hash(await _page(client)) == first

    welcome("Hello again")
    changed_global = _hash(await _page(client))
    assert changed_global != first

    await db.set_token_info(token["id"], "Door code 9999", None)
    changed_own = _hash(await _page(client))
    assert changed_own not in (first, changed_global)


async def test_hash_ignores_text_moved_to_the_footer(client, test_db, mock_ha_client, welcome):
    token = await _token(footer=True)
    first = _hash(await _page(client))
    await db.set_token_info(token["id"], "Door code 9999", None)
    assert _hash(await _page(client)) == first


# ---------------------------------------------------------------------------
# Nowhere else
# ---------------------------------------------------------------------------

def _assert_absent(text: str):
    assert "cottage" not in text.lower()
    assert "4821" not in text


async def test_title_and_meta_tags_never_carry_the_text(
    client, test_db, mock_ha_client, welcome
):
    await _token()
    html = await _page(client)
    assert OWN in html  # the page itself does
    head = html[:html.index("</head>")]
    _assert_absent(head)


async def test_state_and_manifest_never_carry_the_text(
    client, test_db, mock_ha_client, welcome
):
    await _token()
    for path in ("/g/info/state", "/g/info/manifest.json"):
        resp = await client.get(path)
        assert resp.status_code == 200, path
        _assert_absent(resp.text)


async def test_revoked_and_expired_pages_never_carry_the_text(
    client, test_db, mock_ha_client, welcome
):
    revoked = await _token(slug="gone")
    await db.revoke_token(revoked["id"])
    await _token(slug="old", expires_at=int(time.time()) - 60)
    for slug in ("gone", "old"):
        resp = await client.get(f"/g/{slug}")
        assert resp.status_code == 410
        _assert_absent(resp.text)


async def test_ip_refusal_never_carries_the_text(client, test_db, mock_ha_client, welcome):
    await db.create_token(
        label="Token walled", slug="walled", entity_ids=["light.living_room"],
        expires_at=int(time.time()) + 86400, ip_allowlist=["203.0.113.0/24"],
        info_text=OWN,
    )
    resp = await client.get("/g/walled")
    assert resp.status_code == 403
    _assert_absent(resp.text)


async def test_pin_page_never_carries_the_text(client, test_db, mock_ha_client, welcome):
    await _token(pin_hash=await guest_pin.hash_pin("2468"))
    html = await _page(client)
    assert 'name="pin"' in html or "PIN" in html
    _assert_absent(html)
    wrong = await client.post("/g/info/pin", data={"pin": "0000"})
    _assert_absent(wrong.text)


async def test_device_screens_never_carry_the_text(client, test_db, mock_ha_client, welcome):
    from main import app

    await _token(device_binding=True)
    claim = await _page(client)
    assert "/bind" in claim
    _assert_absent(claim)

    bound = await client.post("/g/info/bind")
    assert bound.status_code == 303
    assert OWN in await _page(client)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as other:
        refused = await other.get("/g/info")
    assert refused.status_code == 403
    _assert_absent(refused.text)
