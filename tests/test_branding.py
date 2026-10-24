"""Per-link branding: a logo, an eyebrow, a title and optional colours.

Real DB (migrated per test) and real routing; only ha_client is mocked. The
logo checks run on bytes built here, so no image library is needed.
"""
import struct
import time
import zlib
from unittest.mock import patch

import pytest

from app import branding
from app import database as db
from app.config import settings

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}


# ---------------------------------------------------------------------------
# Image bytes
# ---------------------------------------------------------------------------

def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(w=64, h=64, extra=b"", trailing=b"") -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + extra
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 8)) + _chunk(b"IEND", b"") + trailing
    )


def _riff(chunks: bytes) -> bytes:
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WEBP" + chunks


def webp_lossless(w=64, h=64) -> bytes:
    bits = (w - 1) | ((h - 1) << 14)
    body = b"\x2f" + struct.pack("<I", bits) + b"\x00" * 11
    return _riff(b"VP8L" + struct.pack("<I", len(body)) + body)


def webp_extended(w=64, h=64, flags=0x10) -> bytes:
    body = bytes([flags]) + b"\x00" * 3 + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return _riff(b"VP8X" + struct.pack("<I", len(body)) + body)


# ---------------------------------------------------------------------------
# Text and colours
# ---------------------------------------------------------------------------

def test_clean_line_keeps_case_and_flattens_control_characters():
    assert branding.clean_line("  Be-Nith \n\tCottage ") == "Be-Nith Cottage"
    assert branding.clean_line("a\u202eb") == "a b"
    assert branding.clean_line(None) is None


def test_clean_colors_full_set_or_none():
    assert branding.clean_colors(None) is None
    out = branding.clean_colors({"brand_primary": "#aa0000", "brand_bg": "#ffffff"})
    assert out["brand_primary"] == "#AA0000"
    assert out["brand_accent"] == ""
    assert set(out) == set(branding.COLOR_KEYS)


@pytest.mark.parametrize("bad", [
    {"brand_primary": "#aa0000"},                                  # background missing
    {"brand_primary": "red", "brand_bg": "#ffffff"},
    {"brand_primary": "#aa0000", "brand_bg": "#ffffff", "x": "#000000"},
])
def test_clean_colors_rejects(bad):
    with pytest.raises(ValueError):
        branding.clean_colors(bad)


# ---------------------------------------------------------------------------
# Logo validation
# ---------------------------------------------------------------------------

def test_png_and_webp_accepted_with_their_size():
    assert branding.inspect_logo(png(64, 40)) == ("image/png", 64, 40)
    assert branding.inspect_logo(webp_lossless(120, 80)) == ("image/webp", 120, 80)
    assert branding.inspect_logo(webp_extended(200, 100, flags=0x10)) == ("image/webp", 200, 100)


@pytest.mark.parametrize("data", [
    b"",
    b"<svg xmlns='http://www.w3.org/2000/svg'/>",
    b"GIF89a" + b"\x00" * 40,
    png()[:-8],                                                    # cut short
    png()[:23] + b"\x41" + png()[24:],                             # bad CRC
    png(trailing=b"junk"),
    png(extra=_chunk(b"acTL", b"\x00" * 8)),                       # animated
    webp_extended(flags=0x02),                                     # animation flag
    webp_extended()[:-3],
])
def test_bad_logos_refused(data):
    with pytest.raises(branding.LogoError):
        branding.inspect_logo(data)


@pytest.mark.parametrize("w,h", [(31, 64), (64, 31), (1025, 64), (64, 1025)])
def test_logo_dimensions_bounded(w, h):
    with pytest.raises(branding.LogoError):
        branding.inspect_logo(png(w, h))


def test_logo_size_bounded():
    with pytest.raises(branding.LogoError):
        branding.inspect_logo(png(extra=_chunk(b"tEXt", b"x" * (branding.LOGO_MAX_BYTES))))


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

async def _make(slug="brand", ip_allowlist=None, **kwargs):
    return await db.create_token(
        label="Cottage", slug=slug, entity_ids=["light.living_room"],
        expires_at=int(time.time()) + 3600, ip_allowlist=ip_allowlist, **kwargs,
    )


async def _upload(client, admin_session, token_id, data, name="logo.png", ctype="image/png"):
    return await client.post(
        f"/admin/tokens/{token_id}/logo", files={"file": (name, data, ctype)}, cookies=admin_session,
    )


async def test_create_with_branding_and_read_back(client, admin_session):
    resp = await client.post("/admin/tokens", cookies=admin_session, json={
        "label": "Cottage", "entity_ids": ["light.living_room"], "expires_in_seconds": 3600,
        "branding": {"eyebrow": "Be-Nith Cottage", "title": "Welcome", "logo_height": 60},
    })
    assert resp.status_code in (200, 201), resp.text
    b = resp.json()["branding"]
    assert (b["eyebrow"], b["title"], b["logo_height"], b["colors"], b["logo"]) == (
        "Be-Nith Cottage", "Welcome", 60, None, None)


async def test_branding_defaults_to_empty(client, admin_session):
    t = await _make("plain")
    got = (await client.get(f"/admin/tokens/{t['id']}", cookies=admin_session)).json()
    assert got["branding"]["eyebrow"] == "" and got["branding"]["logo"] is None


@pytest.mark.parametrize("body", [
    {"eyebrow": "x" * 41},
    {"title": "x" * 81},
    {"logo_height": 23},
    {"logo_height": 97},
    {"logo_height": "40"},
    {"colors": {"brand_primary": "#aa0000"}},
])
async def test_branding_patch_validation(client, admin_session, body):
    t = await _make()
    resp = await client.patch(f"/admin/tokens/{t['id']}/branding", json=body, cookies=admin_session)
    assert resp.status_code == 422


async def test_branding_requires_admin(client):
    t = await _make()
    assert (await client.patch(f"/admin/tokens/{t['id']}/branding", json={"title": "x"})).status_code in (401, 403)
    assert (await client.post(f"/admin/tokens/{t['id']}/logo", files={"file": ("a.png", png())})).status_code in (401, 403)
    assert (await client.delete(f"/admin/tokens/{t['id']}/logo")).status_code in (401, 403)


async def test_patch_keeps_unsent_fields_and_null_colors_means_global(client, admin_session):
    t = await _make()
    own = {"brand_primary": "#112233", "brand_bg": "#EEEEEE"}
    r = await client.patch(f"/admin/tokens/{t['id']}/branding", cookies=admin_session,
                           json={"eyebrow": "Hi", "title": "There", "colors": own})
    assert r.status_code == 200, r.text
    r = await client.patch(f"/admin/tokens/{t['id']}/branding", cookies=admin_session, json={"title": "Again"})
    b = r.json()["branding"]
    assert (b["eyebrow"], b["title"]) == ("Hi", "Again")
    assert b["colors"]["brand_primary"] == "#112233"
    r = await client.patch(f"/admin/tokens/{t['id']}/branding", cookies=admin_session, json={"colors": None})
    assert r.json()["branding"]["colors"] is None


async def test_logo_upload_replace_delete(client, admin_session):
    t = await _make()
    r = await _upload(client, admin_session, t["id"], png(64, 48))
    assert r.status_code == 200, r.text
    first = r.json()["branding"]["logo"]
    assert (first["width"], first["height"]) == (64, 48)

    r = await _upload(client, admin_session, t["id"], webp_lossless(100, 100), "x.webp", "image/webp")
    second = r.json()["branding"]["logo"]
    assert second["version"] != first["version"]
    assert (await db.get_token_logo(t["id"]))["content_type"] == "image/webp"

    assert (await client.delete(f"/admin/tokens/{t['id']}/logo", cookies=admin_session)).status_code == 200
    assert await db.get_token_logo(t["id"]) is None
    got = (await client.get(f"/admin/tokens/{t['id']}", cookies=admin_session)).json()
    assert got["branding"]["logo"] is None


async def test_upload_ignores_name_and_declared_type(client, admin_session):
    t = await _make()
    assert (await _upload(client, admin_session, t["id"], b"<svg/>", "logo.png", "image/png")).status_code == 422
    # A real PNG is accepted whatever it is called.
    assert (await _upload(client, admin_session, t["id"], png(), "logo.txt", "text/plain")).status_code == 200


async def test_oversize_upload_is_413(client, admin_session):
    t = await _make()
    big = png(extra=_chunk(b"tEXt", b"x" * branding.LOGO_MAX_BYTES))
    assert (await _upload(client, admin_session, t["id"], big)).status_code == 413


async def test_revoked_link_refuses_branding_edits(client, admin_session):
    t = await _make()
    await db.revoke_token(t["id"])
    assert (await client.patch(f"/admin/tokens/{t['id']}/branding", json={"title": "x"}, cookies=admin_session)).status_code == 400
    assert (await _upload(client, admin_session, t["id"], png())).status_code == 400


async def test_delete_token_removes_logo(client, admin_session):
    t = await _make()
    await _upload(client, admin_session, t["id"], png())
    await db.delete_token(t["id"])
    assert await db.get_token_logo(t["id"]) is None


async def test_create_copies_logo_from_source(client, admin_session):
    src = await _make("src")
    await _upload(client, admin_session, src["id"], png(80, 80))
    resp = await client.post("/admin/tokens", cookies=admin_session, json={
        "label": "Copy", "entity_ids": ["light.living_room"], "expires_in_seconds": 3600,
        "branding": {"title": "Copy"}, "copy_logo_from": src["id"],
    })
    assert resp.status_code in (200, 201), resp.text
    new = resp.json()
    assert new["branding"]["logo"]["width"] == 80
    assert (await db.get_token_logo(new["id"]))["data"] == (await db.get_token_logo(src["id"]))["data"]


async def test_create_with_unknown_copy_source_fails_cleanly(client, admin_session):
    resp = await client.post("/admin/tokens", cookies=admin_session, json={
        "label": "Copy", "entity_ids": ["light.living_room"], "expires_in_seconds": 3600,
        "copy_logo_from": "nope",
    })
    assert resp.status_code == 422
    assert all(t["label"] != "Copy" for t in await db.list_tokens())


# ---------------------------------------------------------------------------
# Guest pages
# ---------------------------------------------------------------------------

async def _branded(slug="b", logo=True, **extra):
    t = await _make(slug, **extra)
    await db.set_token_branding(t["id"], branding.stored({
        "eyebrow": "Be-Nith <b>Cottage</b>", "title": "Welcome", "logo_height": 48,
        "colors": {"brand_primary": "#0A7A5A", "brand_bg": "#F4FBF8"},
    }))
    if logo:
        await db.set_token_logo(t["id"], "image/png", png(64, 32), 64, 32)
    return t


async def test_guest_page_shows_branding_escaped(client, mock_ha_client):
    await _branded("b1")
    html = (await client.get("/g/b1")).text
    assert "Be-Nith &lt;b&gt;Cottage&lt;/b&gt;" in html
    assert "<b>Cottage</b>" not in html
    assert ">Welcome<" in html
    assert "/g/b1/logo?v=" in html
    assert "#0A7A5A".lower() in html.lower() or "10 122 90" in html


async def test_guest_page_without_branding_has_no_header(client, mock_ha_client):
    await _make("plain")
    html = (await client.get("/g/plain")).text
    assert "/logo?v=" not in html


async def test_logo_route_serves_bytes_with_safe_headers(client, mock_ha_client):
    await _branded("b2")
    r = await client.get("/g/b2/logo")
    assert r.status_code == 200
    assert r.content == png(64, 32)
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "immutable" in r.headers["cache-control"]


async def test_logo_route_404s(client, mock_ha_client):
    await _branded("none", logo=False)
    assert (await client.get("/g/none/logo")).status_code == 404
    assert (await client.get("/g/unknown/logo")).status_code == 404


async def test_logo_route_follows_ip_gate(client, mock_ha_client):
    await _branded("gated", ip_allowlist=["203.0.113.0/24"])
    assert (await client.get("/g/gated/logo")).status_code == 404


async def test_pin_page_is_branded_and_logo_still_loads(client, mock_ha_client):
    from app import guest_pin
    await _branded("pinned", pin_hash=await guest_pin.hash_pin("4821"))
    r = await client.get("/g/pinned")
    assert "Welcome" in r.text and "/g/pinned/logo?v=" in r.text
    assert (await client.get("/g/pinned/logo")).status_code == 200


async def test_expired_and_revoked_pages_are_branded(client, mock_ha_client):
    t = await _branded("dead")
    await db.revoke_token(t["id"])
    r = await client.get("/g/dead")
    assert "Welcome" in r.text
    assert (await client.get("/g/dead/logo")).status_code == 200


async def test_device_claim_page_stays_unbranded(client, mock_ha_client):
    await _branded("claim", device_binding=True)
    r = await client.get("/g/claim")
    assert "Welcome" not in r.text and "Be-Nith" not in r.text and "/logo?v=" not in r.text


async def test_manifest_uses_link_identity_only_when_live(client, mock_ha_client):
    t = await _branded("m1")
    m = (await client.get("/g/m1/manifest.json")).json()
    assert m["name"] == "Welcome"
    assert m["theme_color"] == "#0A7A5A" and m["background_color"] == "#F4FBF8"
    await db.revoke_token(t["id"])
    m = (await client.get("/g/m1/manifest.json")).json()
    assert m["name"] == settings.app_name
    unknown = (await client.get("/g/nope/manifest.json")).json()
    assert unknown["name"] == settings.app_name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@pytest.fixture
def api_on():
    with patch.object(settings, "api_enabled", True), patch.object(settings, "api_token", KEY):
        yield


async def test_api_exposes_branding_without_the_image(client, api_on):
    t = await _branded("api1")
    got = (await client.get(f"/api/v1/tokens/{t['id']}", headers=HEADERS)).json()
    assert got["branding"]["title"] == "Welcome"
    assert got["branding"]["logo"]["width"] == 64
    assert "data" not in got["branding"]["logo"]


async def test_api_patch_branding_and_duplicate_copies_logo(client, api_on):
    t = await _branded("api2")
    r = await client.patch(f"/api/v1/tokens/{t['id']}", headers=HEADERS, json={"branding": {"title": "Hello"}})
    assert r.status_code == 200, r.text
    assert r.json()["branding"]["title"] == "Hello"
    assert r.json()["branding"]["eyebrow"] == "Be-Nith <b>Cottage</b>"
    dup = await client.post(f"/api/v1/tokens/{t['id']}/duplicate", headers=HEADERS, json={})
    assert dup.status_code in (200, 201), dup.text
    assert dup.json()["branding"]["logo"]["width"] == 64


# ---------------------------------------------------------------------------
# Per-link palette
# ---------------------------------------------------------------------------

def test_link_palette_is_derived_like_the_global_one():
    colors = branding.clean_colors({"brand_primary": "#0A7A5A", "brand_bg": "#F4FBF8"})
    css, dark = branding.brand_theme(branding.brand_from_colors(colors))
    assert "--color-primary" in css and "--color-bg-dark" in css
    assert dark.startswith("#")


async def test_blocked_visitor_learns_nothing_of_the_branding(client, mock_ha_client):
    await _branded("hidden", ip_allowlist=["203.0.113.0/24"])
    r = await client.get("/g/hidden")
    assert "Welcome" not in r.text and "Be-Nith" not in r.text and "/logo?v=" not in r.text
